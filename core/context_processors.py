from accounts.permissions import get_profile


def user_profile(request):
    """Expose the current user's profile to every template."""
    if request.user.is_authenticated:
        return {"profile": get_profile(request.user)}
    return {"profile": None}


def chrome_nav(request):
    """Sidebar menus resolved from the single nav registry."""
    try:
        from catalog.nav import build_nav_for_request

        return {"nav_sections": build_nav_for_request(request)}
    except Exception:  # noqa: BLE001 — migrations / anonymous
        return {"nav_sections": []}


def _hidden_column_css(request=None) -> str:
    try:
        from catalog.models import SystemNamingKey
        from catalog.naming_registry import naming_preview_key

        preview_hk = naming_preview_key(request) if request is not None else ""
        preview_col = ""
        if preview_hk:
            prow = (
                SystemNamingKey.objects.filter(key=preview_hk)
                .only("column_key")
                .first()
            )
            if prow and prow.column_key:
                preview_col = prow.column_key
            elif ".col." in preview_hk:
                preview_col = preview_hk.rsplit(".col.", 1)[-1]
        rules = []
        qs = SystemNamingKey.objects.filter(
            is_active=False,
            category=SystemNamingKey.Category.COLUMN,
        ).exclude(column_key="")
        for row in qs.only("column_key"):
            ck = (row.column_key or "").replace('"', "")
            if not ck:
                continue
            if preview_col and ck == preview_col.replace('"', ""):
                rules.append(
                    f'[data-col="{ck}"]{{display:table-cell !important;'
                    f"opacity:.42;box-shadow:inset 0 0 0 2px #eab308;"
                    f"background:repeating-linear-gradient(135deg,#fff7d6,#fff7d6 6px,#fff 6px,#fff 12px);}}"
                )
                continue
            rules.append(f'[data-col="{ck}"]{{display:none !important;}}')
        return "".join(rules)
    except Exception:  # noqa: BLE001
        return ""


def table_layout(request):
    """Expose per-menu table height, borders, and width-lock flags."""
    import json

    try:
        from catalog.models import TableLayoutSettings
        from catalog.table_layout import css_for_layouts, locks_from_layouts

        settings = TableLayoutSettings.load()
        layouts = settings.layouts_map()
        locks = locks_from_layouts(layouts)
        return {
            "table_row_height_px": settings.clamped_row_height(),
            "table_width_locks": locks,
            "table_width_locks_json": json.dumps(locks, ensure_ascii=False),
            "table_section_layouts": layouts,
            "table_section_layouts_json": json.dumps(layouts, ensure_ascii=False),
            "table_layout_css": css_for_layouts(layouts) + _hidden_column_css(request),
        }
    except Exception:  # noqa: BLE001 — migrations / early boot
        return {
            "table_row_height_px": 36,
            "table_width_locks": {},
            "table_width_locks_json": "{}",
            "table_section_layouts": {},
            "table_section_layouts_json": "{}",
            "table_layout_css": "",
        }
