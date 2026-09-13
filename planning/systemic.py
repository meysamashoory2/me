"""Create a draft weekly plan automatically from the offline auto-planner.

Turns ``planning.auto_planner`` proposals (history-learned machine/cavities/
cycle + master-data molds, capacity- and due-date-aware) into a real
``WeeklyPlan`` with items and lines. The plan is a DRAFT that the planner then
edits by hand; every decision and every skipped item is recorded in the plan's
alarms so the reasoning stays visible.
"""

from __future__ import annotations

from django.db import transaction

from catalog.models import Machine, MoldOption, Product
from production.models import FittingProduction

from . import auto_planner
from .models import WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine
from .uid import refresh_plan_uids
from .utils import mold_change_date_candidates


@transaction.atomic
def create_systemic_plan(
    *,
    program_number: str,
    plan_date,
    user,
    friday_machine_ids: set[int] | None = None,
) -> tuple[WeeklyPlan, list[str]]:
    """Create a draft weekly plan populated from auto-planner proposals."""
    proposals = auto_planner.build_auto_proposals(friday_machine_ids=friday_machine_ids)

    alarms: list[str] = []
    if not proposals:
        alarms.append(
            "هیچ قلم قابل برنامه‌ریزی یافت نشد. ابتدا سفارشات و موجودی را از اکسل منتقل کنید."
        )

    plan = WeeklyPlan.objects.create(
        program_number=program_number,
        date=plan_date,
        status=WeeklyPlan.Status.DRAFT,
        planning_mode=WeeklyPlan.PlanningMode.SYSTEMIC,
        created_by=user,
    )

    # Default mold-change: first شنبه on/after the plan date.
    candidates = mold_change_date_candidates(plan_date, 0) or []
    change_date = candidates[0] if candidates else plan_date
    weekday = change_date.weekday() if hasattr(change_date, "weekday") else 0

    # Bulk-load referenced objects.
    codes = [p.product_code for p in proposals]
    products = {p.code: p for p in Product.objects.filter(code__in=codes).select_related("subgroup")}
    machine_ids = {p.machine_id for p in proposals if p.machine_id}
    machines = {m.pk: m for m in Machine.objects.filter(pk__in=machine_ids).select_related("unit")}
    mold_ids = {p.mold_id for p in proposals if p.mold_id}
    molds = {m.pk: m for m in MoldOption.objects.filter(pk__in=mold_ids)}
    machines_with_history = set(
        FittingProduction.objects.filter(machine_id__in=machine_ids)
        .values_list("machine_id", flat=True)
        .distinct()
    )

    seq_by_machine: dict[int, int] = {}
    created = 0
    for prop in proposals:
        # Skipped/deferred items: surface why, then move on.
        if prop.produce_qty <= 0 or not prop.machine_id:
            for w in prop.warnings:
                alarms.append(f"{prop.product_code}: {w}")
            continue

        product = products.get(prop.product_code)
        machine = machines.get(prop.machine_id)
        if product is None or machine is None:
            alarms.append(f"{prop.product_code}: محصول یا دستگاه یافت نشد — رد شد.")
            continue
        mold = molds.get(prop.mold_id) if prop.mold_id else None

        seq_by_machine[machine.pk] = seq_by_machine.get(machine.pk, 0) + 1
        cavities = max(int(prop.cavities or 1), 1)
        item = WeeklyPlanItem.objects.create(
            plan=plan,
            subgroup=product.subgroup,
            unit=machine.unit,
            machine=machine,
            product=product,
            mold=mold,
            mold_change_weekday=weekday,
            mold_change_date=change_date,
            active_cavities=cavities,
            sequence=seq_by_machine[machine.pk],
            history_alarm=machine.pk not in machines_with_history,
        )
        WeeklyPlanLine.objects.create(
            item=item,
            production_type=None,
            mold=mold,
            quantity=prop.produce_qty,
            cycle=int(prop.cycle_seconds or 0),
            active_cavities=cavities,
        )
        created += 1

        explain = (
            f"{prop.product_code}: دستگاه {machine} / قالب {mold.label if mold else '—'}"
            f" × {prop.produce_qty}"
        )
        if prop.reasons:
            explain += " — " + "؛ ".join(prop.reasons[:2])
        alarms.append(explain)
        for w in prop.warnings:
            alarms.append(f"{prop.product_code}: {w}")

    refresh_plan_uids(plan)
    if created == 0 and len(alarms) <= 1:
        alarms.append("برنامه سیستمی خالی ایجاد شد.")
    plan.alarms = "\n".join(alarms)
    plan.save(update_fields=["alarms"])
    return plan, alarms
