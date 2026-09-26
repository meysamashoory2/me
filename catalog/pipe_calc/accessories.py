"""Accessory and packing math for pipe production matrix rows."""

from __future__ import annotations

import math
from typing import Any

from .constants import COVER_ROLL_KG, NO_SPACER_SIZE_MM, ORING_BAG_QTY


def _f(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def ceil_packs(qty: int, pack_qty: int) -> int:
    qty_i = max(0, int(qty or 0))
    pack = max(0, int(pack_qty or 0))
    if qty_i <= 0 or pack <= 0:
        return 0
    return int(math.ceil(qty_i / pack))


def calc_accessories(
    *,
    qty: int,
    size_mm: int,
    socket_ends: int,
    pack_qty: int,
    spacers_per_pack: int,
    pipe_cap_per_piece: float,
    cover_cm: float,
    cover_g_per_m: float,
    socket_cap_bag: int,
    pipe_cap_bag: int,
    spacer_bag: int,
    oring_bag: int | None = None,
) -> dict[str, float]:
    """Return piece/bag/roll quantities. Bags and rolls stay decimal; packs ceil."""
    qty_i = max(0, int(qty or 0))
    ends = max(0, int(socket_ends or 0))
    sockets = qty_i * ends
    orings = sockets
    socket_caps = sockets
    pipe_caps = qty_i * _f(pipe_cap_per_piece)
    packs = ceil_packs(qty_i, pack_qty)
    spacers = 0.0
    if int(size_mm or 0) != NO_SPACER_SIZE_MM:
        spacers = float(packs * max(0, int(spacers_per_pack or 0)))
    cover_cm_total = packs * _f(cover_cm)
    cover_m = cover_cm_total / 100.0
    cover_g = cover_m * _f(cover_g_per_m)
    cover_kg = cover_g / 1000.0
    cover_rolls = cover_kg / COVER_ROLL_KG if COVER_ROLL_KG else 0.0

    def bags(pieces: float, per_bag: int) -> float:
        if pieces <= 0 or per_bag <= 0:
            return 0.0
        return pieces / per_bag

    bag_oring = int(oring_bag or 0) or int(ORING_BAG_QTY.get(int(size_mm or 0), 0))
    return {
        "packs": float(packs),
        "sockets": float(sockets),
        "orings": float(orings),
        "oring_bags": bags(orings, bag_oring),
        "socket_caps": float(socket_caps),
        "socket_cap_bags": bags(socket_caps, int(socket_cap_bag or 0)),
        "pipe_caps": float(pipe_caps),
        "pipe_cap_bags": bags(pipe_caps, int(pipe_cap_bag or 0)),
        "spacers": spacers,
        "spacer_bags": bags(spacers, int(spacer_bag or 0)),
        "cover_cm": cover_cm_total,
        "cover_kg": round(cover_kg, 4),
        "cover_rolls": cover_rolls,
    }


def split_material_kg(
    meters: float,
    kg_per_meter: float,
    mix: list[dict[str, Any]] | None = None,
    *,
    middle_share: float = 100.0,
    skin_share: float = 0.0,
) -> list[dict[str, Any]]:
    """Expand kg/m × meters using factory mix percents."""
    total = max(0.0, _f(meters)) * max(0.0, _f(kg_per_meter))
    rows: list[dict[str, Any]] = []
    items = list(mix or [])
    if not items:
        rows.append(
            {
                "key": "pipe_kg",
                "name": "مواد لوله",
                "kg_total": round(total, 4),
                "percent": 100.0,
            }
        )
        return rows
    middle = _f(middle_share)
    skin = _f(skin_share)
    if 0 < middle <= 1.0:
        middle *= 100.0
    if 0 < skin <= 1.0:
        skin *= 100.0
    if middle <= 0:
        middle = 100.0
    for item in items:
        pct = _f(item.get("percent"))
        name = str(item.get("name") or item.get("code") or "")
        bucket = str(item.get("bucket") or "all")
        if bucket == "middle":
            kg = total * (middle / 100.0) * (pct / 100.0)
        elif bucket == "skin":
            kg = total * (skin / 100.0) * (pct / 100.0)
        else:
            kg = total * (pct / 100.0)
        rows.append(
            {
                "key": str(item.get("code") or name),
                "name": name,
                "kg_total": round(kg, 4),
                "percent": pct,
                "bucket": bucket,
            }
        )
    return rows
