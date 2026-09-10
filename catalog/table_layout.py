"""Per-menu table display settings (row height, borders, column-width lock)."""

from __future__ import annotations

from typing import Any

MIN_ROW_HEIGHT = 5
MAX_ROW_HEIGHT = 120
DEFAULT_ROW_HEIGHT = 36

# Order matches the main sidebar menus that have tables.
SECTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("planning", "برنامه‌ریزی هفتگی"),
    ("systemic", "برنامه‌ریزی توسط سیستم"),
    ("production", "ثبت و کنترل تولید"),
    ("history", "سوابق تولید"),
    ("pipe_calc", "محاسبات زمان تولید"),
    ("reports", "گزارش‌ها"),
    ("forms", "فرم‌ها"),
    ("excel", "بارگذاری فایل"),
    ("product_data", "دیتای محصولات"),
    ("system", "مدیریت داده‌های سامانه"),
)

SECTION_KEYS: tuple[str, ...] = tuple(key for key, _label in SECTION_CHOICES)
UNLOCKED_WIDTH_DEFAULTS: frozenset[str] = frozenset({"reports"})


def clamp_row_height(value: Any, default: int = DEFAULT_ROW_HEIGHT) -> int:
    try:
        height = int(value)
    except (TypeError, ValueError):
        height = default
    return max(MIN_ROW_HEIGHT, min(MAX_ROW_HEIGHT, height))


def default_width_locked(section_key: str) -> bool:
    return str(section_key or "") not in UNLOCKED_WIDTH_DEFAULTS


def default_layout(section_key: str) -> dict[str, Any]:
    return {
        "row_height_px": DEFAULT_ROW_HEIGHT,
        "col_border": True,
        "row_border": True,
        "header_border": True,
        "width_locked": default_width_locked(section_key),
    }


def normalize_section_layout(section_key: str, raw: Any) -> dict[str, Any]:
    layout = default_layout(section_key)
    if not isinstance(raw, dict):
        return layout
    if "row_height_px" in raw:
        layout["row_height_px"] = clamp_row_height(raw.get("row_height_px"))
    if "col_border" in raw:
        layout["col_border"] = bool(raw.get("col_border"))
    if "row_border" in raw:
        layout["row_border"] = bool(raw.get("row_border"))
    if "header_border" in raw:
        layout["header_border"] = bool(raw.get("header_border"))
    if "width_locked" in raw:
        layout["width_locked"] = bool(raw.get("width_locked"))
    return layout


def normalize_all_layouts(
    stored: Any,
    *,
    legacy_height: int | None = None,
    legacy_locks: dict[str, bool] | None = None,
) -> dict[str, dict[str, Any]]:
    stored = stored if isinstance(stored, dict) else {}
    legacy_locks = legacy_locks if isinstance(legacy_locks, dict) else {}
    fallback_height = (
        clamp_row_height(legacy_height)
        if legacy_height is not None
        else DEFAULT_ROW_HEIGHT
    )
    out: dict[str, dict[str, Any]] = {}
    for key, _label in SECTION_CHOICES:
        raw = stored.get(key)
        if isinstance(raw, dict):
            out[key] = normalize_section_layout(key, raw)
            continue
        layout = default_layout(key)
        layout["row_height_px"] = fallback_height
        # Old lock maps defaulted to unlocked (False). Only keep explicit True.
        if bool(legacy_locks.get(key)):
            layout["width_locked"] = True
        out[key] = layout
    return out


def locks_from_layouts(layouts: dict[str, dict[str, Any]]) -> dict[str, bool]:
    return {key: bool(cfg.get("width_locked")) for key, cfg in layouts.items()}


# Visible vertical rules. The page stylesheet uses --line (#e2e8f0), which
# disappears on white cells — "show column border" must paint its own stroke.
# Keep it a soft slate-300: dark enough to read on white, light enough not to
# dominate the table.
COLUMN_BORDER_COLOR = "#cbd5e1"


def _section_cells(key: str, tags: tuple[str, ...], suffix: str = "") -> str:
    parts: list[str] = []
    for tag in tags:
        sel = f"{tag}{suffix}"
        parts.append(f'[data-table-section="{key}"] table {sel}')
        parts.append(f'[data-table-section="{key}"] .table {sel}')
        parts.append(f'[data-table-section="{key}"] .pcx-table {sel}')
        parts.append(f'[data-table-section="{key}"].table {sel}')
        parts.append(f'[data-table-section="{key}"].pcx-table {sel}')
    return ",".join(parts)


def css_for_layouts(layouts: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for key, cfg in layouts.items():
        height = clamp_row_height(cfg.get("row_height_px"))
        parts.append(f'[data-table-section="{key}"]{{--table-row-height:{height}px;}}')
        all_cells = _section_cells(key, ("th", "td"))
        body_cells = _section_cells(key, ("td",))
        header_cells = _section_cells(key, ("th",))
        if cfg.get("col_border", True):
            between = _section_cells(key, ("th", "td"), ":not(:last-child)")
            # Paint a single 1px stroke inside the cell. Adjacent cells with
            # border-spacing:0 cover a real border and overflow:hidden clips it,
            # so the background line is the reliable one. Drawing a border here
            # too would double the stroke at every divider (too thick), so we
            # rely on the background line alone.
            parts.append(
                f"{between}{{"
                f"background-image:linear-gradient({COLUMN_BORDER_COLOR},{COLUMN_BORDER_COLOR}) !important;"
                "background-repeat:no-repeat !important;"
                "background-size:1px 100% !important;"
                "background-position:left center !important;"
                "}"
            )
        else:
            parts.append(
                f"{all_cells}{{"
                "border-left:none !important;"
                "border-right:none !important;"
                "border-inline-start:none !important;"
                "border-inline-end:none !important;"
                "background-image:none !important;"
                "}"
            )
        if not cfg.get("row_border", True):
            parts.append(
                f"{body_cells}{{"
                "border-top-color:transparent !important;"
                "border-bottom-color:transparent !important;"
                "}"
            )
        if not cfg.get("header_border", True):
            parts.append(
                f"{header_cells}{{"
                "border-top-color:transparent !important;"
                "border-bottom-color:transparent !important;"
                "}"
            )
            parts.append(
                f'[data-table-section="{key}"] .table-scroll thead th,'
                f'[data-table-section="{key}"] .table-scroll-wide thead th{{'
                "box-shadow:none !important;"
                "}"
            )
    return "".join(parts)
