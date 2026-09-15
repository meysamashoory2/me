"""Single source of truth for sidebar menus, table-layout tabs, and naming harvest.

Any new sidebar item belongs here. System-data hub, table-settings tabs, and
نام‌گذاری عناوین all read this module so labels stay in sync.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavSurface:
    key: str
    title: str
    url_name: str = ""
    highlight: str = "main.content table, main.content .pcx-table"


@dataclass(frozen=True)
class NavItem:
    key: str
    title: str
    url_name: str
    table_section: str = ""
    match: tuple[str, ...] = ()
    perm: str = ""
    kind: str = "link"
    query: str = ""
    surfaces: tuple[NavSurface, ...] = ()


@dataclass(frozen=True)
class NavSection:
    key: str
    title: str
    items: tuple[NavItem, ...]
    solo: bool = False


def _item(key, title, url_name, table_section="", match=(), perm="", kind="link", query="", surfaces=()):
    names = tuple(match) if match else (url_name,)
    return NavItem(
        key=key,
        title=title,
        url_name=url_name,
        table_section=table_section,
        match=names,
        perm=perm,
        kind=kind,
        query=query,
        surfaces=tuple(surfaces),
    )


NAV_SECTIONS: tuple[NavSection, ...] = (
    NavSection(
        key="dashboard",
        title="داشبورد",
        solo=True,
        items=(
            _item("dashboard", "داشبورد", "dashboard"),
        ),
    ),
    NavSection(
        key="production",
        title="برنامه‌های تولید",
        items=(
            _item(
                "planning",
                "برنامه‌ریزی هفتگی",
                "plan_list",
                "planning",
                match=("plan_list", "plan_detail", "plan_create", "plan_edit", "plan_operate", "plan_set_status"),
                surfaces=(
                    NavSurface("list", "برنامه‌ریزی هفتگی تولید", "plan_list", "table#plan-list-table"),
                    NavSurface("form", "ایجاد برنامه هفتگی", "plan_create", ".panel"),
                    NavSurface("detail", "جزئیات برنامه هفتگی", "plan_detail", "table.table-plan-items"),
                ),
            ),
            _item(
                "systemic",
                "برنامه‌ریزی هوشمند",
                "systemic_intelligence",
                "systemic",
                match=("systemic_intelligence", "inventory_orders"),
                surfaces=(
                    NavSurface("hub", "برنامه‌ریزی هوشمند", "systemic_intelligence", "[data-table-section='systemic'] table"),
                    NavSurface("orders", "بررسی موجودی و سفارشات", "inventory_orders", "#io-orders-table"),
                ),
            ),
            _item(
                "programs",
                "ثبت و کنترل تولید",
                "program_list",
                "production",
                match=(
                    "program_list",
                    "production_hub",
                    "production_list",
                    "program_status",
                    "entry_create",
                    "entry_edit",
                    "pipe_create",
                    "pipe_edit",
                ),
                surfaces=(
                    NavSurface("list", "ثبت و کنترل تولید — تعیین وضعیت", "program_list", "[data-table-section='production'] table"),
                    NavSurface("hub", "ثبت و کنترل تولید", "production_hub", "[data-table-section='production'] table"),
                    NavSurface("entry", "ثبت تولید روزانه", "production_list", "[data-table-section='production'] table"),
                ),
            ),
            _item(
                "history",
                "سوابق تولید",
                "production_history",
                "history",
                match=(
                    "production_history",
                    "production_history_detail",
                    "production_history_archive_detail",
                    "production_conflicts",
                    "production_conflicts_kind",
                ),
                surfaces=(
                    NavSurface("list", "سوابق تولید", "production_history", "table#history-list-table"),
                    NavSurface("detail", "جزئیات سابقه تولید", "production_history_detail", "[data-table-section='history'] table"),
                    NavSurface("conflicts", "بررسی تداخل برنامه", "production_conflicts", "[data-table-section='history'] table"),
                ),
            ),
            _item(
                "pipe_calc",
                "محاسبات تولید",
                "pipe_calc",
                "pipe_calc",
                match=("pipe_calc", "pipe_calc_run"),
                surfaces=(NavSurface("hub", "محاسبات تولید", "pipe_calc", ".pcx-table"),),
            ),
        ),
    ),
    NavSection(
        key="reports",
        title="گزارشات",
        items=(
            _item(
                "reports",
                "گزارش‌ها",
                "report_list",
                "reports",
                match=("report_list", "report_detail", "report_create", "report_edit"),
                surfaces=(
                    NavSurface("list", "لیست گزارش‌ها", "report_list", "table#report-list-table"),
                    NavSurface("detail", "مشاهده گزارش", "report_detail", "table#report-data-table"),
                    NavSurface("form", "طراحی گزارش", "report_create", ".panel"),
                ),
            ),
            _item(
                "forms",
                "فرم‌ها",
                "print_form_list",
                "forms",
                match=("print_form_list", "print_form_detail", "print_form_edit", "print_form_create"),
                surfaces=(
                    NavSurface("list", "فرم‌ها", "print_form_list", "table#form-list-table"),
                    NavSurface("detail", "مشاهده فرم", "print_form_detail", "[data-table-section='forms']"),
                    NavSurface("form", "طراحی فرم", "print_form_create", "[data-table-section='forms']"),
                ),
            ),
            _item(
                "product_data",
                "دیتای محصولات",
                "product_data",
                "product_data",
                match=("product_data", "vouchers_hub"),
                surfaces=(NavSurface("hub", "دیتای محصولات", "product_data", ".product-data-table"),),
            ),
        ),
    ),
    NavSection(
        key="config",
        title="پیکربندی سیستم",
        items=(
            _item(
                "excel",
                "بارگذاری فایل",
                "excel_list",
                "excel",
                match=("excel_list", "excel_import", "excel_detail"),
                surfaces=(
                    NavSurface("list", "بارگذاری فایل", "excel_list", "[data-table-section='excel'] table"),
                    NavSurface("import", "وارد کردن فایل اکسل / CSV", "excel_import", "#excel-dropzone"),
                    NavSurface("detail", "جزئیات فایل اکسل", "excel_detail", "#excel-editor"),
                ),
            ),
            _item(
                "system",
                "مدیریت داده‌ها",
                "system_data",
                "system",
                match=(
                    "system_data",
                    "system_naming_keys",
                    "system_table_layout",
                    "system_table_columns",
                    "system_menu_config",
                    "system_planning_chrome",
                    "planning_process_list",
                    "planning_process_detail",
                    "planning_process_edit",
                ),
                surfaces=(
                    NavSurface("hub", "مدیریت داده‌ها", "system_data", "#system-accordion"),
                    NavSurface("naming", "نام‌گذاری عناوین سیستم", "system_naming_keys", ".naming-keys-table"),
                    NavSurface("layout", "تنظیمات جداول", "system_table_layout", ".table-layout-form"),
                ),
            ),
        ),
    ),
    NavSection(
        key="account",
        title="کاربری سامانه",
        items=(
            _item(
                "users",
                "مدیریت کاربران",
                "user_management",
                "users",
                perm="users",
                surfaces=(NavSurface("hub", "مدیریت کاربران", "user_management", "[data-table-section='users'] table"),),
            ),
            _item("logout", "خروج از سامانه", "logout", kind="logout"),
        ),
    ),
)

# Table-settings tabs follow sidebar order, including items that have tables.
LAYOUT_SECTIONS: tuple[tuple[str, str], ...] = tuple(
    (item.table_section, item.title)
    for section in NAV_SECTIONS
    for item in section.items
    if item.table_section
)

MENU_CONFIG_ITEMS: tuple[tuple[str, str], ...] = (
    ("planning", "برنامه‌ریزی هفتگی"),
    ("systemic", "برنامه‌ریزی هوشمند"),
    ("programs", "ثبت و کنترل تولید"),
    ("history", "سوابق تولید"),
    ("pipe_calc", "محاسبات تولید"),
    ("reports", "گزارش‌ها"),
    ("forms", "فرم‌ها"),
    ("product_data", "دیتای محصولات"),
    ("excel", "بارگذاری فایل"),
    ("system", "مدیریت داده‌ها"),
    ("users", "مدیریت کاربران"),
    ("backup", "پشتیبان‌گیری"),
)


def layout_surfaces(section_key: str) -> tuple[NavSurface, ...]:
    for section in NAV_SECTIONS:
        for item in section.items:
            if item.table_section == section_key:
                return item.surfaces or (NavSurface("", "صفحه بدون سرتیتر"),)
    return (NavSurface("", "صفحه بدون سرتیتر"),)


def layout_item_for_section(section_key: str) -> NavItem | None:
    for section in NAV_SECTIONS:
        for item in section.items:
            if item.table_section == section_key:
                return item
    return None


def _reverse_preview_path(url_name: str, fallback_name: str = "") -> str:
    from django.urls import NoReverseMatch, reverse

    if url_name:
        try:
            return reverse(url_name)
        except NoReverseMatch:
            pass
        pk_models = {
            "plan_detail": ("planning.models", "WeeklyPlan"),
            "plan_edit": ("planning.models", "WeeklyPlan"),
            "production_history_detail": ("production.models", "ProductionHistoryRecord"),
            "excel_detail": ("catalog.models", "ExcelUpload"),
            "report_detail": ("reports.models", "SavedReport"),
            "report_edit": ("reports.models", "SavedReport"),
            "print_form_detail": ("reports.models", "PrintForm"),
            "print_form_edit": ("reports.models", "PrintForm"),
            "program_status": ("production.models", "ProductionProgram"),
        }
        spec = pk_models.get(url_name)
        if spec:
            mod_name, cls_name = spec
            import importlib

            model = getattr(importlib.import_module(mod_name), cls_name)
            obj = model.objects.order_by("-pk").only("pk").first()
            if obj is not None:
                try:
                    return reverse(url_name, args=[obj.pk])
                except NoReverseMatch:
                    pass
    if fallback_name:
        try:
            return reverse(fallback_name)
        except NoReverseMatch:
            return ""
    return ""


def surface_preview_href(section_key: str, surface_key: str, return_path: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    item = layout_item_for_section(section_key)
    surfaces = layout_surfaces(section_key)
    chosen = None
    for surf in surfaces:
        if surf.key == surface_key:
            chosen = surf
            break
    if chosen is None and surfaces:
        chosen = surfaces[0]
    url_name = (chosen.url_name if chosen else "") or (item.url_name if item else "")
    fallback = item.url_name if item else "dashboard"
    path = _reverse_preview_path(url_name, fallback)
    if not path:
        return ""
    highlight = (chosen.highlight if chosen else "") or "main.content table"
    parts = urlsplit(path)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in {"naming_preview", "hk", "hl", "ret"}
    ]
    query.extend(
        [
            ("naming_preview", "1"),
            ("hk", f"layout.{section_key}.{surface_key or 'main'}"),
            ("hl", highlight),
            ("ret", return_path),
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def nav_item_by_key(key: str) -> NavItem | None:
    for section in NAV_SECTIONS:
        for item in section.items:
            if item.key == key:
                return item
    return None


def build_nav_for_request(request) -> list[dict]:
    """Resolved sidebar for the current user (labels + visibility + active)."""
    from django.urls import NoReverseMatch, reverse

    from accounts.permissions import get_profile
    from catalog.naming_registry import lookup_naming_rows

    profile = get_profile(request.user) if getattr(request, "user", None) and request.user.is_authenticated else None
    url_name = ""
    match = getattr(request, "resolver_match", None)
    if match is not None:
        url_name = match.url_name or ""

    keys: list[str] = []
    for section in NAV_SECTIONS:
        keys.append(f"nav.heading.{section.key}")
        for item in section.items:
            keys.append(f"nav.item.{item.key}")
    rows = lookup_naming_rows(keys)

    out: list[dict] = []
    for section in NAV_SECTIONS:
        heading_row = rows.get(f"nav.heading.{section.key}")
        if heading_row is not None and not heading_row.is_active:
            continue
        heading = heading_row.label if heading_row and heading_row.label else section.title
        items_out: list[dict] = []
        section_active = False
        for item in section.items:
            item_row = rows.get(f"nav.item.{item.key}")
            if item_row is not None and not item_row.is_active:
                continue
            if item.perm == "users" and not (
                profile and (profile.can_manage_users or getattr(request.user, "is_superuser", False))
            ):
                continue
            if item.perm == "backup" and not (
                profile and (profile.can_backup or getattr(request.user, "is_superuser", False))
            ):
                continue
            title = item_row.label if item_row and item_row.label else item.title
            href = ""
            if item.kind != "logout":
                try:
                    href = reverse(item.url_name)
                except NoReverseMatch:
                    href = "#"
                if item.query:
                    href = f"{href}?{item.query}"
            active = url_name in item.match
            if active:
                section_active = True
            items_out.append(
                {
                    "key": item.key,
                    "title": title,
                    "href": href,
                    "active": active,
                    "kind": item.kind,
                    "table_section": item.table_section,
                }
            )
        if not items_out:
            continue
        out.append(
            {
                "key": section.key,
                "title": heading,
                "solo": section.solo,
                "active": section_active,
                "items": items_out,
            }
        )
    return out
