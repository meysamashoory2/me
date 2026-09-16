"""System data hub must expose real configuration — not app-page shortcuts."""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from catalog.system_sections import build_system_groups


def _accordion_hrefs(html: str) -> list[str]:
    """Collect hrefs only from the system accordion (exclude sidebar/nav)."""
    match = re.search(
        r'<div class="system-accordion"[^>]*>(.*?)</div>\s*(?:</div>\s*)?(?:{%|\n\s*</main>|</main>)',
        html,
        flags=re.S,
    )
    chunk = match.group(1) if match else ""
    if not chunk:
        # Fallback: between accordion id and end of panel
        start = html.find('id="system-accordion"')
        end = html.find("</main>", start)
        chunk = html[start:end] if start >= 0 and end > start else html
    return re.findall(r'href="([^"]+)"', chunk)


class SystemDataNoShortcutsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.filter(username="admin").first()
        if self.user is None:
            self.user = User.objects.create_superuser("admin", "a@b.c", "erp12345")
        self.client = Client(HTTP_HOST="localhost")
        assert self.client.login(username="admin", password="erp12345")

    def test_hub_avoids_operational_page_urls(self):
        page = self.client.get(reverse("system_data"))
        self.assertEqual(page.status_code, 200)
        hrefs = _accordion_hrefs(page.content.decode())

        forbidden_prefixes = [
            reverse("product_data"),
            reverse("pipe_calc"),
            reverse("excel_list"),
            reverse("excel_import"),
            reverse("systemic_intelligence"),
            reverse("plan_list"),
            reverse("program_list"),
            reverse("production_history"),
            reverse("dashboard"),
        ]
        for href in hrefs:
            for bad in forbidden_prefixes:
                self.assertFalse(
                    href == bad or href.startswith(bad + "?") or href.startswith(bad + "/"),
                    msg=f"accordion shortcut {href} matches operational {bad}",
                )

    def test_hub_uses_admin_for_former_shortcuts(self):
        page = self.client.get(reverse("system_data"))
        hrefs = _accordion_hrefs(page.content.decode())
        joined = "\n".join(hrefs)
        self.assertIn(reverse("admin:catalog_excelupload_changelist"), joined)
        self.assertIn(reverse("admin:catalog_flexibledataset_changelist"), joined)
        self.assertIn(reverse("admin:catalog_flexiblerow_changelist"), joined)
        self.assertIn(reverse("admin:catalog_pipeproductline_changelist"), joined)
        self.assertTrue(
            all("/planning/systemic" not in h for h in hrefs),
            msg="systemic intelligence must not appear as a system-data section link",
        )

    def test_registry_items_prefer_admin_or_system_config_ui(self):
        """Every section either has admin changelist or a dedicated system config URL."""
        system_config_urls = {
            "system_naming_keys",
            "system_table_columns",
            "system_table_layout",
            "system_menu_config",
            "system_planning_chrome",
            "planning_process_list",
            "backup_center",
        }
        for group in build_system_groups():
            for item in group.items:
                if item.admin_changelist:
                    self.assertTrue(
                        item.admin_changelist.startswith("admin:"),
                        msg=f"{item.key} admin_changelist invalid",
                    )
                    continue
                self.assertIn(
                    item.url_name,
                    system_config_urls,
                    msg=f"{item.key} is neither admin nor allowed system-config UI",
                )

    def test_report_parameter_defs_in_system_data(self):
        keys = [i.key for g in build_system_groups() for i in g.items]
        self.assertIn("report_parameter_defs", keys)
        item = next(
            i
            for g in build_system_groups()
            for i in g.items
            if i.key == "report_parameter_defs"
        )
        self.assertEqual(item.admin_changelist, "admin:reports_reportparameterdef_changelist")
        page = self.client.get(reverse("system_data"))
        self.assertContains(page, "تعریف پارامترهای گزارش")
        self.assertContains(page, reverse("admin:reports_reportparameterdef_changelist"))

    def test_hub_new_groups_and_naming_page(self):
        page = self.client.get(reverse("system_data"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "عناوین و ساختار نمایش")
        self.assertContains(page, "نام‌گذاری عناوین سیستم")
        self.assertContains(page, "تنظیمات جداول")
        self.assertContains(page, 'name="system-hub"')
        self.assertContains(page, "system-acc-item-link")
        self.assertNotContains(page, "مشاهده / ویرایش")
        self.assertContains(page, "پیکربندی منوها")
        self.assertContains(page, "پشتیبان‌گیری استاندارد")
        naming = self.client.get(reverse("system_naming_keys"))
        self.assertEqual(naming.status_code, 200)
        self.assertContains(naming, "نوع کلید")
        self.assertContains(naming, "عنوان نمایشی")
        self.assertContains(naming, "سرتیتر")
        self.assertIn("naming_preview=1", naming.content.decode())
        self.assertNotIn('<span class="naming-address">', naming.content.decode())
        layout = self.client.get(reverse("system_table_layout"))
        self.assertContains(layout, "سرستون جداول")
        self.assertContains(layout, "بدنه جداول")
        self.assertNotContains(layout, "dlg.close")
        cfg = self.client.get(reverse("system_menu_config"), {"menu": "planning"})
        self.assertEqual(cfg.status_code, 200)
        self.assertContains(cfg, "برنامه‌ریزی هفتگی")

    def test_flexible_dataset_admin_loads(self):
        resp = self.client.get(reverse("admin:catalog_flexibledataset_changelist"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "بازگشت به مدیریت داده‌ها")

    def test_section_redirect_uses_admin_not_app(self):
        resp = self.client.get(reverse("system_section", args=["pipe_calc"]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/catalog/pipeproductline/", resp["Location"])

        resp2 = self.client.get(reverse("system_section", args=["product_data_hub"]))
        self.assertEqual(resp2.status_code, 302)
        self.assertIn("/admin/catalog/flexibledataset/", resp2["Location"])


class TableLayoutSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        from django.contrib.auth import get_user_model

        call_command("seed_demo")
        cls.User = get_user_model()
        cls.admin = cls.User.objects.get(username="admin")

    def test_table_layout_defaults_lock_non_reports(self):
        from catalog.models import TableLayoutSettings

        settings = TableLayoutSettings.load()
        self.assertTrue(settings.is_width_locked("reports"))
        self.assertTrue(settings.is_width_locked("product_data"))
        self.assertTrue(settings.is_width_locked("planning"))
        self.assertTrue(settings.is_width_locked("systemic"))

    def test_table_layout_page_save(self):
        from django.urls import reverse
        from catalog.models import TableLayoutSettings

        self.client.login(username="admin", password="erp12345")
        url = reverse("system_table_layout")
        get = self.client.get(url)
        self.assertEqual(get.status_code, 200)
        self.assertContains(get, "قفل کردن ستون")
        self.assertContains(get, "سرستون جداول")
        self.assertContains(get, "بدنه جداول")
        self.assertContains(get, "layout-tint-num")
        self.assertContains(get, "پیش‌فرض")
        self.assertContains(get, 'name="layout_reset"')
        self.assertNotContains(get, "dlg.close")
        body = self.client.get(url, {"tab": "body"})
        self.assertContains(body, "ارتفاع ردیف")
        resp = self.client.post(
            url,
            {
                "tab": "body",
                "layout_part": "body",
                "row_height_px": "44",
                "col_border": "show",
                "row_border": "show",
            },
        )
        self.assertEqual(resp.status_code, 302)
        settings = TableLayoutSettings.load()
        self.assertEqual(settings.clamped_row_height(), 44)
        self.assertTrue(settings.is_width_locked("reports"))

    def test_table_layout_ajax_save_returns_css(self):
        from django.urls import reverse
        from catalog.models import TableLayoutSettings
        from catalog.table_layout import GLOBAL_LAYOUT_KEY

        self.client.login(username="admin", password="erp12345")
        url = reverse("system_table_layout")
        resp = self.client.post(
            url,
            {
                "tab": "body",
                "layout_part": "body",
                "row_height_px": "52",
                "col_border": "show",
                "row_border": "show",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertIn("--table-row-height:52px", payload["css"])
        self.assertIn("main.content", payload["css"])
        self.assertNotIn('data-table-surface="list"', payload["css"])
        settings = TableLayoutSettings.load()
        self.assertEqual(settings.layouts_map()[GLOBAL_LAYOUT_KEY]["row_height_px"], 52)
        page = self.client.get(reverse("plan_list"))
        self.assertIn("--table-row-height:52px", page.content.decode())
        history = self.client.get(reverse("production_history"))
        self.assertIn("--table-row-height:52px", history.content.decode())

    def test_table_layout_reset_restores_default_header_color(self):
        from django.urls import reverse
        from catalog.models import TableLayoutSettings
        from catalog.table_layout import DEFAULT_HEADER_COLOR, GLOBAL_LAYOUT_KEY

        self.client.login(username="admin", password="erp12345")
        url = reverse("system_table_layout")
        self.client.post(
            url,
            {
                "tab": "header",
                "layout_part": "header",
                "header_height_px": "90",
                "header_border": "show",
                "header_color": "#ff00aa",
                "header_alpha": "30",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        resp = self.client.post(
            url,
            {
                "tab": "header",
                "layout_reset": "header",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["layout"]["header_color"], DEFAULT_HEADER_COLOR)
        self.assertEqual(payload["layout"]["header_alpha"], 100)
        self.assertEqual(payload["layout"]["header_height_px"], 36)
        settings = TableLayoutSettings.load()
        self.assertEqual(settings.layouts_map()[GLOBAL_LAYOUT_KEY]["header_color"], DEFAULT_HEADER_COLOR)

    def test_layout_css_does_not_paint_page_background(self):
        import re
        from catalog.table_layout import CSS_HOST, css_for_layouts, default_layout

        css = css_for_layouts({"all": default_layout("all")})
        self.assertIn(f"{CSS_HOST}{{--table-row-height:", css)
        for block in css.split("}"):
            if "var(--table-row-selected)" not in block and "var(--table-header-bg)" not in block:
                continue
            if "transparent" in block:
                continue
            selector = block.split("{", 1)[0]
            self.assertIn(" ", selector)
            self.assertNotEqual(selector.strip(), CSS_HOST)
            for part in selector.split(","):
                part = part.strip()
                self.assertTrue(
                    re.search(r"(table|th|td|thead|tr|accordion|sidebar|nav)", part),
                    msg=f"page-level background selector leaked: {part}",
                )

    def test_table_layout_save_other_section(self):
        from django.urls import reverse
        from catalog.models import TableLayoutSettings
        from catalog.table_layout import GLOBAL_LAYOUT_KEY

        self.client.login(username="admin", password="erp12345")
        url = reverse("system_table_layout")
        resp = self.client.post(
            url,
            {
                "tab": "body",
                "layout_part": "body",
                "row_height_px": "8",
                "col_border": "hide",
                "row_border": "show",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("tab=body", resp["Location"])
        settings = TableLayoutSettings.load()
        layout = settings.layouts_map()[GLOBAL_LAYOUT_KEY]
        self.assertEqual(layout["row_height_px"], 8)
        self.assertFalse(layout["col_border"])
        self.assertTrue(layout["row_border"])
        get = self.client.get(url, {"tab": "body"})
        self.assertContains(get, 'value="8"')
        self.assertContains(get, 'name="col_border" value="hide"')

    def test_column_border_toggle_reaches_planning_and_history_pages(self):
        from django.urls import reverse
        from catalog.table_layout import COLUMN_BORDER_COLOR, CSS_HOST

        self.client.login(username="admin", password="erp12345")
        layout_url = reverse("system_table_layout")
        show_rule = (
            f"background-image:linear-gradient({COLUMN_BORDER_COLOR},{COLUMN_BORDER_COLOR}) !important"
        )
        show_sel = f"{CSS_HOST} table.table th:not(:last-child)"

        self.client.post(
            layout_url,
            {
                "tab": "body",
                "layout_part": "body",
                "row_height_px": "36",
                "col_border": "show",
                "row_border": "show",
                "header_border": "show",
            },
        )
        plans_html = self.client.get(reverse("plan_list")).content.decode()
        self.assertIn(show_sel, plans_html)
        self.assertIn(show_rule, plans_html)
        history_html = self.client.get(reverse("production_history")).content.decode()
        self.assertIn(show_sel, history_html)

        self.client.post(
            layout_url,
            {
                "tab": "body",
                "layout_part": "body",
                "row_height_px": "36",
                "col_border": "hide",
                "row_border": "show",
                "header_border": "show",
            },
        )
        plans_hidden = self.client.get(reverse("plan_list")).content.decode()
        self.assertNotIn(show_sel, plans_hidden)
        self.assertIn("border-left:none !important", plans_hidden)
        history_hidden = self.client.get(reverse("production_history")).content.decode()
        self.assertNotIn(show_sel, history_hidden)

    def test_system_hub_accordion_is_not_a_data_table(self):
        from django.urls import reverse

        self.client.login(username="admin", password="erp12345")
        html = self.client.get(reverse("system_data")).content.decode()
        self.assertIn("system-acc-item", html)
        self.assertNotIn('class="table table-compact table-plan-list"', html)
        start = html.find('id="system-accordion"')
        chunk = html[start : html.find("</main>", start)]
        self.assertNotIn("<table", chunk)
