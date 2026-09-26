"""Per-menu table display settings (row/header height, wrap, colors, borders)."""

from __future__ import annotations

import math
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
GLOBAL_LAYOUT_KEY = "all"
PROFILE_LAYOUT_KEY = "profiles"
PROFILE_KEYS = ("compact", "report", "data_entry")
PROFILE_LABELS = {
    "compact": "فشرده",
    "report": "گزارش / محاسبات",
    "data_entry": "ورود اطلاعات",
}
PROFILE_DEFAULTS: dict[str, dict[str, int | float]] = {
    "compact": {"row_height_px": 26, "header_height_px": 28, "font_size_px": 12, "cell_padding_px": 2},
    "report": {"row_height_px": 34, "header_height_px": 34, "font_size_px": 12.5, "cell_padding_px": 5},
    "data_entry": {"row_height_px": 34, "header_height_px": 34, "font_size_px": 13, "cell_padding_px": 5},
}
PROFILE_INLINE_PADDING_OFFSET = {"compact": 4, "report": 3, "data_entry": 3}
PROFILE_VALUE_BOUNDS = {
    "row_height_px": (18, 80),
    "header_height_px": (18, 80),
    "font_size_px": (9, 24),
    "cell_padding_px": (1, 24),
}
TABLE_PROFILE_REGISTRY: dict[str, tuple[dict[str, str], ...]] = {
    "compact": ({"key": "pipe_depot", "label": "برنامه دپو و نیاز تولید", "module": "محاسبات تولید لوله"},),
    "report": ({"key": "pipe_calculation_results", "label": "جدول محاسباتی لوله و مواد", "module": "محاسبات تولید لوله"},),
    "data_entry": ({"key": "pipe_initial_definitions", "label": "تعاریف اولیه", "module": "محاسبات تولید لوله"},),
}
HEADER_LAYOUT_KEYS = (
    "header_height_px",
    "header_border",
    "width_locked",
    "header_wrap",
    "header_color",
    "header_alpha",
)
BODY_LAYOUT_KEYS = (
    "row_height_px",
    "col_border",
    "row_border",
    "body_wrap",
    "marquee",
    "row_selected_color",
    "row_selected_alpha",
    "cell_outline_color",
    "cell_outline_alpha",
    "cell_fill_color",
    "cell_fill_alpha",
)


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


class ProfileValidationError(ValueError):
    """A profile density value is malformed or outside its safe range."""


def default_profile_layout(profile_key: str) -> dict[str, int | float]:
    if profile_key not in PROFILE_DEFAULTS:
        raise KeyError(profile_key)
    return dict(PROFILE_DEFAULTS[profile_key])


def _profile_number(value: Any, field: str, *, strict: bool, fallback: int | float) -> int | float:
    low, high = PROFILE_VALUE_BOUNDS[field]
    try:
        number = float(value)
    except (TypeError, ValueError):
        if strict:
            raise ProfileValidationError(f"مقدار «{field}» باید عددی باشد.") from None
        return fallback
    if not math.isfinite(number) or number < low or number > high:
        if strict:
            raise ProfileValidationError(f"مقدار «{field}» باید بین {low} و {high} باشد.")
        return fallback
    if field != "font_size_px":
        if not number.is_integer():
            if strict:
                raise ProfileValidationError(f"مقدار «{field}» باید عدد صحیح باشد.")
            return fallback
        return int(number)
    return round(number, 1)


def normalize_profile_layout(profile_key: str, raw: Any) -> dict[str, int | float]:
    defaults = default_profile_layout(profile_key)
    source = raw if isinstance(raw, dict) else {}
    return {
        field: _profile_number(source.get(field, fallback), field, strict=False, fallback=fallback)
        for field, fallback in defaults.items()
    }


def normalize_profile_layouts(stored: Any) -> dict[str, dict[str, int | float]]:
    root = stored if isinstance(stored, dict) else {}
    raw_profiles = root.get(PROFILE_LAYOUT_KEY)
    raw_profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
    return {key: normalize_profile_layout(key, raw_profiles.get(key)) for key in PROFILE_KEYS}


def profile_layout_from_post(profile_key: str, post) -> dict[str, int | float]:
    defaults = default_profile_layout(profile_key)
    if any(field not in post for field in defaults):
        raise ProfileValidationError("همه مقادیر تراکم پروفایل الزامی هستند.")
    return {
        field: _profile_number(post.get(field), field, strict=True, fallback=fallback)
        for field, fallback in defaults.items()
    }


def css_for_profiles(profiles: Any) -> str:
    normalized = (
        profiles
        if isinstance(profiles, dict) and all(key in profiles for key in PROFILE_KEYS)
        else normalize_profile_layouts({PROFILE_LAYOUT_KEY: profiles})
    )
    parts: list[str] = []
    for key in PROFILE_KEYS:
        cfg = normalize_profile_layout(key, normalized.get(key))
        selector = f".erp-table-profile--{key.replace('_', '-')}"
        inline_padding = int(cfg["cell_padding_px"]) + PROFILE_INLINE_PADDING_OFFSET[key]
        parts.append(
            f"{selector}{{"
            f"--table-profile-row-height:{cfg['row_height_px']}px;"
            f"--table-profile-header-height:{cfg['header_height_px']}px;"
            f"--table-profile-font-size:{cfg['font_size_px']}px;"
            f"--table-profile-cell-padding-block:{cfg['cell_padding_px']}px;"
            f"--table-profile-cell-padding-inline:{inline_padding}px;"
            "}"
        )
    return "".join(parts)


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
        "header_color": "#c7d7ea",
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


def reset_layout_part(current: dict[str, Any], part: str) -> dict[str, Any]:
    """Reset one half of the unified settings without disturbing the other."""
    defaults = default_layout(GLOBAL_LAYOUT_KEY)
    result = dict(current)
    keys = HEADER_LAYOUT_KEYS if part == "header" else BODY_LAYOUT_KEYS
    for key in keys:
        result[key] = defaults[key]
    return result


def resolve_global_layout(layouts: Any) -> dict[str, Any]:
    """Read the shared setting, falling back safely to legacy per-menu data."""
    stored = layouts if isinstance(layouts, dict) else {}
    raw = stored.get(GLOBAL_LAYOUT_KEY)
    if not isinstance(raw, dict):
        raw = stored.get("planning")
    if not isinstance(raw, dict):
        raw = next((value for value in stored.values() if isinstance(value, dict)), {})
    return normalize_section_layout(GLOBAL_LAYOUT_KEY, raw)


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
    if isinstance(stored.get(GLOBAL_LAYOUT_KEY), dict):
        shared = normalize_section_layout(GLOBAL_LAYOUT_KEY, stored[GLOBAL_LAYOUT_KEY])
        # Replicas keep older model helpers and templates compatible while the
        # canonical stored preference remains under ``all``.
        return {
            GLOBAL_LAYOUT_KEY: shared,
            **{key: dict(shared) for key in SECTION_KEYS},
        }
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
    if GLOBAL_LAYOUT_KEY in layouts:
        return {GLOBAL_LAYOUT_KEY: bool(resolve_global_layout(layouts).get("width_locked"))}
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
    """Host elements only — never include a descendant in the same comma list.

    Appending a suffix to a comma-joined list like ``A,B .child`` paints A itself.
    """
    section, surface = split_layout_key(key)
    if not section:
        return []
    if surface:
        return [f'[data-table-section="{section}"][data-table-surface="{surface}"]']
    return [f'[data-table-section="{section}"]']


def _scope(key: str) -> str:
    scopes = _scopes(key)
    return scopes[0] if scopes else ""


def _sel(key: str, *suffixes: str) -> str:
    out: list[str] = []
    for host in _scopes(key):
        for suffix in suffixes:
            out.append(f"{host}{suffix}")
    return ",".join(out)


def _section_cells(key: str, tags: tuple[str, ...], suffix: str = "") -> str:
    parts: list[str] = []
    for host in _scopes(key):
        for tag in tags:
            sel = f"{tag}{suffix}"
            parts.append(f"{host} table {sel}")
            parts.append(f"{host} .table {sel}")
            parts.append(f"{host} .pcx-table {sel}")
            parts.append(f"{host} .results table {sel}")
    return ",".join(parts)


def css_for_layouts(
    layouts: dict[str, dict[str, Any]],
    profiles: dict[str, dict[str, int | float]] | None = None,
) -> str:
    parts: list[str] = []
    if GLOBAL_LAYOUT_KEY in layouts:
        shared = resolve_global_layout(layouts)
        layouts = {key: dict(shared) for key in SECTION_KEYS}
    ordered = sorted(layouts.items(), key=lambda kv: (0 if "::" in kv[0] else -1, kv[0]))
    # Defaults (no surface) first, then surface overrides.
    ordered.sort(key=lambda kv: 1 if "::" in kv[0] else 0)
    for key, cfg in ordered:
        host = _scope(key)
        row_h = clamp_row_height(cfg.get("row_height_px"))
        head_h = clamp_row_height(cfg.get("header_height_px"), DEFAULT_HEADER_HEIGHT)
        if host:
            parts.append(
                f"{host}{{"
                f"--table-row-height:{row_h}px;"
                f"--table-header-height:{head_h}px;"
                f"--table-header-bg:{_rgba(cfg.get('header_color') or '#c7d7ea', cfg.get('header_alpha', 100))};"
                f"--table-row-selected:{_rgba(cfg.get('row_selected_color') or '#dbeafe', cfg.get('row_selected_alpha', 100))};"
                f"--table-cell-outline:{_rgba(cfg.get('cell_outline_color') or '#2563eb', cfg.get('cell_outline_alpha', 100))};"
                f"--table-cell-fill:{_rgba(cfg.get('cell_fill_color') or '#ffffff', cfg.get('cell_fill_alpha', 100))};"
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
                f"{_sel(key, ' .table-scroll thead th', ' .table-scroll-wide thead th')}"
                f"{{box-shadow:none !important;}}"
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
        header_bg = ",".join(
            p
            for p in (
                header_cells,
                _sel(key, " .table-scroll thead th", " .table-scroll-wide thead th"),
            )
            if p
        )
        if header_bg:
            parts.append(f"{header_bg}{{background:var(--table-header-bg) !important;}}")
        selected = _sel(
            key,
            " table tbody tr.is-row-selected > td",
            " table tbody tr.is-row-selected > th",
            " .table tbody tr.is-row-selected > td",
            " .table tbody tr.is-row-selected > th",
            " .pcx-table tbody tr.is-row-selected > td",
            " .pcx-table tbody tr.is-row-selected > th",
        )
        if selected:
            parts.append(f"{selected}{{background:var(--table-row-selected) !important;}}")
        focus = _sel(
            key,
            " table tbody tr.is-row-selected > td.is-cell-focus",
            " table tbody tr.is-row-selected > th.is-cell-focus",
            " .table tbody tr.is-row-selected > td.is-cell-focus",
            " .table tbody tr.is-row-selected > th.is-cell-focus",
        )
        if focus:
            parts.append(
                f"{focus}{{"
                "background:var(--table-cell-fill) !important;"
                "outline:1px solid var(--table-cell-outline) !important;"
                "outline-offset:-1px !important;}}"
            )
        if host:
            parts.append(f"{host}{{--table-marquee:{1 if cfg.get('marquee') else 0};}}")
    if profiles is not None:
        parts.append(css_for_profiles(profiles))
    return "".join(parts)
