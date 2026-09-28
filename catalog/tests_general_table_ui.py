"""General-table acceptance contracts, using only Django's test database.

Optionally export synthetic rendered pages for scripts/tests/general_tables.cjs
by setting ERP_TABLE_UI_FIXTURES to an absolute JSON path outside the repository.
"""
import json
import os
from pathlib import Path

import jdatetime
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, ProductGroup, ProductSubGroup, TableLayoutSettings
from catalog.table_layout import css_for_families, normalize_family_layouts
from planning.models import WeeklyPlan
from reports.models import PrintForm, SavedReport


class GeneralTableAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("ui-test", "ui@example.test", "test-only")
        group = ProductGroup.objects.create(name="گروه آزمایشی")
        subgroup = ProductSubGroup.objects.create(group=group, name="زیرگروه آزمایشی")
        for index in range(8):
            Product.objects.create(code=f"UI-{index}", name="نام طولانی محصول برای آزمون جدول " * 3, subgroup=subgroup)
        WeeklyPlan.objects.create(program_number="UI-PLAN", date=jdatetime.date(1405, 1, 1), created_by=cls.user)
        SavedReport.objects.create(owner=cls.user, title="عنوان بلند گزارش برای آزمون نمایش " * 3, number=1)
        PrintForm.objects.create(owner=cls.user, title="عنوان بلند فرم برای آزمون نمایش " * 3, description="توضیح بلند آزمایشی " * 5, number=1)
        TableLayoutSettings.objects.create()

    def setUp(self):
        self.client.force_login(self.user)

    def test_real_general_surfaces_and_browser_fixtures(self):
        pages = {}
        for name in ("plan_list", "systemic_intelligence", "production_list", "report_list",
                     "print_form_list", "product_data", "excel_list",
                     "admin:catalog_product_changelist", "user_management"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name), follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-table-type="general"')
                self.assertContains(response, '--table-family-body-font-size:14px')
                pages[name] = {"url": reverse(name), "html": response.content.decode()}
        for tab in ("materials", "capacity", "variance", "exceptions"):
            response = self.client.get(reverse("systemic_intelligence"), {"tab": tab})
            self.assertContains(response, 'data-table-type="general"')
        for key in ("general", "calculation", "report"):
            response = self.client.get(reverse("system_table_layout"), {"family": key})
            self.assertContains(response, f'data-table-type="{key}"')
            self.assertNotContains(response, "پیش‌نمایش مؤثر")
            self.assertNotContains(response, "پس از اعمال یا بازگشت به پیش‌فرض، همین تنظیمات مؤثر نمایش داده می‌شوند.")
            self.assertContains(response, 'type="text" inputmode="decimal" dir="ltr"')
            self.assertContains(response, 'output dir="ltr"')
            pages[f"settings_{key}"] = {"url": reverse("system_table_layout") + f"?family={key}", "html": response.content.decode()}
        destination = os.environ.get("ERP_TABLE_UI_FIXTURES")
        if destination:
            target = Path(destination).resolve()
            self.assertTrue(Path(destination).is_absolute())
            self.assertFalse(target.is_relative_to(settings.BASE_DIR.resolve()))
            cases = {}
            for key, overrides in {
                "on": {"header_border": True, "col_border": True, "row_border": True,
                       "header_wrap": True, "body_wrap": True, "header_height_px": 50,
                       "row_height_px": 60, "header_font_size_px": 18, "body_font_size_px": 20,
                       "header_color": "#ff00aa", "row_selected_color": "#123456", "cell_fill_color": "#abcdef", "cell_outline_color": "#654321"},
                "off": {"header_border": False, "col_border": True, "row_border": True, "header_wrap": False, "body_wrap": False},
                "body_off": {"header_border": True, "col_border": False, "row_border": False},
                "short": {"row_height_px": 24}, "tall": {"row_height_px": 70},
            }.items():
                families = normalize_family_layouts({"families": {"general": overrides}})
                cases[key] = {"css": css_for_families(families), "layout": families["general"]}
            defaults = normalize_family_layouts({})
            target.write_text(json.dumps({"pages": pages, "cases": cases, "defaults": defaults,
                                         "defaultCss": css_for_families(defaults)}, ensure_ascii=False), encoding="utf-8")

    def test_general_border_save_reload_and_independence(self):
        defaults = normalize_family_layouts({})["general"]
        for visible in (True, False):
            data = {name: str(value) for name, value in defaults.items() if not isinstance(value, bool)}
            data.update(family="general", header_border="show" if visible else "hide",
                        col_border="show", row_border="show")
            response = self.client.post(reverse("system_table_layout"), data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(response.status_code, 200)
            saved = TableLayoutSettings.objects.get().section_layouts["families"]["general"]
            self.assertEqual(saved["header_border"], visible)
            self.assertTrue(saved["col_border"])
            html = self.client.get(reverse("systemic_intelligence")).content.decode()
            self.assertIn(response.json()["css"], html)
            css = response.json()["css"].split('[data-table-type="calculation"]', 1)[0]
            self.assertEqual("thead th:not(:last-child)" in css, visible)
            self.assertIn("tbody td:not(:last-child)", css)
            self.assertNotIn("linear-gradient", css)
