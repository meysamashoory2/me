"""Per-menu table display settings (row/header height, wrap, colors, borders)."""

from __future__ import annotations

from typing import Any

from catalog.nav import LAYOUT_SECTIONS

MIN_ROW_HEIGHT = 5
MAX_ROW_HEIGHT = 120
DEFAULT_ROW_HEIGHT = 36
DEFAULT_HEADER_HEIGHT = 36

SECTION_CHOICES: tuple[tuple[str, str], ...] = LAYOUT_SECTIONS
SECTION_KEYS: tuple[str, ...] = tuple(key for key, _label in SECTION_CHOICES)
UNLOCKED_WIDTH_DEFAULTS: frozenset[str] = frozenset({"reports"})
COLUMN_BORDER_COLOR = "#cbd5e1"


def clamp_row_height(value: Any, default: int = DEFAULT_ROW_HEIGHT) -> int:
    try:
        height = int(value)
    except (TypeError, ValueError):
        height = default
    return max(MIN_ROW_HEIGHT, min(MAX_ROW_HEIGHT, height))


def clamp_alpha(value: Any, default: int = 100) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(0, min(100, n))


def default_width_locked(section_key: str) -> bool:
    base = str(section_key or "").split("::", 1)[0]
    return base not in UNLOCKED_WIDTH_DEFAULTS


def default_layout(section_key: str) -> dict[str, Any]:
    return {
        "row_height_px": DEFAULT_ROW_HEIGHT,
        "header_height_px": DEFAULT_HEADER_HEIGHT,
        "col_border": True,
        "row_border": True,
        "header_border": True,
        "width_locked": default_width_locked(section_key),
        "header_wrap": False,
        "body_wrap": False,
        "marquee": True,
        "header_color": "#f8fafc",
        "header_alpha": 100,
        "row_selected_color": "#dbeafe",
        "row_selected_alpha": 100,
        "cell_outline_color": "#2563eb",
        "cell_outline_alpha": 100,
        "cell_fill_color": "#ffffff",
        "cell_fill_alpha": 100,
    }


def _hex_ok(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("#") and len(raw) in (4, 7):
        return raw
    return fallback


def normalize_section_layout(section_key: str, raw: Any) -> dict[str, Any]:
    layout = default_layout(section_key)
    if not isinstance(raw, dict):
        return layout
    if "row_height_px" in raw:
        layout["row_height_px"] = clamp_row_height(raw.get("row_height_px"))
    if "header_height_px" in raw:
        layout["header_height_px"] = clamp_row_height(
            raw.get("header_height_px"), DEFAULT_HEADER_HEIGHT
        )
    for flag in ("col_border", "row_border", "header_border", "width_locked", "header_wrap", "body_wrap", "marquee"):
        if flag in raw:
            layout[flag] = bool(raw.get(flag))
    layout["header_color"] = _hex_ok(raw.get("header_color"), layout["header_color"])
    layout["row_selected_color"] = _hex_ok(raw.get("row_selected_color"), layout["row_selected_color"])
    layout["cell_outline_color"] = _hex_ok(raw.get("cell_outline_color"), layout["cell_outline_color"])
    layout["cell_fill_color"] = _hex_ok(raw.get("cell_fill_color"), layout["cell_fill_color"])
    for alpha_key in ("header_alpha", "row_selected_alpha", "cell_outline_alpha", "cell_fill_alpha"):
        if alpha_key in raw:
            layout[alpha_key] = clamp_alpha(raw.get(alpha_key))
    if not layout["col_border"] and not layout["row_border"]:
        layout["body_wrap"] = True
        layout["marquee"] = False
    if layout["body_wrap"]:
        layout["marquee"] = False
    return layout


def merge_layout_post(current: dict[str, Any], post) -> dict[str, Any]:
    """Update only fields that were actually posted so a closed dialog cannot wipe the other."""
    posted = dict(current)
    part = str(post.get("layout_part") or "").strip()
    header_keys_present = "header_height_px" in post or "header_border" in post or part == "header"
    body_keys_present = "row_height_px" in post or "col_border" in post or part == "body"
    if part == "header":
        body_keys_present = False
        header_keys_present = True
    elif part == "body":
        header_keys_present = False
        body_keys_present = True

    if header_keys_present:
        if "header_height_px" in post:
            posted["header_height_px"] = clamp_row_height(
                post.get("header_height_px"), posted["header_height_px"]
            )
        if "header_border" in post:
            posted["header_border"] = post.get("header_border") == "show"
        posted["width_locked"] = post.get("width_locked") == "1"
        posted["header_wrap"] = post.get("header_wrap") == "1"
        if "header_color" in post:
            posted["header_color"] = post.get("header_color") or posted["header_color"]
        if "header_alpha" in post:
            posted["header_alpha"] = clamp_alpha(post.get("header_alpha"), posted["header_alpha"])
    elif part != "body":
        if "width_locked" in post:
            posted["width_locked"] = post.get("width_locked") == "1"
        if "header_wrap" in post:
            posted["header_wrap"] = post.get("header_wrap") == "1"
    if body_keys_present:
        if "row_height_px" in post:
            posted["row_height_px"] = clamp_row_height(
                post.get("row_height_px"), posted["row_height_px"]
            )
        if "col_border" in post:
            posted["col_border"] = post.get("col_border") == "show"
        if "row_border" in post:
            posted["row_border"] = post.get("row_border") == "show"
        posted["body_wrap"] = post.get("body_wrap") == "1"
        posted["marquee"] = post.get("marquee") == "1"
        if "row_selected_color" in post:
            posted["row_selected_color"] = post.get("row_selected_color") or posted["row_selected_color"]
        if "row_selected_alpha" in post:
            posted["row_selected_alpha"] = clamp_alpha(
                post.get("row_selected_alpha"), posted["row_selected_alpha"]
            )
        if "cell_outline_color" in post:
            posted["cell_outline_color"] = post.get("cell_outline_color") or posted["cell_outline_color"]
        if "cell_outline_alpha" in post:
            posted["cell_outline_alpha"] = clamp_alpha(
                post.get("cell_outline_alpha"), posted["cell_outline_alpha"]
            )
        if "cell_fill_color" in post:
            posted["cell_fill_color"] = post.get("cell_fill_color") or posted["cell_fill_color"]
        if "cell_fill_alpha" in post:
            posted["cell_fill_alpha"] = clamp_alpha(
                post.get("cell_fill_alpha"), posted["cell_fill_alpha"]
            )
    return posted


def layout_storage_key(section: str, surface: str = "") -> str:
    section = str(section or "").strip()
    surface = str(surface or "").strip()
    if surface:
        return f"{section}::{surface}"
    return section


def split_layout_key(key: str) -> tuple[str, str]:
    raw = str(key or "")
    if "::" in raw:
        section, surface = raw.split("::", 1)
        return section, surface
    return raw, ""


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
        if bool(legacy_locks.get(key)):
            layout["width_locked"] = True
        out[key] = layout
    for key, raw in stored.items():
        if key in out or not isinstance(raw, dict):
            continue
        section, _surface = split_layout_key(str(key))
        if section not in SECTION_KEYS:
            continue
        out[str(key)] = normalize_section_layout(section, raw)
    return out


def locks_from_layouts(layouts: dict[str, dict[str, Any]]) -> dict[str, bool]:
    return {key: bool(cfg.get("width_locked")) for key, cfg in layouts.items()}


def _rgba(hex_color: str, alpha: int) -> str:
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = clamp_alpha(alpha) / 100
    return f"rgba({r},{g},{b},{a:.2f})"


def _scopes(key: str) -> list[str]:
    section, surface = split_layout_key(key)
    if not section:
        return []
    if surface:
        pair = f'[data-table-section="{section}"][data-table-surface="{surface}"]'
        return [pair, f'{pair} table', f'table{pair}']
    host = f'[data-table-section="{section}"]'
    return [host, f'{host} table', f'table{host}']


def _scope(key: str) -> str:
    scopes = _scopes(key)
    return scopes[0] if scopes else ""


def _section_cells(key: str, tags: tuple[str, ...], suffix: str = "") -> str:
    parts: list[str] = []
    for scope in _scopes(key):
        for tag in tags:
            sel = f"{tag}{suffix}"
            parts.append(f"{scope} table {sel}")
            parts.append(f"{scope} .table {sel}")
            parts.append(f"{scope} .pcx-table {sel}")
            parts.append(f"{scope} .results table {sel}")
            parts.append(f"{scope}.table {sel}")
            parts.append(f"{scope}.pcx-table {sel}")
    return ",".join(parts)


def css_for_layouts(layouts: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    ordered = sorted(layouts.items(), key=lambda kv: (0 if "::" in kv[0] else -1, kv[0]))
    # Defaults (no surface) first, then surface overrides.
    ordered.sort(key=lambda kv: 1 if "::" in kv[0] else 0)
    for key, cfg in ordered:
        scope = ",".join(_scopes(key))
        row_h = clamp_row_height(cfg.get("row_height_px"))
        head_h = clamp_row_height(cfg.get("header_height_px"), DEFAULT_HEADER_HEIGHT)
        parts.append(
            f"{scope}{{"
            f"--table-row-height:{row_h}px !important;"
            f"--table-header-height:{head_h}px !important;"
            f"--table-header-bg:{_rgba(cfg.get('header_color') or '#f8fafc', cfg.get('header_alpha', 100))} !important;"
            f"--table-row-selected:{_rgba(cfg.get('row_selected_color') or '#dbeafe', cfg.get('row_selected_alpha', 100))} !important;"
            f"--table-cell-outline:{_rgba(cfg.get('cell_outline_color') or '#2563eb', cfg.get('cell_outline_alpha', 100))} !important;"
            f"--table-cell-fill:{_rgba(cfg.get('cell_fill_color') or '#ffffff', cfg.get('cell_fill_alpha', 100))} !important;"
            "}"
        )
        all_cells = _section_cells(key, ("th", "td"))
        body_cells = _section_cells(key, ("td",))
        header_cells = _section_cells(key, ("th",))
        if cfg.get("col_border", True):
            between = _section_cells(key, ("th", "td"), ":not(:last-child)")
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
                f"{scope} .table-scroll thead th,"
                f"{scope} .table-scroll-wide thead th{{box-shadow:none !important;}}"
            )
        header_wrap = bool(cfg.get("header_wrap"))
        header_border = bool(cfg.get("header_border", True))
        if header_wrap:
            parts.append(
                f"{header_cells}{{white-space:normal !important;overflow:hidden !important;"
                "text-overflow:clip !important;height:var(--table-header-height);"
                "max-height:var(--table-header-height);vertical-align:middle;}}"
            )
        elif header_border:
            parts.append(
                f"{header_cells}{{white-space:nowrap !important;overflow:hidden !important;"
                "text-overflow:clip !important;height:var(--table-header-height);"
                "max-height:var(--table-header-height);}}"
            )
        else:
            parts.append(
                f"{header_cells}{{white-space:nowrap !important;overflow:visible !important;"
                "text-overflow:clip !important;height:var(--table-header-height);"
                "max-height:var(--table-header-height);}}"
            )
        body_wrap = bool(cfg.get("body_wrap"))
        body_border = bool(cfg.get("col_border", True) or cfg.get("row_border", True))
        if body_wrap or not body_border:
            parts.append(
                f"{body_cells}{{white-space:normal !important;overflow:hidden !important;"
                "height:var(--table-row-height);max-height:var(--table-row-height);"
                "vertical-align:middle;}}"
            )
        else:
            parts.append(
                f"{body_cells}{{white-space:nowrap !important;overflow:hidden !important;"
                "text-overflow:clip !important;height:var(--table-row-height);"
                "max-height:var(--table-row-height);}}"
            )
        parts.append(
            f"{header_cells},"
            f"{scope} .table-scroll thead th,"
            f"{scope} .table-scroll-wide thead th{{background:var(--table-header-bg) !important;}}"
        )
        parts.append(
            f"{scope} table tbody tr.is-row-selected > td,"
            f"{scope} table tbody tr.is-row-selected > th,"
            f"{scope} table.js-table-nav tbody tr.is-row-selected > td,"
            f"{scope} table.js-table-nav tbody tr.is-row-selected > th{{"
            "background:var(--table-row-selected) !important;}}"
        )
        parts.append(
            f"{scope} table tbody tr.is-row-selected > td.is-cell-focus,"
            f"{scope} table tbody tr.is-row-selected > th.is-cell-focus{{"
            "background:var(--table-cell-fill) !important;"
            "outline:1px solid var(--table-cell-outline) !important;"
            "outline-offset:-1px !important;}}"
        )
        if cfg.get("marquee"):
            parts.append(f"{scope}{{--table-marquee:1;}}")
        else:
            parts.append(f"{scope}{{--table-marquee:0;}}")
    return "".join(parts)
