"""Tests for system naming-key registry and column management UI."""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from catalog.models import SystemNamingKey
from catalog.naming_registry import resolve_label, sync_naming_registry
from catalog.transfer import list_destinations_for_ui

User = get_user_model()


class SystemNamingRegistryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")
        cls.admin = User.objects.get(username="admin")
        cls.expert = User.objects.get(username="expert")

    def test_sync_creates_keys_with_addresses(self):
        stats = sync_naming_registry()
        self.assertGreater(stats["created"], 20)
        self.assertTrue(SystemNamingKey.objects.filter(key="system.section.molds").exists())
        col = SystemNamingKey.objects.get(key="ui.table.planning.plan_list.col.program_number")
        self.assertIn("سرستون", col.address)
        self.assertEqual(col.category, SystemNamingKey.Category.COLUMN)

    def test_admin_harvest_skips_technical_id_fields(self):
        from catalog.naming_registry import harvest_specs

        specs = harvest_specs()
        admin_field_keys = [s["key"] for s in specs if s["key"].startswith("admin.field.")]
        self.assertTrue(admin_field_keys)
        self.assertFalse(any(k.endswith(".id") for k in admin_field_keys))
        created_by = [
            s for s in specs if s["key"].endswith(".created_by") and s["key"].startswith("admin.")
        ]
        if created_by:
            self.assertIn("پنل مدیریت", created_by[0]["address"])

    def test_resync_deactivates_obsolete_admin_fields(self):
        sync_naming_registry()
        obsolete = SystemNamingKey.objects.create(
            key="admin.field.catalog.unit.id",
            label="ID",
            default_label="ID",
            address="old technical field",
            category=SystemNamingKey.Category.COLUMN,
            table_key="admin.catalog.unit",
            column_key="id",
            is_custom=False,
            is_active=True,
        )
        stats = sync_naming_registry(refresh_defaults=True)
        obsolete.refresh_from_db()
        self.assertFalse(obsolete.is_active)
        self.assertGreaterEqual(stats.get("deactivated", 0), 1)

    def test_naming_keys_default_source_hides_admin(self):
        sync_naming_registry()
        self.client.login(username="admin", password="erp12345")
        page = self.client.get(reverse("system_naming_keys"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "نوع کلید")
        self.assertContains(page, "عنوان نمایشی")
        self.assertNotContains(page, "admin.field.")

    def test_transfer_dialog_titles_are_harvested(self):
        sync_naming_registry()
        self.assertTrue(
            SystemNamingKey.objects.filter(
                key="transfer.ui.dialog.title_transfer"
            ).exists()
        )
        self.assertTrue(
            SystemNamingKey.objects.filter(key="transfer.dest.production_history").exists()
        )
        row = SystemNamingKey.objects.get(key="transfer.ui.dialog.title_transfer")
        self.assertIn("دیالوگ انتقال", row.address)
        self.assertEqual(row.section_key, "transfer_dialog_labels")

    def test_hide_transfer_destination_removes_from_dialog(self):
        sync_naming_registry()
        dest = SystemNamingKey.objects.get(key="transfer.dest.production_history")
        dest.is_active = False
        dest.save(update_fields=["is_active"])
        ids = [d["id"] for d in list_destinations_for_ui()]
        self.assertNotIn("production_history", ids)
        self.assertIn("product_data", ids)

    def test_hide_transfer_field_removes_from_dialog(self):
        sync_naming_registry()
        field = SystemNamingKey.objects.get(
            key="transfer.field.production_history.history_list.scrap_qty"
        )
        field.is_active = False
        field.save(update_fields=["is_active"])
        hist = next(
            d for d in list_destinations_for_ui() if d["id"] == "production_history"
        )
        level = next(lv for lv in hist["levels"] if lv["id"] == "history_list")
        keys = [f["key"] for f in level["fields"]]
        self.assertNotIn("scrap_qty", keys)
        self.assertIn("unique_code", keys)

    def test_system_data_lists_transfer_dialog_section(self):
        sync_naming_registry()
        self.client.login(username="admin", password="erp12345")
        hub = self.client.get(reverse("system_data"))
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, "عناوین دیالوگ و مقاصد انتقال داده")
        self.assertContains(hub, "kind=key")

    def test_system_data_hides_inactive_section(self):
        sync_naming_registry()
        row = SystemNamingKey.objects.get(key="system.section.transfer_dialog_labels")
        row.is_active = False
        row.save(update_fields=["is_active"])
        self.client.login(username="admin", password="erp12345")
        hub = self.client.get(reverse("system_data"))
        self.assertEqual(hub.status_code, 200)
        self.assertNotContains(hub, "عناوین دیالوگ و مقاصد انتقال داده")

    def test_rename_persists_and_resolve_label(self):
        sync_naming_registry()
        self.client.login(username="expert", password="erp12345")
        row = SystemNamingKey.objects.get(
            key="ui.table.production.history_list.col.unique_code"
        )
        resp = self.client.post(
            reverse("system_naming_key_save"),
            data=json.dumps({"id": row.pk, "label": "کد یکتای سفارشی"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        row.refresh_from_db()
        self.assertEqual(row.label, "کد یکتای سفارشی")
        self.assertEqual(
            resolve_label("ui.table.production.history_list.col.unique_code"),
            "کد یکتای سفارشی",
        )

    def test_naming_keys_page_search_by_address(self):
        sync_naming_registry()
        self.client.login(username="admin", password="erp12345")
        hub = self.client.get(reverse("system_data"))
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, "نام‌گذاری عناوین سیستم")
        self.assertContains(hub, "تنظیمات جداول")

        page = self.client.get(reverse("system_naming_keys"), {"q": "unique_code"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "کد یکتا")

    def test_table_columns_add_custom_and_link_section(self):
        sync_naming_registry()
        self.client.login(username="admin", password="erp12345")
        cols = self.client.get(
            reverse("system_table_columns"),
            {"table": "planning.plan_list"},
        )
        self.assertEqual(cols.status_code, 200)
        self.assertContains(cols, "افزودن سرستون سفارشی")

        create = self.client.post(
            reverse("system_naming_key_save"),
            data=json.dumps({
                "key": "ui.table.planning.plan_list.col.custom_note",
                "label": "یادداشت سفارشی",
                "category": "column",
                "table_key": "planning.plan_list",
                "column_key": "custom_note",
                "linked_section_key": "weekly_plans",
                "address": "custom:planning.plan_list#custom_note",
                "is_active": True,
                "order": 50,
            }),
            content_type="application/json",
        )
        self.assertEqual(create.status_code, 200)
        self.assertTrue(create.json()["ok"])
        row = SystemNamingKey.objects.get(key="ui.table.planning.plan_list.col.custom_note")
        self.assertTrue(row.is_custom)
        self.assertEqual(row.linked_section_key, "weekly_plans")

    def test_plan_list_uses_renamed_column_label(self):
        sync_naming_registry()
        row = SystemNamingKey.objects.get(
            key="ui.table.planning.plan_list.col.creator"
        )
        row.label = "نام کاربر ایجادکننده"
        row.save(update_fields=["label"])
        self.client.login(username="admin", password="erp12345")
        page = self.client.get(reverse("plan_list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "نام کاربر ایجادکننده")

    def test_transfer_source_filter_shows_dialog_titles(self):
        sync_naming_registry()
        self.client.login(username="admin", password="erp12345")
        page = self.client.get(
            reverse("system_naming_keys"),
            {"kind": "key", "q": "انتقال داده جدول"},
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "دیالوگ انتقال")

    def test_naming_page_skips_full_harvest_and_links_rows(self):
        sync_naming_registry()
        self.client.login(username="admin", password="erp12345")
        from catalog import naming_registry as nr

        calls = {"n": 0}
        orig = nr.harvest_specs

        def tracked():
            calls["n"] += 1
            return orig()

        nr.harvest_specs = tracked
        try:
            page = self.client.get(reverse("system_naming_keys"))
        finally:
            nr.harvest_specs = orig
        self.assertEqual(page.status_code, 200)
        self.assertEqual(calls["n"], 0)
        html = page.content.decode()
        self.assertIn("naming_preview=1", html)
        self.assertIn(reverse("plan_list"), html)
        from catalog.naming_registry import preview_href

        href = preview_href(
            "ui.table.planning.plan_list.col.program_number",
            return_path="/data/system/naming/",
            table_key="planning.plan_list",
            column_key="program_number",
        )
        self.assertIn("naming_preview=1", href)
        self.assertIn(reverse("plan_list"), href)
        self.assertIn("data-col", href)
        self.assertIn("focus_key", href)
        nav = preview_href("nav.item.planning", return_path="/x/")
        self.assertTrue(nav)
        self.assertIn("naming_preview=1", nav)
        dest = preview_href("transfer.dest.production_history", return_path="/x/")
        self.assertTrue(dest)

    def test_preview_keeps_inactive_nav_and_hub_slot(self):
        sync_naming_registry()
        nav_row = SystemNamingKey.objects.get(key="nav.item.pipe_calc")
        nav_row.is_active = False
        nav_row.save(update_fields=["is_active"])
        hub_row = SystemNamingKey.objects.get(key="system.section.transfer_dialog_labels")
        hub_row.is_active = False
        hub_row.save(update_fields=["is_active"])
        self.client.login(username="admin", password="erp12345")
        hidden = self.client.get(reverse("dashboard"))
        self.assertNotContains(hidden, 'data-nav-key="pipe_calc"')
        preview = self.client.get(
            reverse("pipe_calc"),
            {"naming_preview": "1", "hk": "nav.item.pipe_calc"},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, 'data-nav-key="pipe_calc"')
        self.assertContains(preview, "naming-ghost-slot")
        hub = self.client.get(reverse("system_data"))
        self.assertNotContains(hub, "عناوین دیالوگ و مقاصد انتقال داده")
        hub_preview = self.client.get(
            reverse("system_data"),
            {"naming_preview": "1", "hk": "system.section.transfer_dialog_labels"},
        )
        self.assertContains(hub_preview, "عناوین دیالوگ و مقاصد انتقال داده")
        self.assertContains(hub_preview, "naming-ghost-slot")

    def test_inactive_column_preview_unhides_slot(self):
        from core.context_processors import _hidden_column_css
        from django.test import RequestFactory

        sync_naming_registry()
        row = SystemNamingKey.objects.get(key="ui.table.planning.plan_list.col.creator")
        row.is_active = False
        row.save(update_fields=["is_active"])
        hidden = _hidden_column_css()
        self.assertIn('data-col="creator"', hidden)
        self.assertIn("display:none", hidden)
        req = RequestFactory().get(
            reverse("plan_list"),
            {"naming_preview": "1", "hk": row.key},
        )
        preview_css = _hidden_column_css(req)
        self.assertIn("display:table-cell", preview_css)
        self.assertNotIn('[data-col="creator"]{display:none', preview_css)

    def test_plan_list_double_click_opens_view(self):
        self.client.login(username="admin", password="erp12345")
        page = self.client.get(reverse("plan_list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "mode=view")
        self.assertContains(page, "data-href")

    def test_naming_rows_carry_key_for_return_focus(self):
        sync_naming_registry()
        self.client.login(username="admin", password="erp12345")
        page = self.client.get(reverse("system_naming_keys"))
        self.assertContains(page, 'data-key="nav.item.planning"')

    def test_excel_list_uses_renamed_column_label(self):
        from catalog.models import ExcelUpload

        sync_naming_registry()
        ExcelUpload.objects.create(title="نمونه", original_name="sample.xlsx", uploaded_by=self.admin)
        row = SystemNamingKey.objects.get(key="ui.table.catalog.excel_list.col.title")
        row.label = "نام فایل سفارشی"
        row.save(update_fields=["label"])
        self.client.login(username="admin", password="erp12345")
        page = self.client.get(reverse("excel_list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "نام فایل سفارشی")
        self.assertContains(page, 'data-col="title"')

    def test_admin_header_save_scoped_to_model(self):
        sync_naming_registry()
        self.client.login(username="admin", password="erp12345")
        # Seed two models that share column_key "label"
        key_a = "admin.field.catalog.moldoption.label"
        key_b = "admin.field.catalog.stoppagereason.label"
        SystemNamingKey.objects.update_or_create(
            key=key_a,
            defaults={
                "label": "عنوان",
                "default_label": "عنوان",
                "category": SystemNamingKey.Category.COLUMN,
                "table_key": "admin.catalog.moldoption",
                "column_key": "label",
                "is_active": True,
            },
        )
        SystemNamingKey.objects.update_or_create(
            key=key_b,
            defaults={
                "label": "عنوان",
                "default_label": "عنوان",
                "category": SystemNamingKey.Category.COLUMN,
                "table_key": "admin.catalog.stoppagereason",
                "column_key": "label",
                "is_active": True,
            },
        )
        resp = self.client.post(
            reverse("system_admin_header_save"),
            data=json.dumps({
                "path": "/admin/catalog/moldoption/",
                "field": "label",
                "label": "عنوان قالب سفارشی",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(resp.json()["key"], key_a)
        self.assertEqual(SystemNamingKey.objects.get(key=key_a).label, "عنوان قالب سفارشی")
        self.assertEqual(SystemNamingKey.objects.get(key=key_b).label, "عنوان")


class ReportColumnDefaultWidthUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")
        cls.admin = User.objects.get(username="admin")

    def test_report_table_shows_width_column(self):
        sync_naming_registry()
        self.client.login(username="admin", password="erp12345")
        page = self.client.get(
            reverse("system_table_columns"),
            {"table": "report.fitting"},
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "عرض ستون")
        self.assertContains(page, "default_width_px")

        row = SystemNamingKey.objects.get(key="report.col.fitting.code")
        save = self.client.post(
            reverse("system_naming_key_save"),
            data=json.dumps({
                "id": row.pk,
                "label": row.label,
                "default_width_px": 180,
                "is_active": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(save.status_code, 200)
        self.assertTrue(save.json()["ok"])
        row.refresh_from_db()
        self.assertEqual(row.default_width_px, 180)
