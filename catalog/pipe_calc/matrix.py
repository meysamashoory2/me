"""Depot and production matrix row math."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .accessories import calc_accessories, split_material_kg
from .engine import (
    CalcItemInput,
    billing_shots,
    calc_production_time,
    days_1dp,
    format_duration,
    hours_1dp,
    shifts_1dp,
)


def _f(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


@dataclass(frozen=True)
class DepotMatrixRow:
    """One nominal-length row for the upper depot planning table."""

    length_code: str
    label: str
    depot_ceiling: int
    stock: int
    voucher: int
    remaining_after_voucher: int
    avg_monthly_sales: float
    months_remaining: float | None
    depot_remaining_pct: float | None
    deduct_from_depot_stock: int
    deduct_from_depot_remaining: int
    required_qty: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calc_depot_matrix_row(
    *,
    length_code: str,
    label: str,
    depot_ceiling: int,
    stock: int,
    voucher: int,
    avg_monthly_sales: float,
    required_qty: int | None = None,
) -> DepotMatrixRow:
    """Derive planning columns from ceiling / stock / voucher / avg sales."""
    ceiling = max(0, int(depot_ceiling or 0))
    stock_i = int(stock or 0)
    voucher_i = max(0, int(voucher or 0))
    remaining = stock_i - voucher_i
    avg = max(0.0, _f(avg_monthly_sales))
    months = round(remaining / avg, 2) if avg > 0 else None
    pct = round((remaining / ceiling) * 100.0, 2) if ceiling > 0 else None
    deduct_stock = max(0, ceiling - stock_i)
    deduct_remaining = max(0, ceiling - remaining)
    req = int(required_qty) if required_qty is not None else deduct_stock
    return DepotMatrixRow(
        length_code=length_code,
        label=label,
        depot_ceiling=ceiling,
        stock=stock_i,
        voucher=voucher_i,
        remaining_after_voucher=remaining,
        avg_monthly_sales=avg,
        months_remaining=months,
        depot_remaining_pct=pct,
        deduct_from_depot_stock=deduct_stock,
        deduct_from_depot_remaining=deduct_remaining,
        required_qty=max(0, req),
    )


@dataclass(frozen=True)
class ProductionMatrixRow:
    """Lower computational table row after «محاسبه»."""

    length_code: str
    label: str
    qty: int
    line_seconds: float
    billing_seconds: float
    billing_shots: float
    socket_caps: float
    socket_cap_bags: float
    pipe_caps: float
    pipe_cap_bags: float
    orings: float
    oring_bags: float
    spacers: float
    spacer_bags: float
    cover_kg: float
    cover_rolls: float
    materials: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["line_fmt"] = format_duration(self.line_seconds)
        data["billing_fmt"] = format_duration(self.billing_seconds)
        data["line_hours"] = hours_1dp(self.line_seconds)
        data["line_days"] = days_1dp(self.line_seconds)
        data["line_shifts"] = shifts_1dp(self.line_seconds)
        data["billing_hours"] = hours_1dp(self.billing_seconds)
        data["billing_days"] = days_1dp(self.billing_seconds)
        data["billing_shifts"] = shifts_1dp(self.billing_seconds)
        return data


def calc_production_matrix_row(
    *,
    length_code: str,
    label: str,
    qty: int,
    cut_length_mm: float,
    line_speed_m_per_min: float,
    billing_pieces_per_hour: float = 0.0,
    socket_ends: int = 0,
    needs_billing: bool = True,
    layers: Sequence[dict[str, Any]] | None = None,
    size_mm: int = 0,
    pack_qty: int = 0,
    spacers_per_pack: int = 0,
    pipe_cap_per_piece: float = 0.0,
    cover_cm: float = 0.0,
    cover_g_per_m: float = 0.0,
    billing_cycle_s: float = 0.0,
    billing_cavities: float = 1.0,
    socket_cap_bag: int = 0,
    pipe_cap_bag: int = 0,
    spacer_bag: int = 0,
    oring_bag: int = 0,
    kg_per_meter: float = 0.0,
    mix: Sequence[dict[str, Any]] | None = None,
    middle_share: float = 100.0,
    skin_share: float = 0.0,
    socket_cap_per_socket: float = 1.0,
    spacer_per_piece: float = 0.0,
    cover_per_piece: float = 0.0,
    material_factors: dict[str, float] | None = None,
) -> ProductionMatrixRow:
    """Time + factory accessories + material mix for one length × qty."""
    qty_i = max(0, int(qty or 0))
    ends = max(0, int(socket_ends or 0))
    time = calc_production_time(
        CalcItemInput(
            key=length_code,
            pieces=qty_i,
            cut_length_mm=cut_length_mm,
            line_speed_m_per_min=line_speed_m_per_min,
            billing_pieces_per_hour=billing_pieces_per_hour,
            socket_ends=ends,
            pack_qty=pack_qty,
            needs_billing=bool(needs_billing) and ends > 0,
            label=label,
            billing_cycle_s=billing_cycle_s,
            billing_cavities=billing_cavities,
        )
    )
    acc = calc_accessories(
        qty=qty_i,
        size_mm=size_mm,
        socket_ends=ends,
        pack_qty=pack_qty,
        spacers_per_pack=spacers_per_pack,
        pipe_cap_per_piece=pipe_cap_per_piece if pipe_cap_per_piece or ends == 0 else (1.0 if ends == 1 else 0.0),
        cover_cm=cover_cm,
        cover_g_per_m=cover_g_per_m,
        socket_cap_bag=socket_cap_bag,
        pipe_cap_bag=pipe_cap_bag,
        spacer_bag=spacer_bag,
        oring_bag=oring_bag,
    )
    if socket_cap_per_socket != 1.0:
        acc["socket_caps"] = round(qty_i * ends * _f(socket_cap_per_socket), 4)
    if spacer_per_piece and acc["spacers"] == 0:
        acc["spacers"] = round(qty_i * _f(spacer_per_piece), 4)
    if cover_per_piece and acc["cover_kg"] == 0:
        acc["cover_kg"] = round(qty_i * _f(cover_per_piece), 4)

    kg_m = _f(kg_per_meter)
    if kg_m <= 0 and layers:
        kg_m = sum(_f(layer.get("kg_per_meter") or 0) for layer in layers)
    materials = split_material_kg(
        time.meters,
        kg_m,
        list(mix or []),
        middle_share=middle_share,
        skin_share=skin_share,
    )
    factors = material_factors or {}
    if layers and not mix:
        materials = []
        for layer in layers:
            factor = _f(factors.get(str(layer.get("layer") or ""), 1.0))
            materials.append(
                {
                    "key": f"layer:{layer.get('layer')}",
                    "name": layer.get("material_name") or layer.get("material_code") or layer.get("layer"),
                    "kg_total": round(time.meters * _f(layer.get("kg_per_meter") or 0) * factor, 4),
                    "percent": _f(layer.get("share_percent") or 0),
                }
            )
    return ProductionMatrixRow(
        length_code=length_code,
        label=label,
        qty=qty_i,
        line_seconds=time.line.seconds,
        billing_seconds=time.billing.seconds if needs_billing else 0.0,
        billing_shots=billing_shots(qty_i, ends, billing_cavities),
        socket_caps=acc["socket_caps"],
        socket_cap_bags=acc["socket_cap_bags"],
        pipe_caps=acc["pipe_caps"],
        pipe_cap_bags=acc["pipe_cap_bags"],
        orings=acc["orings"],
        oring_bags=acc["oring_bags"],
        spacers=acc["spacers"],
        spacer_bags=acc["spacer_bags"],
        cover_kg=acc["cover_kg"],
        cover_rolls=acc["cover_rolls"],
        materials=tuple(materials),
    )


def resolve_qty_from_depot_row(row: DepotMatrixRow | dict[str, Any], source: str) -> int:
    """Map qty-source selector onto a depot matrix row."""
    if isinstance(row, DepotMatrixRow):
        data = row.to_dict()
    else:
        data = row
    if source == "deduct_remaining":
        return max(0, int(data.get("deduct_from_depot_remaining") or 0))
    if source == "required":
        return max(0, int(data.get("required_qty") or 0))
    # default: deduct_stock
    return max(0, int(data.get("deduct_from_depot_stock") or 0))
