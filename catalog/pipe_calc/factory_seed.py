"""Apply factory CSV master data onto pipe-calc models."""

from __future__ import annotations

from decimal import Decimal

from .constants import DEFAULT_DEPOT_CEILING, LAYER_INNER, LAYER_MIDDLE, LAYER_OUTER, LAYER_SINGLE, LENGTH_COUPLER, ORING_BAG_QTY
from .factory_csv import FactoryLineData, FactorySize, load_factory_line
from .models import PipeLayerSpec, PipeLengthCut, PipeProductLine, PipeSizeProfile


def _dec(value: float | int | Decimal) -> Decimal:
    return Decimal(str(value))


def _cut_mm(cut_m: float) -> int:
    return max(0, int(round(float(cut_m) * 1000)))


def _apply_layers(profile: PipeSizeProfile, rec: FactorySize, layer_mode: str) -> None:
    kg = float(rec.kg_per_meter or 0)
    if layer_mode == PipeProductLine.LayerMode.TRIPLE:
        middle = float(rec.middle_share or 0) or 100.0
        skin = float(rec.skin_share or 0)
        if middle + skin <= 0:
            middle, skin = 100.0, 0.0
        shares = (
            (LAYER_INNER, "درونی", skin / 2.0, 0),
            (LAYER_MIDDLE, "میانی", middle, 1),
            (LAYER_OUTER, "بیرونی", skin / 2.0, 2),
        )
        for layer, fa_name, share, order in shares:
            PipeLayerSpec.objects.update_or_create(
                size_profile=profile,
                layer=layer,
                defaults={
                    "material_code": f"{profile.line.code.upper()}-{layer[:3].upper()}-{profile.size_mm}",
                    "material_name": f"مواد لایه {fa_name} Ø{profile.size_mm}",
                    "kg_per_meter": _dec(round(kg * share / 100.0, 5)),
                    "share_percent": _dec(round(share, 2)),
                    "order": order,
                },
            )
        return
    PipeLayerSpec.objects.update_or_create(
        size_profile=profile,
        layer=LAYER_SINGLE,
        defaults={
            "material_code": f"PVC-{profile.size_mm}",
            "material_name": f"مواد تک‌لایه Ø{profile.size_mm}",
            "kg_per_meter": _dec(kg),
            "share_percent": _dec(100),
            "order": 0,
        },
    )


def apply_factory_line(line: PipeProductLine, data: FactoryLineData) -> int:
    """Upsert factory rates. Stock, voucher, and monthly sales stay as-is.

    Depot ceiling is loaded from the factory file as the default, then remains
    editable in تعاریف اولیه because it can change in some periods.
    """
    touched = 0
    skus_by_size: dict[int, list] = {}
    for sku in data.skus:
        skus_by_size.setdefault(sku.size_mm, []).append(sku)

    for size_mm, rec in data.sizes.items():
        existing = PipeSizeProfile.objects.filter(line=line, size_mm=size_mm).first()
        extras = dict(existing.extras or {}) if existing else {}
        extras["mix"] = rec.mix
        extras["middle_share"] = rec.middle_share
        extras["skin_share"] = rec.skin_share
        profile, _ = PipeSizeProfile.objects.update_or_create(
            line=line,
            size_mm=size_mm,
            defaults={
                "line_speed_m_per_min": _dec(0),
                "billing_cycle_seconds": _dec(rec.billing_cycle_s),
                "billing_cavities": _dec(rec.billing_cavities or 1),
                "socket_cap_bag_qty": rec.socket_cap_bag,
                "pipe_cap_bag_qty": rec.pipe_cap_bag,
                "spacer_bag_qty": rec.spacer_bag,
                "oring_bag_qty": ORING_BAG_QTY.get(size_mm, 0),
                "cover_g_per_m": _dec(rec.cover_g_per_m),
                "kg_per_meter": _dec(rec.kg_per_meter),
                "pack_qty": (skus_by_size.get(size_mm) or [None])[0].pack_qty if skus_by_size.get(size_mm) else 0,
                "is_active": True,
                "extras": extras,
            },
        )
        size_ceilings = [sku.depot_ceiling for sku in skus_by_size.get(size_mm, []) if sku.depot_ceiling]
        if size_ceilings:
            profile.depot_ceiling = max(size_ceilings)
            profile.save(update_fields=["depot_ceiling"])
        elif not profile.depot_ceiling:
            profile.depot_ceiling = DEFAULT_DEPOT_CEILING.get(size_mm, 0)
            profile.save(update_fields=["depot_ceiling"])
        _apply_layers(profile, rec, line.layer_mode)
        for sku in skus_by_size.get(size_mm, []):
            label = sku.label or sku.length_code
            defaults = {
                "label": label,
                "nominal_cm": _nominal_cm(sku.length_code),
                "cut_length_mm": _cut_mm(sku.cut_length_m),
                "socket_ends": sku.socket_ends,
                "sku_code": sku.sku_code,
                "cover_cm": _dec(sku.cover_cm),
                "pack_qty": sku.pack_qty,
                "spacers_per_pack": sku.spacers_per_pack,
                "pipe_cap_per_piece": _dec(sku.pipe_cap_per_piece),
                "line_speed_m_per_min": _dec(sku.line_speed_m_per_min),
                "depot_ceiling": int(sku.depot_ceiling or 0),
                "is_active": True,
            }
            PipeLengthCut.objects.update_or_create(
                size_profile=profile,
                length_code=sku.length_code,
                defaults=defaults,
            )
        _ensure_coupler(profile)
        touched += 1
    return touched


def _nominal_cm(length_code: str) -> int:
    if length_code.startswith("30cm"):
        return 30
    if length_code.startswith("50cm"):
        return 50
    if length_code.startswith("100cm"):
        return 100
    if length_code.startswith("200cm"):
        return 200
    if length_code.startswith("300cm"):
        return 300
    return 0


def _ensure_coupler(profile: PipeSizeProfile) -> None:
    ceiling = int(profile.depot_ceiling or DEFAULT_DEPOT_CEILING.get(profile.size_mm, 0))
    existing = PipeLengthCut.objects.filter(size_profile=profile, length_code=LENGTH_COUPLER).first()
    defaults = {
        "label": "رابط",
        "nominal_cm": 0,
        "cut_length_mm": existing.cut_length_mm if existing else 0,
        "socket_ends": 0,
        "is_active": True,
    }
    if existing is None:
        defaults["depot_ceiling"] = max(100, ceiling // 10)
    PipeLengthCut.objects.update_or_create(
        size_profile=profile,
        length_code=LENGTH_COUPLER,
        defaults=defaults,
    )


def apply_all_factory_csvs() -> int:
    total = 0
    for code in ("protect", "general", "silent"):
        line = PipeProductLine.objects.filter(code=code).first()
        if line is None:
            continue
        total += apply_factory_line(line, load_factory_line(code))
    return total
