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
        self.assertContains(page, "system-acc-item-title")
        self.assertNotContains(page, "system-acc-row")
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
        self.assertContains(layout, "بدنه جدول")
        cfg = self.client.get(reverse("system_menu_config"), {"menu": "planning"})
        self.assertEqual(cfg.status_code, 200)
        self.assertContains(cfg, "برنامه‌ریزی هفتگی")

    def test_ui_assets_and_current_factory_report_pages_render(self):
        page = self.client.get(reverse("system_data"))
        html = page.content.decode()
        self.assertIn("css/app.css", html)
        self.assertIn("js/table_nav.js", html)
        self.assertIn("js/table_layout.js", html)
        self.assertIn("js/naming_preview.js", html)
        self.assertEqual(self.client.get(reverse("pipe_calc")).status_code, 200)
        self.assertEqual(self.client.get(reverse("report_list")).status_code, 200)

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
        from django.contrib.auth import get_user_model

        cls.User = get_user_model()
        cls.admin = cls.User.objects.create_superuser("admin", "admin@example.test", "erp12345")

    def test_table_layout_defaults_lock_non_reports(self):
        from catalog.models import TableLayoutSettings

        settings = TableLayoutSettings.load()
        self.assertFalse(settings.is_width_locked("reports"))
        self.assertTrue(settings.is_width_locked("product_data"))
        self.assertTrue(settings.is_width_locked("planning"))
        self.assertTrue(settings.is_width_locked("systemic"))

    def _family_payload(self, family="production_calculation", **overrides):
        data = {
            "family": family, "row_height_px": "34", "header_height_px": "34",
            "body_font_size_px": "12.5", "header_font_size_px": "12",
            "cell_padding_vertical_px": "5", "cell_padding_horizontal_px": "8",
            "header_border": "show", "col_border": "show", "row_border": "show",
            "header_color": "#c7d7ea", "header_alpha": "100",
            "row_selected_color": "#dbeafe", "row_selected_alpha": "100",
            "cell_outline_color": "#2563eb", "cell_outline_alpha": "100",
            "cell_fill_color": "#ffffff", "cell_fill_alpha": "100", "marquee": "1",
        }
        data.update(overrides)
        return data

    def test_business_family_tabs_panels_and_readonly_impact(self):
        self.client.login(username="admin", password="erp12345")
        url = reverse("system_table_layout")
        response = self.client.get(url, {"family": "production_calculation"})
        self.assertEqual(response.status_code, 200)
        for label in ("فهرست‌ها و مدیریت", "محاسبات تولید", "جداول عملیاتی", "گزارشات"):
            self.assertContains(response, label)
        for old in ("عمومی", "فشرده", "گزارش / محاسبات", "ورود اطلاعات"):
            self.assertNotContains(response, old)
        self.assertContains(response, "سرستون")
        self.assertContains(response, "بدنه جدول")
        self.assertContains(response, 'name="header_font_size_px"')
        self.assertContains(response, 'name="body_font_size_px"')
        self.assertContains(response, "برنامه دپو و نیاز تولید")
        html = response.content.decode()
        self.assertNotIn("profile_assignment", html)
        self.assertIn("<details", html)

    def test_family_save_is_isolated_and_preserves_compatibility_data(self):
        from catalog.models import TableLayoutSettings
        self.client.login(username="admin", password="erp12345")
        settings = TableLayoutSettings.objects.create(section_layouts={
            "all": {"row_height_px": 47}, "planning": {"row_height_px": 41},
            "profiles": {"compact": {"row_height_px": 26}}, "unknown": {"keep": True},
            "families": {"report_output": {"row_height_px": 45}},
        })
        response = self.client.post(reverse("system_table_layout"), self._family_payload(body_font_size_px="13.7", header_font_size_px="11.4"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-table-family="production-calculation"', response.json()["css"])
        settings.refresh_from_db()
        self.assertEqual(settings.section_layouts["all"], {"row_height_px": 47})
        self.assertEqual(settings.section_layouts["planning"], {"row_height_px": 41})
        self.assertEqual(settings.section_layouts["profiles"], {"compact": {"row_height_px": 26}})
        self.assertEqual(settings.section_layouts["unknown"], {"keep": True})
        self.assertEqual(settings.section_layouts["families"]["report_output"], {"row_height_px": 45})
        saved = settings.section_layouts["families"]["production_calculation"]
        self.assertEqual(saved["body_font_size_px"], 13.7)
        self.assertEqual(saved["header_font_size_px"], 11.4)

    def test_invalid_family_value_is_rejected_without_persistence(self):
        from catalog.models import TableLayoutSettings
        self.client.login(username="admin", password="erp12345")
        settings = TableLayoutSettings.objects.create(section_layouts={"sentinel": {"kept": True}})
        before = settings.section_layouts
        response = self.client.post(reverse("system_table_layout"), self._family_payload(row_height_px="NaN"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        settings.refresh_from_db()
        self.assertEqual(settings.section_layouts, before)

    def test_family_reset_is_isolated(self):
        from catalog.models import TableLayoutSettings
        from catalog.table_layout import FAMILY_DEFAULTS
        self.client.login(username="admin", password="erp12345")
        original = {"all": {"row_height_px": 48}, "profiles": {"compact": {"row_height_px": 26}}, "legacy": {"x": 1}, "families": {"report_output": {"row_height_px": 44}}}
        settings = TableLayoutSettings.objects.create(section_layouts=original)
        response = self.client.post(reverse("system_table_layout"), {"family": "production_calculation", "family_reset": "production_calculation"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        settings.refresh_from_db()
        saved = settings.section_layouts["families"]["production_calculation"]
        for key, value in FAMILY_DEFAULTS["production_calculation"].items():
            self.assertEqual(saved[key], value)
        self.assertEqual(settings.section_layouts["all"], {"row_height_px": 48})
        self.assertEqual(settings.section_layouts["profiles"], original["profiles"])
        self.assertEqual(settings.section_layouts["legacy"], {"x": 1})
        self.assertEqual(settings.section_layouts["families"]["report_output"], {"row_height_px": 44})

    def test_family_save_css_reload_and_reset_return_effective_values(self):
        from catalog.models import TableLayoutSettings
        from catalog.table_layout import FAMILY_DEFAULTS, normalize_family_layouts

        self.client.login(username="admin", password="erp12345")
        original = {
            "all": {"row_height_px": 48},
            "profiles": {"compact": {"row_height_px": 26}},
            "legacy": {"keep": True},
            "families": {"operational": {"row_height_px": 41}},
        }
        settings = TableLayoutSettings.objects.create(section_layouts=original)
        url = reverse("system_table_layout")

        saved = self.client.post(
            url,
            self._family_payload(body_font_size_px="20", header_font_size_px="18", row_height_px="70", header_height_px="70", header_color="#ff00aa"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(saved.status_code, 200)
        payload = saved.json()
        self.assertEqual(payload["layout"]["body_font_size_px"], 20)
        self.assertEqual(payload["layout"]["header_font_size_px"], 18)
        self.assertEqual(payload["layout"]["row_height_px"], 70)
        self.assertEqual(payload["layout"]["header_height_px"], 70)
        self.assertEqual(payload["layout"]["header_color"], "#ff00aa")
        production_css = payload["css"].split('[data-table-family="production-calculation"]', 1)[1].split('[data-table-family="operational"]', 1)[0]
        self.assertIn("--table-family-body-font-size:20px", production_css)
        self.assertIn("--table-family-header-font-size:18px", production_css)
        self.assertIn("--table-row-height:70px", production_css)
        self.assertIn("--table-header-height:70px", production_css)
        self.assertIn("--table-header-bg:rgba(255,0,170,1.00)", production_css)
        self.assertIn("tbody tr{height:var(--table-row-height) !important", production_css)
        self.assertIn("thead tr{height:var(--table-header-height) !important", production_css)

        settings.refresh_from_db()
        effective = normalize_family_layouts(settings.section_layouts)
        self.assertEqual(effective["production_calculation"]["body_font_size_px"], 20)
        self.assertEqual(effective["production_calculation"]["row_height_px"], 70)
        self.assertEqual(effective["production_calculation"]["header_height_px"], 70)
        self.assertEqual(effective["production_calculation"]["header_color"], "#ff00aa")
        reloaded = self.client.get(url, {"family": "production_calculation"})
        self.assertContains(reloaded, 'name="body_font_size_px" min="9" max="24" step="0.1" value="20.0"')

        reset = self.client.post(
            url,
            {"family": "production_calculation", "family_reset": "production_calculation"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(reset.status_code, 200)
        reset_payload = reset.json()
        self.assertEqual(reset_payload["layout"]["body_font_size_px"], 12.5)
        self.assertEqual(reset_payload["layout"]["row_height_px"], 34)
        self.assertEqual(reset_payload["layout"]["header_height_px"], 34)
        reset_css = reset_payload["css"].split('[data-table-family="production-calculation"]', 1)[1].split('[data-table-family="operational"]', 1)[0]
        self.assertIn("--table-family-body-font-size:12.5px", reset_css)
        self.assertIn("--table-row-height:34px", reset_css)
        self.assertIn("--table-header-height:34px", reset_css)
        self.assertIn("--table-header-bg:rgba(199,215,234,1.00)", reset_css)
        self.assertNotIn("--table-family-body-font-size:20px", reset_css)

        settings.refresh_from_db()
        self.assertEqual(settings.section_layouts["all"], original["all"])
        self.assertEqual(settings.section_layouts["profiles"], original["profiles"])
        self.assertEqual(settings.section_layouts["legacy"], original["legacy"])
        self.assertEqual(settings.section_layouts["families"]["operational"], original["families"]["operational"])
        for key, value in FAMILY_DEFAULTS["production_calculation"].items():
            self.assertEqual(settings.section_layouts["families"]["production_calculation"][key], value)

    def test_family_font_edits_preserve_the_other_font_value(self):
        from catalog.models import TableLayoutSettings

        self.client.login(username="admin", password="erp12345")
        settings = TableLayoutSettings.objects.create()
        url = reverse("system_table_layout")
        first = self.client.post(url, self._family_payload(header_font_size_px="18", body_font_size_px="12.5"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(first.status_code, 200)
        second = self.client.post(url, self._family_payload(header_font_size_px="18", body_font_size_px="20"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(second.status_code, 200)
        settings.refresh_from_db()
        saved = settings.section_layouts["families"]["production_calculation"]
        self.assertEqual(saved["header_font_size_px"], 18)
        self.assertEqual(saved["body_font_size_px"], 20)

    def test_ajax_ui_applies_normalized_save_and_reset_values(self):
        self.client.login(username="admin", password="erp12345")
        response = self.client.get(reverse("system_table_layout"))
        html = response.content.decode()
        self.assertIn("function applyFamilyValuesToForm(v)", html)
        self.assertIn("applyFamilyValuesToForm(x.j.layout)", html)
        self.assertIn("e.submitter||lastSubmitter", html)

    def test_family_settings_desktop_grid_and_collapsed_impact_structure(self):
        self.client.login(username="admin", password="erp12345")
        response = self.client.get(reverse("system_table_layout"))
        html = response.content.decode()
        self.assertIn('class="family-layout-panels"', html)
        self.assertEqual(html.count('class="layout-page-fields"'), 2)
        self.assertIn('<details class="family-impact">', html)
        self.assertNotIn('<details class="family-impact" open', html)

    def test_table_layout_page_save(self):
        from django.urls import reverse
        from catalog.models import TableLayoutSettings

        self.client.login(username="admin", password="erp12345")
        url = reverse("system_table_layout")
        get = self.client.get(url)
        self.assertEqual(get.status_code, 200)
        self.assertContains(get, "قفل عرض ستون")
        self.assertContains(get, "سرستون")
        self.assertContains(get, "بدنه جدول")
        self.assertContains(get, "جداول تحت تأثیر")
        body = self.client.get(url, {"family": "list_management"})
        self.assertContains(body, "ارتفاع ردیف")
        self.assertEqual(body.content.decode().count('name="row_height_px"'), 1)
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
        self.assertTrue(settings.is_width_locked("product_data"))

    def test_table_layout_ajax_save_returns_css(self):
        from django.urls import reverse
        from catalog.models import TableLayoutSettings

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
        self.assertIn('data-table-section="planning"', payload["css"])
        self.assertEqual(payload["layout"]["row_height_px"], 52)
        settings = TableLayoutSettings.load()
        self.assertEqual(settings.layouts_map()["planning"]["row_height_px"], 52)
        page = self.client.get(reverse("plan_list"))
        self.assertIn("--table-row-height:52px", page.content.decode())

    def test_layout_css_does_not_paint_page_background(self):
        import re
        from catalog.table_layout import css_for_layouts, default_layout

        css = css_for_layouts({"planning": default_layout("planning")})
        self.assertIn('[data-table-section="planning"]{--table-row-height:', css)
        for block in css.split("}"):
            if "background:var(--table-row-selected)" not in block and "background:var(--table-header-bg)" not in block:
                continue
            selector = block.split("{", 1)[0]
            self.assertIn(" ", selector)
            self.assertNotEqual(selector.strip(), '[data-table-section="planning"]')
            for part in selector.split(","):
                part = part.strip()
                self.assertTrue(
                    re.search(r"(table|th|td|thead|tr)", part),
                    msg=f"page-level background selector leaked: {part}",
                )

    def test_legacy_preferences_are_accepted_and_retained(self):
        from django.urls import reverse
        from catalog.models import TableLayoutSettings

        self.client.login(username="admin", password="erp12345")
        url = reverse("system_table_layout")
        settings = TableLayoutSettings.objects.create(
            row_height_px=36,
            section_layouts={"planning": {"row_height_px": 41, "col_border": True}},
        )
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
        settings.refresh_from_db()
        self.assertIn("planning", settings.section_layouts)
        self.assertIn("all", settings.section_layouts)
        layout = settings.layouts_map()["planning"]
        self.assertEqual(layout["row_height_px"], 8)
        self.assertFalse(layout["col_border"])
        self.assertTrue(layout["row_border"])
        self.assertTrue(layout["width_locked"])
        self.assertEqual(settings.section_layouts["all"]["row_height_px"], 8)
        self.assertFalse(settings.section_layouts["all"]["col_border"])

    def test_column_border_toggle_reaches_planning_and_history_pages(self):
        from django.urls import reverse
        from catalog.table_layout import COLUMN_BORDER_COLOR

        self.client.login(username="admin", password="erp12345")
        layout_url = reverse("system_table_layout")
        show_rule = (
            f"background-image:linear-gradient({COLUMN_BORDER_COLOR},{COLUMN_BORDER_COLOR}) !important"
        )
        planning_show = '[data-table-section="planning"] table th:not(:last-child)'
        history_show = '[data-table-section="history"] table th:not(:last-child)'

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
        plans = self.client.get(reverse("plan_list"))
        self.assertEqual(plans.status_code, 200)
        plans_html = plans.content.decode()
        self.assertIn('data-table-section="planning"', plans_html)
        self.assertIn(planning_show, plans_html)
        self.assertIn(show_rule, plans_html)
        self.assertIn(
            f"background-image:linear-gradient({COLUMN_BORDER_COLOR},{COLUMN_BORDER_COLOR}) !important",
            plans_html,
        )

        history = self.client.get(reverse("production_history"))
        self.assertEqual(history.status_code, 200)
        history_html = history.content.decode()
        self.assertIn('data-table-section="history"', history_html)
        self.assertIn(history_show, history_html)

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
        self.assertNotIn(planning_show, plans_hidden)
        self.assertIn('[data-table-section="planning"] table th,', plans_hidden)
        self.assertIn("border-left:none !important", plans_hidden)
        self.assertIn("border-inline-end:none !important", plans_hidden)
        self.assertIn("background-image:none !important", plans_hidden)
        self.assertNotIn(
            '[data-table-section="planning"] table th:not(:last-child)',
            plans_hidden,
        )
        # Unified settings intentionally apply the same hidden-border rule to history.
        self.assertNotIn(history_show, plans_hidden)
