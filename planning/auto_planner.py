"""Offline, deterministic auto-planner for weekly fitting production.

Design (agreed with the plant):
  * Machine, cavities and cycle for each product are LEARNED from production
    history (``FittingProduction``); no separate machine master data.
  * Eligible molds come from the master data (``ProductMold``); each mold has a
    number of physical ``copies`` and every copy can run on only one machine at
    a time (exclusivity).
  * Capacity is real: a per-machine weekly hour budget (default 2 shifts x 6
    days = 96h, optionally +Friday) with mold-change time reserved on changeover.
  * Demand is ordered by delivery date, then priority. Quantities are NOT split
    across machines: a product is placed on one machine and whatever does not
    fit its remaining capacity is left for a later plan.
  * Every proposal carries human-readable reasons so the plan is explainable.

This module only *computes* proposals; turning them into a WeeklyPlan lives in
``planning.systemic`` so this stays pure and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from catalog.models import Machine, Product
from production.models import FittingProduction

from .intelligence import demand_balance
from .models import CustomerOrder

HOURS_PER_SHIFT = 8.0
SHIFTS_PER_DAY = 2
BASE_WORK_DAYS = 6  # شنبه تا پنجشنبه
HOURS_PER_DAY = HOURS_PER_SHIFT * SHIFTS_PER_DAY  # 16
BASE_WEEK_HOURS = HOURS_PER_DAY * BASE_WORK_DAYS  # 96
FRIDAY_HOURS = HOURS_PER_DAY  # کار جمعه یک روز کامل اضافه می‌کند
DEFAULT_CYCLE_SECONDS = 30


@dataclass
class ProductProfile:
    """What history tells us about how a product is normally produced."""

    machine_ids: list[int] = field(default_factory=list)  # best first (recency+frequency)
    cavities: int = 1
    cycle_seconds: int = DEFAULT_CYCLE_SECONDS
    has_history: bool = False


@dataclass
class AutoProposal:
    product_code: str
    product_name: str
    net_need: int
    produce_qty: int
    machine_id: int | None
    machine_label: str
    mold_id: int | None
    mold_label: str
    cavities: int
    cycle_seconds: int
    est_hours: float
    delivery_date_iso: str = ""
    priority: int = 100
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def learn_product_profiles() -> dict[str, ProductProfile]:
    """Learn machine order, cavities and cycle per product code from history.

    Machines are ranked by recency (latest record wins) then frequency.
    """
    machine_rank: dict[str, dict[int, tuple[int, int]]] = {}
    cavities: dict[str, int] = {}
    cycle: dict[str, int] = {}

    qs = (
        FittingProduction.objects.select_related("product", "machine")
        .order_by("-date", "-id")
    )
    seen_latest: set[str] = set()
    for rec in qs.iterator():
        product = rec.product
        code = (getattr(product, "code", "") or "").strip()
        if not code or not rec.machine_id:
            continue
        buckets = machine_rank.setdefault(code, {})
        prev = buckets.get(rec.machine_id)
        # recency = negative index of first time we see this machine (0 = most recent)
        recency = prev[0] if prev else -len(buckets)
        count = (prev[1] if prev else 0) + 1
        buckets[rec.machine_id] = (recency, count)
        # Latest record for a code sets its representative cavities/cycle.
        if code not in seen_latest:
            seen_latest.add(code)
            if rec.active_cavities:
                cavities[code] = int(rec.active_cavities)
            if rec.shot_cycle:
                cycle[code] = int(rec.shot_cycle)

    profiles: dict[str, ProductProfile] = {}
    for code, buckets in machine_rank.items():
        ordered = sorted(buckets.items(), key=lambda kv: (kv[1][0], -kv[1][1]))
        profiles[code] = ProductProfile(
            machine_ids=[mid for mid, _ in ordered],
            cavities=cavities.get(code, 1),
            cycle_seconds=cycle.get(code, DEFAULT_CYCLE_SECONDS),
            has_history=True,
        )
    return profiles


def _earliest_due_by_code() -> dict[str, str]:
    out: dict[str, str] = {}
    qs = CustomerOrder.objects.filter(is_active=True, quantity__gt=0).exclude(
        delivery_date__isnull=True
    )
    for row in qs:
        code = (row.product_code or "").strip()
        if not code:
            continue
        iso = str(row.delivery_date)
        if code not in out or iso < out[code]:
            out[code] = iso
    return out


def _eligible_molds(product: Product):
    """Eligible molds for a product, ordered by slot (preferred first)."""
    links = (
        product.mold_links.filter(is_active=True, mold__is_active=True)
        .select_related("mold")
        .order_by("slot", "id")
    )
    return [ln.mold for ln in links]


def _weekly_hours(machine_id: int, friday_machine_ids: set[int]) -> float:
    return BASE_WEEK_HOURS + (FRIDAY_HOURS if machine_id in friday_machine_ids else 0.0)


def build_auto_proposals(*, friday_machine_ids: set[int] | None = None) -> list[AutoProposal]:
    friday_machine_ids = friday_machine_ids or set()
    profiles = learn_product_profiles()
    due_by_code = _earliest_due_by_code()

    # What to produce: net shortage vs demand, from the shared intelligence engine.
    needs = [row for row in demand_balance() if row.net_gap > 0]

    # Order strictly by delivery date (soonest first), then priority, then code.
    def order_key(row):
        due = due_by_code.get(row.product_code) or "9999-99-99"
        return (due, int(row.priority or 100), row.product_code)

    needs.sort(key=order_key)

    products = {
        p.code: p
        for p in Product.objects.filter(
            code__in=[r.product_code for r in needs]
        ).prefetch_related("mold_links__mold")
    }
    machines = {m.pk: m for m in Machine.objects.filter(machine_type="injection", is_active=True)}
    fallback_machines = sorted(machines.values(), key=lambda m: (getattr(m.unit, "number", 0) or 0, m.number))

    used_hours: dict[int, float] = {}
    mold_machines: dict[int, set[int]] = {}  # mold_id -> machines currently running a copy
    machine_last_mold: dict[int, int] = {}

    def remaining(machine_id: int) -> float:
        return _weekly_hours(machine_id, friday_machine_ids) - used_hours.get(machine_id, 0.0)

    proposals: list[AutoProposal] = []

    for row in needs:
        code = row.product_code
        product = products.get(code)
        prof = profiles.get(code) or ProductProfile()
        # Prefer history; otherwise fall back to the product's catalog values.
        if prof.has_history:
            cavities = max(int(prof.cavities or 1), 1)
            cycle = int(prof.cycle_seconds or DEFAULT_CYCLE_SECONDS)
        else:
            cavities = max(int(getattr(product, "main_cavities", None) or 1), 1)
            cycle = int(getattr(product, "last_cycle", None) or DEFAULT_CYCLE_SECONDS)
        reasons: list[str] = []
        warnings: list[str] = []

        # ---- depot-ceiling cap: never produce above remaining depot room ----
        desired = row.net_gap
        if row.depot_ceiling is not None:
            room = max(int(row.depot_ceiling) - int(row.stock), 0)
            if desired > room:
                warnings.append(
                    f"سقف دپو {row.depot_ceiling}: نیاز {row.net_gap} → {room} محدود شد."
                )
                desired = room
        if desired <= 0:
            continue

        # ---- mold selection (from master data) ----
        molds = _eligible_molds(product) if product is not None else []
        if not molds:
            warnings.append("قالبی برای این محصول تعریف نشده — با قالب نامشخص برنامه‌ریزی شد.")
            molds = [None]  # sentinel: plan without a mold (no exclusivity check)

        # ---- machine candidates (history first, then fallback) ----
        candidate_ids = [mid for mid in prof.machine_ids if mid in machines]
        if candidate_ids:
            reasons.append("دستگاه از سابقهٔ تولید همین محصول انتخاب شد.")
        else:
            candidate_ids = [m.pk for m in fallback_machines]
            if candidate_ids:
                warnings.append("سابقهٔ تولید یافت نشد؛ دستگاه بر اساس کمترین بار انتخاب شد.")

        # Choose the (machine, mold) pair honouring mold-copy exclusivity + capacity.
        chosen_machine = None
        chosen_mold = None  # MoldOption or None (mold-less)
        for mid in sorted(candidate_ids, key=lambda i: (used_hours.get(i, 0.0), i)):
            if remaining(mid) <= 0:
                continue
            for mold in molds:
                if mold is None:
                    chosen_machine, chosen_mold = mid, None
                    break
                running = mold_machines.setdefault(mold.pk, set())
                free_copy = mid in running or len(running) < max(int(mold.copies or 1), 1)
                if free_copy:
                    chosen_machine, chosen_mold = mid, mold
                    break
            if chosen_machine is not None:
                break

        def _deferred(reason_text):
            proposals.append(AutoProposal(
                product_code=code, product_name=row.product_name, net_need=row.net_gap,
                produce_qty=0, machine_id=None, machine_label="", mold_id=None, mold_label="",
                cavities=cavities, cycle_seconds=cycle, est_hours=0.0,
                delivery_date_iso=due_by_code.get(code, ""), priority=int(row.priority or 100),
                reasons=reasons, warnings=warnings + [reason_text],
            ))

        if chosen_machine is None:
            # Either every candidate is full, or all mold copies are busy elsewhere.
            _deferred("ظرفیت دستگاه یا نسخهٔ قالب آزاد نبود؛ به دورهٔ بعد موکول شد.")
            continue

        machine = machines[chosen_machine]
        # Mold-change reservation when this machine switches to a different mold.
        change_hours = 0.0
        if chosen_mold is not None and machine_last_mold.get(chosen_machine) not in (None, chosen_mold.pk):
            change_hours = float(chosen_mold.change_time_hours or 0)
            reasons.append(f"زمان تعویض قالب رزرو شد ({change_hours:g} ساعت).")

        avail = remaining(chosen_machine) - change_hours
        full_hours = (desired / cavities) * cycle / 3600.0
        produce = desired
        if full_hours > avail:
            # No splitting: cap to what fits on this one machine; leave the rest.
            produce = max(int((avail * 3600.0 * cavities) / cycle), 0)
            if produce < desired:
                warnings.append(
                    f"ظرفیت هفته محدود کرد: {desired} → {produce}؛ باقی‌مانده به دورهٔ بعد موکول شد."
                )
        if produce <= 0:
            _deferred("ظرفیت باقیماندهٔ دستگاه صفر بود؛ به دورهٔ بعد موکول شد.")
            continue

        est_hours = round((produce / cavities) * cycle / 3600.0 + change_hours, 2)
        used_hours[chosen_machine] = used_hours.get(chosen_machine, 0.0) + est_hours
        if chosen_mold is not None:
            mold_machines[chosen_mold.pk].add(chosen_machine)
            machine_last_mold[chosen_machine] = chosen_mold.pk
            reasons.append(f"قالب «{chosen_mold.label}» از قالب‌های مجاز محصول انتخاب شد.")

        proposals.append(AutoProposal(
            product_code=code, product_name=row.product_name, net_need=row.net_gap,
            produce_qty=produce, machine_id=chosen_machine, machine_label=str(machine),
            mold_id=(chosen_mold.pk if chosen_mold is not None else None),
            mold_label=(chosen_mold.label if chosen_mold is not None else ""),
            cavities=cavities, cycle_seconds=cycle, est_hours=est_hours,
            delivery_date_iso=due_by_code.get(code, ""), priority=int(row.priority or 100),
            reasons=reasons, warnings=warnings,
        ))

    return proposals
