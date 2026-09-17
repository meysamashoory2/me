"""Parse factory master CSVs for Protect / General / Silent-10."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

_SIZE_RE = re.compile(r"سایز\s*(\d+)")
_SKU_RE = re.compile(r"^\d{7,12}$")
_WS_RE = re.compile(r"\s+")


def _norm(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ")
    return _WS_RE.sub(" ", text).strip()


def _mix_display_name(label: str) -> str:
    text = _norm(label)
    text = re.sub(r"درصد وزنی لایه[^\s]*", "", text)
    text = re.sub(r"درصد ترکیب در لوله", "", text)
    text = re.sub(r"درصد ترکیب", "", text)
    text = re.sub(r"لایه میانی", "", text)
    text = re.sub(r"لایه درونی و بیرنی", "", text)
    text = re.sub(r"لایه درونی و بیرونی", "", text)
    text = _norm(text)
    text = re.sub(r"^میانی\s+", "", text)
    text = re.sub(r"^درونی و بیرنی\s+", "", text)
    text = re.sub(r"^درونی و بیرونی\s+", "", text)
    return _norm(text)


def _num(value: Any, default: float = 0.0) -> float:
    text = _norm(value).replace(",", "").replace("٪", "")
    if text in {"", "-", "—"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _int(value: Any, default: int = 0) -> int:
    return int(round(_num(value, default)))


def length_code_from_symbol(symbol: str, sockets: int) -> str:
    """Map 110×200 D / 40×30 E onto shared length_code keys."""
    text = _norm(symbol).replace("x", "×").replace("X", "×")
    if sockets <= 0:
        return "coupler"
    match = re.search(r"×\s*(\d+)", text)
    if not match:
        return ""
    raw = int(match.group(1))
    suffix = "2s" if sockets >= 2 else "1s"
    if raw == 30:
        return f"30cm_{suffix}"
    if raw == 50:
        return f"50cm_{suffix}"
    if raw in {100, 1}:
        return f"100cm_{suffix}"
    if raw in {200, 2}:
        return f"200cm_{suffix}"
    if raw in {300, 3}:
        return f"300cm_{suffix}"
    return f"{raw}cm_{suffix}"


def _header_blob(rows: list[list[str]], index: int) -> list[str]:
    current = rows[index]
    prev = rows[index - 1] if index > 0 else []
    nxt = rows[index + 1] if index + 1 < len(rows) else []
    nxt_first = _norm(nxt[0] if nxt else "")
    merge_next = bool(nxt) and not _SIZE_RE.search(nxt_first) and not _SKU_RE.match(nxt_first)
    prev_blob = " ".join(_norm(c) for c in prev)
    merge_prev = bool(prev) and "درصد" in prev_blob
    width = max(len(current), len(prev) if merge_prev else 0, len(nxt) if merge_next else 0)
    out: list[str] = []
    for col in range(width):
        a = _norm(current[col] if col < len(current) else "")
        p = _norm(prev[col] if merge_prev and col < len(prev) else "")
        b = _norm(nxt[col] if merge_next and col < len(nxt) else "")
        seen: list[str] = []
        for part in (p, a, b):
            if part and part not in seen:
                seen.append(part)
        out.append(" ".join(seen))
    return out


def _classify_size_col(label: str) -> str | None:
    t = _norm(label)
    if "درپوش سوکت" in t:
        return "socket_cap_bag"
    if "درپوش لوله" in t:
        return "pipe_cap_bag"
    if "اسپیسر" in t:
        return "spacer_bag"
    if "سیکل" in t:
        return "billing_cycle_s"
    if "حفره" in t:
        return "billing_cavities"
    if "وزن یک متر لوله" in t or "وزن یک متر لوله" in t:
        return "kg_per_meter"
    if "وزن یک متر کاور" in t or "کاور به گرم" in t:
        return "cover_g_per_m"
    if "درصد وزنی" in t and ("میانی" in t or "ميانی" in t):
        return "mix_middle"
    if "درصد وزنی" in t:
        return "mix_skin"
    if "درصد ترکیب" in t and ("درونی" in t or "بیرونی" in t or "بیرنی" in t):
        return "skin_share"
    if "درصد ترکیب" in t and "میانی" in t:
        return "middle_share"
    if ("لایه میانی" in t or "لایه ميانی" in t) and "درصد وزنی" not in t:
        return "middle_share"
    if ("لایه درونی" in t or "بیرونی" in t or "بیرنی" in t) and "مواد" not in t:
        return "skin_share"
    if t in {"", "سایز"} or t.startswith("سایز"):
        return None
    if t:
        return "mix"
    return None


@dataclass
class FactorySku:
    sku_code: str
    size_mm: int
    length_code: str
    label: str
    cover_cm: float
    pack_qty: int
    spacers_per_pack: int
    socket_ends: int
    pipe_cap_per_piece: float
    cut_length_m: float
    line_speed_m_per_min: float
    depot_ceiling: int = 0
    alt_speed_m_per_min: float = 0.0


@dataclass
class FactorySize:
    size_mm: int
    socket_cap_bag: int = 0
    pipe_cap_bag: int = 0
    spacer_bag: int = 0
    billing_cycle_s: float = 0.0
    billing_cavities: float = 1.0
    kg_per_meter: float = 0.0
    cover_g_per_m: float = 0.0
    middle_share: float = 100.0
    skin_share: float = 0.0
    mix: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FactoryLineData:
    sizes: dict[int, FactorySize]
    skus: list[FactorySku]


def _as_share(value: float) -> float:
    if value <= 0:
        return 0.0
    if value <= 1.0:
        return round(value * 100.0, 4)
    return value


def parse_factory_csv(path: Path) -> FactoryLineData:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    sizes: dict[int, FactorySize] = {}
    skus: list[FactorySku] = []
    size_map: list[str | None] | None = None
    mix_names: dict[int, tuple[str, str]] = {}
    sku_header_idx: int | None = None

    for idx, row in enumerate(rows):
        first = _norm(row[0] if row else "")
        blob = " ".join(_norm(c) for c in row)
        if "کد کالا" in first or (
            "کد کالا" in blob and "متراژ" in blob and not first.startswith("سایز")
        ):
            sku_header_idx = idx
            break
        if "درپوش سوکت" in blob and size_map is None:
            headers = _header_blob(rows, idx)
            size_map = []
            mix_names = {}
            for col, label in enumerate(headers):
                kind = _classify_size_col(label)
                size_map.append(kind)
                if kind in {"mix", "mix_middle", "mix_skin"} and col > 0:
                    bucket = "all"
                    if kind == "mix_middle":
                        bucket = "middle"
                    elif kind == "mix_skin":
                        bucket = "skin"
                    mix_names[col] = (_mix_display_name(label) or _norm(label), bucket)
            continue
        match = _SIZE_RE.search(first)
        if match and size_map:
            size_mm = int(match.group(1))
            rec = FactorySize(size_mm=size_mm)
            mix: list[dict[str, Any]] = []
            for col, kind in enumerate(size_map):
                if kind is None or col >= len(row):
                    continue
                raw = row[col]
                if kind in {"mix", "mix_middle", "mix_skin"}:
                    name, bucket = mix_names.get(col) or (_norm(raw), "all")
                    if name:
                        mix.append({"name": name, "percent": _num(raw), "bucket": bucket})
                    continue
                value = _num(raw)
                if kind in {"middle_share", "skin_share"}:
                    value = _as_share(value)
                setattr(rec, kind, value if kind not in {"socket_cap_bag", "pipe_cap_bag", "spacer_bag"} else _int(raw))
            rec.mix = mix
            if rec.middle_share <= 0:
                rec.middle_share = 100.0
                rec.skin_share = 0.0
            sizes[size_mm] = rec

    if sku_header_idx is None:
        return FactoryLineData(sizes=sizes, skus=skus)

    sku_headers = [_norm(c) for c in rows[sku_header_idx]]

    def _sku_col(*needles: str) -> int | None:
        for idx, label in enumerate(sku_headers):
            if all(n in label for n in needles):
                return idx
        return None

    col_ceiling = _sku_col("سقف دپو")
    col_speed = _sku_col("سرعت تولید لوله")
    col_alt = _sku_col("فرق کند")

    for row in rows[sku_header_idx + 1 :]:
        code = _norm(row[0] if row else "")
        if not _SKU_RE.match(code):
            continue
        size_mm = _int(row[1] if len(row) > 1 else 0)
        sockets = _int(row[7] if len(row) > 7 else 1)
        symbol = row[2] if len(row) > 2 else ""
        pack = _int(row[5] if len(row) > 5 else 0)
        if size_mm == 200 and pack <= 0:
            pack = 2
        speed_idx = col_speed if col_speed is not None else 10
        ceiling_idx = col_ceiling if col_ceiling is not None else 11
        alt_idx = col_alt
        skus.append(
            FactorySku(
                sku_code=code,
                size_mm=size_mm,
                length_code=length_code_from_symbol(symbol, sockets),
                label=_norm(row[3] if len(row) > 3 else ""),
                cover_cm=_num(row[4] if len(row) > 4 else 0),
                pack_qty=pack,
                spacers_per_pack=_int(row[6] if len(row) > 6 else 0),
                socket_ends=sockets,
                pipe_cap_per_piece=_num(row[8] if len(row) > 8 else 0),
                cut_length_m=_num(row[9] if len(row) > 9 else 0),
                line_speed_m_per_min=_num(row[speed_idx] if speed_idx < len(row) else 0),
                depot_ceiling=_int(row[ceiling_idx] if ceiling_idx < len(row) else 0),
                alt_speed_m_per_min=_num(row[alt_idx] if alt_idx is not None and alt_idx < len(row) else 0),
            )
        )
    return FactoryLineData(sizes=sizes, skus=skus)


def load_factory_line(line_code: str) -> FactoryLineData:
    mapping = {
        "protect": DATA_DIR / "protect.csv",
        "general": DATA_DIR / "general.csv",
        "silent": DATA_DIR / "silent10.csv",
    }
    path = mapping[line_code]
    return parse_factory_csv(path)
