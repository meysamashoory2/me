"""System naming-key registry: harvest, resolve, and sync labels."""

from __future__ import annotations

from typing import Any, Iterable

from django.db import transaction

from catalog.models import SystemNamingKey


def naming_preview_key(request) -> str:
    """Return the naming-key being previewed, or empty when not in preview."""
    get = getattr(request, "GET", None)
    if get is None or get.get("naming_preview") != "1":
        return ""
    return str(get.get("hk") or "").strip()


def resolve_label(key: str, default: str = "") -> str:
    """Return the current display label for a naming key (or default/key)."""
    key = (key or "").strip()
    if not key:
        return default
    row = (
        SystemNamingKey.objects.filter(key=key, is_active=True)
        .only("label")
        .first()
    )
    if row and row.label:
        return row.label
    return default or key


def lookup_naming_rows(keys: Iterable[str]) -> dict[str, SystemNamingKey]:
    """Batch-load naming rows (active and inactive) keyed by ``key``."""
    key_list = [str(k).strip() for k in keys if str(k).strip()]
    if not key_list:
        return {}
    return {
        r.key: r
        for r in SystemNamingKey.objects.filter(key__in=key_list).only(
            "key", "label", "is_active", "default_label", "is_key"
        )
    }


def resolve_from_row(
    rows: dict[str, SystemNamingKey],
    key: str,
    default: str = "",
    *,
    require_active: bool = True,
) -> tuple[bool, str]:
    """Return ``(is_active_or_missing, label)`` using a preloaded row map.

    Missing keys are treated as active with ``default``.
    When ``require_active`` is True and the row is inactive, label still returns
    ``default`` (useful for chrome that must stay visible).
    """
    row = rows.get(key)
    if row is None:
        return True, default
    label = row.label or default
    if require_active and not row.is_active:
        return False, default
    return bool(row.is_active), label


def resolve_labels(keys: Iterable[str], defaults: dict[str, str] | None = None) -> dict[str, str]:
    """Batch resolve active labels; falls back to ``defaults`` then the key itself."""
    defaults = defaults or {}
    key_list = [str(k).strip() for k in keys if str(k).strip()]
    if not key_list:
        return {}
    found = {
        r.key: r.label
        for r in SystemNamingKey.objects.filter(key__in=key_list, is_active=True).only(
            "key", "label"
        )
    }
    return {
        k: (found.get(k) or defaults.get(k) or k)
        for k in key_list
    }


def table_columns(table_key: str, *, include_inactive: bool = False) -> list[SystemNamingKey]:
    qs = SystemNamingKey.objects.filter(
        category=SystemNamingKey.Category.COLUMN,
        table_key=table_key,
    )
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs.order_by("order", "id"))


def column_label_map_for_table(table_key: str) -> dict[str, str]:
    """Map short column_key → label for a UI table."""
    return {
        (r.column_key or r.key): r.label
        for r in table_columns(table_key)
        if (r.column_key or r.key)
    }


def section_choices() -> list[tuple[str, str]]:
    from catalog.system_sections import build_system_groups

    out: list[tuple[str, str]] = [("", "به هیچ بخشی وصل نیست")]
    for group in build_system_groups():
        for item in group.items:
            out.append((item.key, f"{group.title} / {item.title}"))
    return out


KIND_LABELS: list[tuple[str, str]] = [
    ("heading", "سرتیتر"),
    ("menu", "منو"),
    ("column", "سرستون"),
    ("table", "جدول"),
    ("key", "کلید"),
]


def kind_code(key: str, category: str = "") -> str:
    k = (key or "").strip()
    if k.startswith("nav.heading.") or k.startswith("system.group."):
        return "heading"
    if k.startswith("nav.item.") or k.startswith("system.section."):
        return "menu"
    if category == SystemNamingKey.Category.COLUMN or ".col." in k:
        return "column"
    if category == SystemNamingKey.Category.TABLE or k.startswith("ui.table.") or k.startswith("admin.table."):
        return "table"
    return "key"


def kind_label(code: str) -> str:
    return dict(KIND_LABELS).get(code, "کلید")


def infer_page_key(key: str, table_key: str = "", section_key: str = "") -> str:
    k = (key or "").strip()
    if k.startswith("nav.heading.") or k.startswith("nav.item."):
        return k.rsplit(".", 1)[-1]
    if k.startswith("system.group.") or k.startswith("system.section."):
        return "system"
    if "planning.plan_list" in k or table_key.startswith("planning"):
        return "planning"
    if "history_list" in k or table_key.endswith("history_list"):
        return "history"
    if "excel" in k or table_key.startswith("catalog.excel"):
        return "excel"
    if k.startswith("transfer.") or "excel_import" in (key or ""):
        return "excel"
    if k.startswith("report.") or table_key.startswith("report."):
        return "reports"
    if k.startswith("admin."):
        return "system"
    if section_key:
        return section_key
    return "other"


def _spec(
    *,
    key: str,
    label: str,
    address: str = "",
    category: str = SystemNamingKey.Category.OTHER,
    section_key: str = "",
    table_key: str = "",
    column_key: str = "",
    order: int = 0,
    is_key: bool = False,
    page_key: str = "",
    url_name: str = "",
    highlight: str = "",
) -> dict[str, Any]:
    return {
        "key": key[:220],
        "label": label[:200],
        "default_label": label[:200],
        "address": address[:400],
        "category": category,
        "section_key": section_key[:80],
        "table_key": table_key[:120],
        "column_key": column_key[:120],
        "order": order,
        "is_key": bool(is_key),
        "page_key": (page_key or infer_page_key(key, table_key, section_key))[:80],
        "url_name": url_name[:120],
        "highlight": highlight[:220],
    }


def _harvest_sections() -> list[dict[str, Any]]:
    from catalog.system_sections import build_system_groups

    out: list[dict[str, Any]] = []
    for gi, group in enumerate(build_system_groups()):
        out.append(
            _spec(
                key=f"system.group.{group.key}",
                label=group.title,
                address=f"داده‌های سیستم ← گروه «{group.title}»",
                category=SystemNamingKey.Category.SECTION,
                section_key=group.key,
                order=gi * 100,
            )
        )
        for ii, item in enumerate(group.items):
            target = item.url_name or item.admin_changelist or ""
            out.append(
                _spec(
                    key=f"system.section.{item.key}",
                    label=item.title,
                    address=(
                        f"داده‌های سیستم ← {group.title} ← «{item.title}»"
                        + (f" → {target}" if target else "")
                    ),
                    category=SystemNamingKey.Category.SECTION,
                    section_key=item.key,
                    order=gi * 100 + ii + 1,
                )
            )
    return out


def _harvest_ui_columns() -> list[dict[str, Any]]:
    """Known app tables with data-col headers (exact template addresses)."""
    tables: list[tuple[str, str, str, str, list[tuple[str, str]]]] = [
        (
            "planning.plan_list",
            "فهرست برنامه‌ریزی هفتگی",
            "weekly_plans",
            "templates/planning/plan_list.html",
            [
                ("row", "ردیف"),
                ("program_number", "شماره برنامه"),
                ("date", "تاریخ برنامه"),
                ("weekday", "روز برنامه"),
                ("mode", "نحوه"),
                ("creator", "ایجاد کننده"),
                ("forms", "تعداد فرم"),
                ("calendar", "تقویم برنامه"),
                ("mold_count", "تعداد قالب برنامه"),
                ("active_mold_count", "تعداد قالب فعال"),
            ],
        ),
        (
            "production.history_list",
            "فهرست سوابق تولید",
            "history",
            "templates/production/history.html",
            [
                ("plan_number", "شماره برنامه"),
                ("plan_date", "تاریخ برنامه‌ریزی"),
                ("machine", "شناسه دستگاه"),
                ("product_name", "نام جنس"),
                ("mold_number", "شماره قالب"),
                ("unique_code", "کد یکتا"),
                ("plan_start", "تاریخ شروع برنامه"),
                ("actual_start", "تاریخ شروع واقعی"),
                ("actual_end", "تاریخ پایان تولید"),
                ("planned_qty", "مقدار تولید برنامه (عدد)"),
                ("actual_qty", "مقدار تولید واقعی (عدد)"),
                ("planned_cycle", "سیکل تولید برنامه (ثانیه)"),
                ("last_cycle", "آخرین سیکل تولید (ثانیه)"),
                ("planned_hours", "ساعت تولید برنامه"),
                ("active_cavities", "تعداد حفره فعال"),
                ("last_cavities", "آخرین وضعیت حفره"),
                ("scrap", "ضایعات تولید"),
            ],
        ),
        (
            "catalog.excel_list",
            "فهرست فایل‌های اکسل",
            "excel_tables",
            "templates/catalog/excel_list.html",
            [
                ("title", "نام فایل"),
                ("table_count", "تعداد جدول"),
                ("row_total", "جمع ردیف"),
                ("table_names", "جداول"),
                ("uploader", "بارگذارنده"),
                ("created", "تاریخ"),
                ("ops", "عملیات"),
            ],
        ),
        (
            "catalog.system_data_hub",
            "هاب داده‌های سیستم",
            "",
            "templates/catalog/system_data.html",
            [
                ("row", "ردیف"),
                ("section", "بخش"),
                ("count", "تعداد"),
            ],
        ),
    ]
    out: list[dict[str, Any]] = []
    preview = {
        "planning.plan_list": ("plan_list", "planning"),
        "production.history_list": ("production_history", "history"),
        "catalog.excel_list": ("excel_list", "excel"),
        "catalog.system_data_hub": ("system_data", "system"),
    }
    for table_key, table_label, section_key, template, cols in tables:
        url_name, page_key = preview.get(table_key, ("", infer_page_key(table_key, table_key, section_key)))
        out.append(
            _spec(
                key=f"ui.table.{table_key}",
                label=table_label,
                address=f"{page_key or 'سامانه'} ← {table_label}",
                category=SystemNamingKey.Category.TABLE,
                section_key=section_key,
                table_key=table_key,
                page_key=page_key,
                url_name=url_name,
                highlight=f'[data-table-section="{page_key or "system"}"] table',
                order=0,
            )
        )
        for i, (col_key, label) in enumerate(cols, start=1):
            out.append(
                _spec(
                    key=f"ui.table.{table_key}.col.{col_key}",
                    label=label,
                    address=f"{table_label} ← سرستون «{label}»",
                    category=SystemNamingKey.Category.COLUMN,
                    section_key=section_key,
                    table_key=table_key,
                    column_key=col_key,
                    page_key=page_key,
                    url_name=url_name,
                    highlight=(
                        f'[data-table-section="{page_key or "system"}"] '
                        f'thead th[data-col="{col_key}"]'
                    ),
                    order=i,
                )
            )
    return out


def _harvest_transfer() -> list[dict[str, Any]]:
    from catalog.transfer import TRANSFER_UI_LABELS, list_destinations

    out: list[dict[str, Any]] = []
    for dest in list_destinations():
        dest_id = str(dest.get("id") or "")
        dest_label = str(dest.get("label") or dest_id)
        out.append(
            _spec(
                key=f"transfer.dest.{dest_id}",
                label=dest_label,
                address=(
                    f"دیالوگ انتقال داده ← بخش مقصد «{dest_label}» "
                    f"(templates/catalog/excel_detail.html#excel-transfer-dialog)"
                ),
                category=SystemNamingKey.Category.TRANSFER,
                section_key="transfer_dialog_labels",
                table_key=f"transfer.{dest_id}",
                order=0,
            )
        )
        for li, level in enumerate(dest.get("levels") or [], start=1):
            level_id = str(level.get("id") or "")
            level_label = str(level.get("label") or level_id)
            table_key = f"transfer.{dest_id}.{level_id}"
            out.append(
                _spec(
                    key=f"transfer.level.{dest_id}.{level_id}",
                    label=level_label,
                    address=(
                        f"دیالوگ انتقال داده ← {dest_label} ← سطح «{level_label}»"
                    ),
                    category=SystemNamingKey.Category.TRANSFER,
                    section_key="transfer_dialog_labels",
                    table_key=table_key,
                    order=li,
                )
            )
            for fi, field in enumerate(level.get("fields") or [], start=1):
                fkey = str(field.get("key") or "")
                flabel = str(field.get("label") or fkey)
                if not fkey:
                    continue
                out.append(
                    _spec(
                        key=f"transfer.field.{dest_id}.{level_id}.{fkey}",
                        label=flabel,
                        address=(
                            f"دیالوگ انتقال داده ← {dest_label} ← {level_label} "
                            f"← فیلد «{flabel}»"
                        ),
                        category=SystemNamingKey.Category.COLUMN,
                        section_key="transfer_dialog_labels",
                        table_key=table_key,
                        column_key=fkey,
                        order=fi,
                        is_key=bool(field.get("is_key")),
                    )
                )

    # Dialog / page chrome (titles, labels, buttons)
    chrome_order = 0
    for key, label in TRANSFER_UI_LABELS.items():
        chrome_order += 1
        if key.startswith("transfer.ui.import."):
            address = (
                f"دیالوگ ورود اکسل ← «{label}» "
                f"(templates/catalog/excel_import.html#excel-import-dialog)"
            )
        elif key.startswith("transfer.ui.page."):
            address = f"صفحه جزئیات فایل اکسل ← «{label}» (templates/catalog/excel_detail.html)"
        else:
            address = (
                f"دیالوگ انتقال داده ← «{label}» "
                f"(templates/catalog/excel_detail.html#excel-transfer-dialog)"
            )
        out.append(
            _spec(
                key=key,
                label=label,
                address=address,
                category=SystemNamingKey.Category.TRANSFER,
                section_key="transfer_dialog_labels",
                table_key="transfer.ui",
                column_key=key.rsplit(".", 1)[-1],
                order=100 + chrome_order,
            )
        )
    return out


def _harvest_reports() -> list[dict[str, Any]]:
    from reports.columns import COLUMNS_BY_SOURCE, get_column_groups

    out: list[dict[str, Any]] = []
    groups = get_column_groups()
    harvested_sources: set[str] = set()
    for gi, group in enumerate(groups, start=1):
        gid = str(group.get("id") or "")
        glabel = str(group.get("label") or gid)
        harvested_sources.add(gid)
        out.append(
            _spec(
                key=f"report.source.{gid}",
                label=glabel,
                address=f"گزارش‌ها ← منبع «{glabel}»",
                category=SystemNamingKey.Category.REPORT,
                table_key=f"report.{gid}",
                order=gi,
            )
        )
        for i, pair in enumerate(group.get("columns") or [], start=1):
            if not pair:
                continue
            col_key, label = pair[0], pair[1]
            out.append(
                _spec(
                    key=f"report.col.{gid}.{col_key}",
                    label=str(label),
                    address=f"گزارش‌ها ← منبع {gid} ← ستون «{label}»",
                    category=SystemNamingKey.Category.COLUMN,
                    section_key="saved_reports",
                    table_key=f"report.{gid}",
                    column_key=str(col_key),
                    order=i,
                )
            )
    for source, cols in COLUMNS_BY_SOURCE.items():
        if source in harvested_sources:
            continue
        for i, tup in enumerate(cols, start=1):
            if len(tup) < 2:
                continue
            col_key, label = tup[0], tup[1]
            out.append(
                _spec(
                    key=f"report.col.{source}.{col_key}",
                    label=str(label),
                    address=f"گزارش‌ها ← منبع {source} ← ستون «{label}»",
                    category=SystemNamingKey.Category.COLUMN,
                    section_key="saved_reports",
                    table_key=f"report.{source}",
                    column_key=str(col_key),
                    order=i,
                )
            )
    return out


# Technical / internal columns that appear in some admin list_display but are
# not meaningful for end-user renaming in the ERP UI.
_ADMIN_SKIP_FIELDS = frozenset(
    {
        "id",
        "pk",
        "password",
        "last_login",
        "date_joined",
        "is_superuser",
        "is_staff",
        "user_permissions",
        "groups",
        "download_link",
    }
)


def _admin_display_label(model, model_admin, name: str) -> str:
    """Human label for a ModelAdmin list_display entry."""
    opts = model._meta
    try:
        field = opts.get_field(name)
        return str(getattr(field, "verbose_name", None) or name)
    except Exception:  # noqa: BLE001 — not a concrete DB field
        pass
    attr = getattr(model_admin, name, None)
    if attr is None:
        attr = getattr(model, name, None)
    short = getattr(attr, "short_description", None)
    if short:
        return str(short)
    return name.replace("_", " ")


def _harvest_admin_models() -> list[dict[str, Any]]:
    """Only columns visible in admin changelist (list_display), not every model field.

    Previously every Django model field (id, created_by, timestamps, …) was
    harvested — those do not appear in the main ERP screens, which confused users.
    """
    from django.contrib import admin as dj_admin

    from catalog.system_sections import build_system_groups

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in build_system_groups():
        for item in group.items:
            if not item.admin_changelist:
                continue
            # admin:app_model_changelist
            parts = item.admin_changelist.split(":")
            if len(parts) != 2:
                continue
            name = parts[1]
            if not name.endswith("_changelist"):
                continue
            model_label = name[: -len("_changelist")]  # catalog_moldoption
            if model_label in seen:
                continue
            seen.add(model_label)
            model = None
            model_admin = None
            for m, ma in dj_admin.site._registry.items():
                opts = m._meta
                if f"{opts.app_label}_{opts.model_name}" == model_label:
                    model = m
                    model_admin = ma
                    break
            if model is None or model_admin is None:
                continue
            opts = model._meta
            table_label = str(opts.verbose_name_plural or opts.verbose_name or model_label)
            table_key = f"admin.{opts.app_label}.{opts.model_name}"
            admin_path = f"/admin/{opts.app_label}/{opts.model_name}/"
            out.append(
                _spec(
                    key=f"admin.table.{opts.app_label}.{opts.model_name}",
                    label=table_label,
                    address=(
                        f"پنل مدیریت (ادمین) ← {table_label} ← فهرست "
                        f"({admin_path})"
                    ),
                    category=SystemNamingKey.Category.TABLE,
                    section_key=item.key,
                    table_key=table_key,
                    order=0,
                )
            )
            display = getattr(model_admin, "list_display", None) or ()
            order_i = 0
            for col in display:
                if not isinstance(col, str):
                    continue
                if col in _ADMIN_SKIP_FIELDS or col.startswith("__"):
                    continue
                order_i += 1
                label = _admin_display_label(model, model_admin, col)
                out.append(
                    _spec(
                        key=f"admin.field.{opts.app_label}.{opts.model_name}.{col}",
                        label=label,
                        address=(
                            f"پنل مدیریت (ادمین) ← {table_label} ← ستون فهرست «{label}» "
                            f"({admin_path} · list_display={col})"
                        ),
                        category=SystemNamingKey.Category.COLUMN,
                        section_key=item.key,
                        table_key=table_key,
                        column_key=col,
                        order=order_i,
                    )
                )
    return out


def _harvest_nav() -> list[dict[str, Any]]:
    from catalog.nav import NAV_SECTIONS

    out: list[dict[str, Any]] = []
    for si, section in enumerate(NAV_SECTIONS):
        out.append(
            _spec(
                key=f"nav.heading.{section.key}",
                label=section.title,
                address=f"{section.title}",
                category=SystemNamingKey.Category.SECTION,
                section_key=section.key,
                page_key=section.key,
                highlight=f'[data-nav-heading="{section.key}"]',
                order=si * 20,
            )
        )
        for ii, item in enumerate(section.items):
            highlight = f'[data-nav-key="{item.key}"]'
            address = f"{section.title} ← {item.title}"
            out.append(
                _spec(
                    key=f"nav.item.{item.key}",
                    label=item.title,
                    address=address,
                    category=SystemNamingKey.Category.SECTION,
                    section_key=item.key,
                    page_key=item.key,
                    url_name=item.url_name if item.kind != "logout" else "logout",
                    highlight=highlight,
                    order=si * 20 + ii + 1,
                )
            )
    return out


def harvest_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    specs.extend(_harvest_nav())
    specs.extend(_harvest_sections())
    specs.extend(_harvest_ui_columns())
    specs.extend(_harvest_transfer())
    specs.extend(_harvest_reports())
    try:
        specs.extend(_harvest_admin_models())
    except Exception:  # noqa: BLE001 — admin may be partially loaded in some contexts
        pass
    # de-dupe by key, keep first
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in specs:
        k = s["key"]
        if k in seen:
            continue
        seen.add(k)
        unique.append(s)
    return unique


SOURCE_CHOICES: list[tuple[str, str]] = [
    ("app", "صفحات کاربری و انتقال/گزارش (پیشنهادی)"),
    ("ui", "فقط صفحات کاربری"),
    ("transfer", "فقط انتقال داده"),
    ("report", "فقط گزارش‌ها"),
    ("section", "فقط بخش‌های سیستم"),
    ("admin", "فقط پنل مدیریت (ادمین)"),
    ("all", "همه منابع"),
]


def preview_map() -> dict[str, dict[str, Any]]:
    return {s["key"]: s for s in harvest_specs()}


_UI_TABLE_PAGES: dict[str, tuple[str, str]] = {
    "planning.plan_list": ("plan_list", "planning"),
    "production.history_list": ("production_history", "history"),
    "catalog.excel_list": ("excel_list", "excel"),
    "catalog.system_data_hub": ("system_data", "system"),
}


def _with_focus_on_return(return_path: str, key: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(return_path or "")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "focus_key"]
    if key:
        query.append(("focus_key", key))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _with_preview_query(path: str, *, key: str, highlight: str, return_path: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    ret = _with_focus_on_return(return_path, key)
    parts = urlsplit(path)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in {"naming_preview", "hk", "hl", "ret"}
    ]
    query.extend(
        [
            ("naming_preview", "1"),
            ("hk", key),
            ("hl", highlight or ""),
            ("ret", ret),
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class PreviewLinker:
    """Build preview shortcuts from stored keys — never re-harvests the registry."""

    def __init__(self, return_path: str):
        self.return_path = return_path
        self._system = None
        self._excel_path = None

    def href(self, row: SystemNamingKey) -> str:
        path, highlight = self._target(row.key, row.table_key, row.column_key, row.section_key)
        if not path:
            from django.urls import reverse

            path = reverse("system_data")
            highlight = highlight or ".topbar-title"
        return _with_preview_query(
            path, key=row.key, highlight=highlight, return_path=self.return_path
        )

    def _reverse(self, url_name: str, query: str = "") -> str:
        from django.urls import NoReverseMatch, reverse

        try:
            path = reverse(url_name)
        except NoReverseMatch:
            return ""
        if query:
            path = f"{path}?{query}"
        return path

    def _system_targets(self) -> dict[str, tuple[str, str]]:
        if self._system is not None:
            return self._system
        from django.urls import NoReverseMatch, reverse

        from catalog.system_sections import build_system_groups

        out: dict[str, tuple[str, str]] = {}
        hub = reverse("system_data")
        for group in build_system_groups():
            out[f"system.group.{group.key}"] = (hub, f'[data-acc-group="{group.key}"]')
            for item in group.items:
                path = ""
                if item.admin_changelist:
                    try:
                        path = reverse(item.admin_changelist)
                    except NoReverseMatch:
                        path = ""
                if not path and item.url_name:
                    path = self._reverse(item.url_name, item.url_query)
                elif path and item.url_query:
                    path = f"{path}?{item.url_query}"
                if path:
                    highlight = ".topbar-title, #content h1, main.content h1"
                    if item.admin_changelist:
                        highlight = "#result_list, .topbar-title"
                    out[f"system.section.{item.key}"] = (path, highlight)
                else:
                    out[f"system.section.{item.key}"] = (
                        hub,
                        f'[data-acc-item="{item.key}"]',
                    )
        self._system = out
        return out

    def _excel_detail_or_import(self) -> str:
        if self._excel_path is not None:
            return self._excel_path
        from django.urls import reverse

        from catalog.models import ExcelUpload

        obj = ExcelUpload.objects.only("pk").order_by("-id").first()
        if obj is not None:
            self._excel_path = reverse("excel_detail", args=[obj.pk])
        else:
            self._excel_path = reverse("excel_import")
        return self._excel_path

    def _target(
        self, key: str, table_key: str, column_key: str, section_key: str
    ) -> tuple[str, str]:
        from catalog.nav import NAV_SECTIONS, nav_item_by_key

        k = (key or "").strip()
        if k.startswith("nav.heading."):
            heading = k.rsplit(".", 1)[-1]
            for section in NAV_SECTIONS:
                if section.key != heading:
                    continue
                first = section.items[0] if section.items else None
                url_name = "dashboard"
                if first is not None and first.kind != "logout":
                    url_name = first.url_name
                return self._reverse(url_name), f'[data-nav-heading="{heading}"]'
            return self._reverse("dashboard"), f'[data-nav-heading="{heading}"]'
        if k.startswith("nav.item."):
            item_key = k.rsplit(".", 1)[-1]
            item = nav_item_by_key(item_key)
            highlight = f'[data-nav-key="{item_key}"]'
            if item is None or item.kind == "logout":
                return self._reverse("dashboard"), highlight
            return self._reverse(item.url_name, item.query), highlight
        if k.startswith("system."):
            return self._system_targets().get(k, ("", ""))
        table = (table_key or "").strip()
        if k.startswith("ui.table.") or table in _UI_TABLE_PAGES:
            page = _UI_TABLE_PAGES.get(table)
            if page is None and k.startswith("ui.table."):
                rest = k[len("ui.table.") :]
                table_id = rest.split(".col.", 1)[0]
                page = _UI_TABLE_PAGES.get(table_id)
                table = table_id
            if page:
                url_name, section = page
                col = (column_key or "").strip()
                if ".col." in k or col:
                    col = col or k.rsplit(".col.", 1)[-1]
                    highlight = (
                        f'[data-table-section="{section}"] thead th[data-col="{col}"]'
                    )
                else:
                    highlight = f'[data-table-section="{section}"] table'
                return self._reverse(url_name), highlight
        if k.startswith("transfer.ui.import.") or k.startswith("transfer.ui.page."):
            from django.urls import reverse

            if k.startswith("transfer.ui.import."):
                return reverse("excel_import"), "#excel-import-dialog"
            return reverse("excel_list"), ".topbar-title"
        if k.startswith("transfer."):
            path = self._excel_detail_or_import()
            if "excel_import" in path:
                return path, "#excel-dropzone, .topbar-title"
            if k.startswith("transfer.dest."):
                return path, "#transfer-destination, #excel-transfer-dialog"
            if k.startswith("transfer.level."):
                return path, "#transfer-level, #excel-transfer-dialog"
            if k.startswith("transfer.field."):
                return path, "#transfer-map-body, #excel-transfer-dialog"
            return path, "#excel-transfer-dialog"
        if k.startswith("report."):
            return self._reverse("report_list"), (
                '[data-nav-key="reports"], [data-table-section="reports"] table'
            )
        if k.startswith("admin.table.") or k.startswith("admin.field."):
            parts = k.split(".")
            # admin.table.app.model  /  admin.field.app.model.col
            if len(parts) >= 4:
                url_name = f"admin:{parts[2]}_{parts[3]}_changelist"
                if k.startswith("admin.field.") and len(parts) >= 5:
                    col = parts[4]
                    return self._reverse(url_name), (
                        f"#result_list thead th.column-{col}, "
                        f"th.column-{col}, #result_list"
                    )
                return self._reverse(url_name), "#result_list, .topbar-title"
        if section_key:
            mapped = self._system_targets().get(f"system.section.{section_key}")
            if mapped:
                return mapped
        return self._reverse("dashboard"), ".topbar-title"


def preview_href(
    key: str,
    *,
    return_path: str,
    table_key: str = "",
    column_key: str = "",
    section_key: str = "",
) -> str:
    """Single-key helper for tests; listing uses PreviewLinker so work is shared."""
    linker = PreviewLinker(return_path)
    path, highlight = linker._target(key, table_key, column_key, section_key)
    if not path:
        from django.urls import reverse

        path = reverse("system_data")
        highlight = highlight or ".topbar-title"
    return _with_preview_query(
        path, key=key, highlight=highlight, return_path=return_path
    )


def key_source(key: str) -> str:
    """Map a naming key to a high-level source bucket."""
    k = (key or "").strip()
    if k.startswith("admin."):
        return "admin"
    if k.startswith("nav."):
        return "section"
    if k.startswith("ui."):
        return "ui"
    if k.startswith("transfer."):
        return "transfer"
    if k.startswith("report."):
        return "report"
    if k.startswith("system."):
        return "section"
    return "other"


@transaction.atomic
def sync_naming_registry(*, refresh_defaults: bool = False) -> dict[str, int]:
    """Insert missing keys; optionally refresh default_label from harvest.

    Never overwrites a user-edited ``label`` unless it still matches the old default.
    Non-custom keys that disappear from harvest are marked inactive (e.g. old
    admin.* technical fields that are no longer collected).
    """
    specs = harvest_specs()
    harvested_keys = {s["key"] for s in specs}
    existing = {r.key: r for r in SystemNamingKey.objects.all()}
    created = updated = skipped = deactivated = 0
    for spec in specs:
        row = existing.get(spec["key"])
        if row is None:
            SystemNamingKey.objects.create(
                key=spec["key"],
                label=spec["label"],
                default_label=spec["default_label"],
                address=spec["address"],
                category=spec["category"],
                section_key=spec["section_key"],
                table_key=spec["table_key"],
                column_key=spec["column_key"],
                order=spec["order"],
                is_custom=False,
                is_active=True,
                is_key=bool(spec.get("is_key")),
            )
            created += 1
            continue
        fields: list[str] = []
        # Keep address/category/table metadata current for non-custom rows
        if not row.is_custom:
            for attr in ("address", "category", "section_key", "table_key", "column_key", "order"):
                new_val = spec[attr]
                if getattr(row, attr) != new_val:
                    setattr(row, attr, new_val)
                    fields.append(attr)
            if refresh_defaults or not row.default_label or spec["key"].startswith(
                ("system.group.", "system.section.")
            ):
                if row.default_label != spec["default_label"]:
                    # If label was still equal to old default, move it with the default
                    if row.label == row.default_label or not row.default_label:
                        row.label = spec["label"]
                        fields.append("label")
                    row.default_label = spec["default_label"]
                    fields.append("default_label")
            if fields:
                row.save(update_fields=list(dict.fromkeys(fields + ["updated_at"])))
                updated += 1
            else:
                skipped += 1
        else:
            skipped += 1

    for key, row in existing.items():
        if row.is_custom or not row.is_active:
            continue
        if key not in harvested_keys:
            row.is_active = False
            row.save(update_fields=["is_active", "updated_at"])
            deactivated += 1

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "deactivated": deactivated,
        "total": len(specs),
    }


def refresh_system_hub_labels() -> int:
    """Update system.group.* / system.section.* labels when still at their old default."""
    from catalog.system_sections import build_system_groups

    updated = 0
    for group in build_system_groups():
        pairs = [(f"system.group.{group.key}", group.title)]
        pairs.extend(
            (f"system.section.{item.key}", item.title) for item in group.items
        )
        for key, title in pairs:
            row = SystemNamingKey.objects.filter(key=key, is_custom=False).first()
            if row is None:
                continue
            if row.label == row.default_label or not row.default_label:
                if row.label != title or row.default_label != title:
                    row.label = title
                    row.default_label = title
                    row.save(update_fields=["label", "default_label", "updated_at"])
                    updated += 1
            elif row.default_label != title:
                row.default_label = title
                row.save(update_fields=["default_label", "updated_at"])
                updated += 1
    return updated


def ensure_registry_seeded() -> dict[str, int] | None:
    if SystemNamingKey.objects.exists():
        return None
    return sync_naming_registry()
