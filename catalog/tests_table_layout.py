"""Unit tests for per-menu table display helpers."""

from __future__ import annotations

from django.test import SimpleTestCase

from catalog.table_layout import (
    COLUMN_BORDER_COLOR,
    FAMILY_DEFAULTS,
    FAMILY_KEYS,
    FAMILY_LABELS,
    FamilyValidationError,
    PROFILE_DEFAULTS,
    ProfileValidationError,
    SECTION_CHOICES,
    clamp_row_height,
    css_for_profiles,
    css_for_layouts,
    css_for_families,
    default_width_locked,
    normalize_all_layouts,
    normalize_family_layouts,
    family_layout_from_post,
    normalize_profile_layouts,
    profile_layout_from_post,
)


class TableLayoutHelpersTests(SimpleTestCase):
    def test_exact_business_family_definitions_and_defaults(self):
        self.assertEqual(FAMILY_KEYS, ("list_management", "production_calculation", "operational", "report_output"))
        self.assertEqual(tuple(FAMILY_LABELS.values()), ("فهرست‌ها و مدیریت", "محاسبات تولید", "جداول عملیاتی", "گزارشات"))
        self.assertEqual(FAMILY_DEFAULTS["list_management"]["body_font_size_px"], 14)
        self.assertEqual(FAMILY_DEFAULTS["production_calculation"]["cell_padding_vertical_px"], 5)
        self.assertEqual(FAMILY_DEFAULTS["report_output"]["cell_padding_horizontal_px"], 8)

    def test_missing_family_data_uses_defaults_without_touching_profiles(self):
        stored = {"all": {"row_height_px": 47}, "profiles": {"compact": {"row_height_px": 26}}, "legacy": {"keep": 1}}
        families = normalize_family_layouts(stored)
        self.assertEqual(families["operational"]["row_height_px"], 36)
        self.assertEqual(stored["profiles"]["compact"]["row_height_px"], 26)
        self.assertEqual(stored["legacy"], {"keep": 1})

    def test_family_validation_and_header_body_independence(self):
        valid = {
            "row_height_px": "34", "header_height_px": "35", "body_font_size_px": "13.4", "header_font_size_px": "11.2",
            "cell_padding_vertical_px": "5", "cell_padding_horizontal_px": "8", "header_border": "show", "col_border": "show", "row_border": "show",
            "header_color": "#c7d7ea", "header_alpha": "100", "row_selected_color": "#dbeafe", "row_selected_alpha": "90",
            "cell_outline_color": "#2563eb", "cell_outline_alpha": "80", "cell_fill_color": "#ffffff", "cell_fill_alpha": "70",
        }
        layout = family_layout_from_post("production_calculation", valid)
        self.assertEqual(layout["header_font_size_px"], 11.2)
        self.assertEqual(layout["body_font_size_px"], 13.4)
        for field, bad in (("row_height_px", "NaN"), ("header_font_size_px", "Infinity"), ("body_font_size_px", "0"), ("cell_padding_vertical_px", "-1"), ("cell_padding_horizontal_px", "99"), ("header_color", "red"), ("header_alpha", "101")):
            with self.subTest(field=field), self.assertRaises(FamilyValidationError):
                family_layout_from_post("production_calculation", {**valid, field: bad})
        with self.assertRaises(FamilyValidationError):
            family_layout_from_post("unknown", valid)

    def test_family_css_is_container_scoped(self):
        css = css_for_families(normalize_family_layouts({}))
        self.assertIn('[data-table-family="production-calculation"]', css)
        self.assertIn("--table-family-header-font-size:12px", css)
        self.assertIn("--table-family-body-font-size:12.5px", css)
        self.assertIn('table:not(.excel-preview-table):not(.excel-grid) th', css)
        self.assertNotIn("table.pcx-table", css)
        self.assertNotIn("#pcx-depot-table", css)

    def test_profile_defaults_are_backward_compatible(self):
        self.assertEqual(normalize_profile_layouts({}), PROFILE_DEFAULTS)
        self.assertEqual(
            normalize_profile_layouts({"all": {"row_height_px": 49}}),
            PROFILE_DEFAULTS,
        )

    def test_profile_normalization_preserves_each_valid_profile(self):
        stored = {"profiles": {"compact": {"row_height_px": 31, "header_height_px": 32, "font_size_px": 11.4, "cell_padding_px": 4}}}
        profiles = normalize_profile_layouts(stored)
        self.assertEqual(profiles["compact"]["row_height_px"], 31)
        self.assertEqual(profiles["compact"]["font_size_px"], 11.4)
        self.assertEqual(profiles["report"], PROFILE_DEFAULTS["report"])

    def test_profile_post_strictly_validates_every_density_field(self):
        valid = {"row_height_px": "31", "header_height_px": "32", "font_size_px": "12.7", "cell_padding_px": "4"}
        self.assertEqual(profile_layout_from_post("compact", valid)["font_size_px"], 12.7)
        for field, bad_values in {
            "row_height_px": ("bad", "0", "81", "20.5"),
            "header_height_px": ("bad", "-1", "81", "20.5"),
            "font_size_px": ("bad", "0", "25"),
            "cell_padding_px": ("bad", "0", "25", "2.5"),
        }.items():
            for value in bad_values:
                posted = {**valid, field: value}
                with self.subTest(field=field, value=value), self.assertRaises(ProfileValidationError):
                    profile_layout_from_post("compact", posted)

    def test_profile_css_is_scoped_and_reproduces_existing_defaults(self):
        css = css_for_profiles(PROFILE_DEFAULTS)
        self.assertIn(".erp-table-profile--compact{--table-profile-row-height:26px", css)
        self.assertIn("--table-profile-cell-padding-inline:6px", css)
        self.assertIn(".erp-table-profile--report{--table-profile-row-height:34px", css)
        self.assertIn("--table-profile-font-size:12.5px", css)
        self.assertIn(".erp-table-profile--data-entry{", css)
        compact = css.split(".erp-table-profile--report", 1)[0]
        self.assertNotIn("data-entry", compact)
        self.assertNotIn("table{", css)

    def test_clamp_row_height_range(self):
        self.assertEqual(clamp_row_height(5), 5)
        self.assertEqual(clamp_row_height(120), 120)
        self.assertEqual(clamp_row_height(4), 5)
        self.assertEqual(clamp_row_height(200), 120)
        self.assertEqual(clamp_row_height("x"), 36)

    def test_default_lock_only_reports_unlocked(self):
        self.assertFalse(default_width_locked("reports"))
        self.assertTrue(default_width_locked("planning"))
        self.assertTrue(default_width_locked("product_data"))

    def test_normalize_empty_uses_defaults(self):
        layouts = normalize_all_layouts({})
        self.assertFalse(layouts["reports"]["width_locked"])
        self.assertTrue(layouts["history"]["width_locked"])
        self.assertTrue(layouts["reports"]["col_border"])
        self.assertEqual(layouts["planning"]["row_height_px"], 36)

    def test_normalize_uses_legacy_locks_when_layouts_empty(self):
        layouts = normalize_all_layouts(
            {},
            legacy_height=40,
            legacy_locks={"reports": True, "planning": False},
        )
        self.assertEqual(layouts["planning"]["row_height_px"], 40)
        self.assertTrue(layouts["reports"]["width_locked"])
        self.assertTrue(layouts["planning"]["width_locked"])

    def test_css_includes_height_and_hidden_borders(self):
        css = css_for_layouts(
            {
                "planning": {
                    "row_height_px": 8,
                    "col_border": False,
                    "row_border": True,
                    "width_locked": True,
                }
            }
        )
        self.assertIn("--table-row-height:8px", css)
        self.assertIn("border-left:none !important", css)
        self.assertIn("border-right:none !important", css)
        self.assertIn("border-inline-end:none !important", css)
        self.assertIn("background-image:none !important", css)
        self.assertIn('[data-table-section="planning"] table th', css)
        self.assertNotIn("border-bottom-color:transparent", css)
        self.assertNotIn(
            '[data-table-section="planning"] table th:not(:last-child)',
            css,
        )

    def test_css_paints_column_borders_when_shown(self):
        css = css_for_layouts(
            {
                "planning": {
                    "row_height_px": 36,
                    "col_border": True,
                    "row_border": True,
                    "header_border": True,
                    "width_locked": True,
                }
            }
        )
        # A single background stroke draws the divider; no doubled border.
        self.assertNotIn("border-inline-end:1px solid", css)
        self.assertIn(
            f"background-image:linear-gradient({COLUMN_BORDER_COLOR},{COLUMN_BORDER_COLOR}) !important",
            css,
        )
        self.assertIn("background-size:1px 100% !important", css)
        self.assertIn(
            '[data-table-section="planning"] table th:not(:last-child)',
            css,
        )
        self.assertIn(
            '[data-table-section="planning"] .pcx-table td:not(:last-child)',
            css,
        )
        self.assertNotIn("border-left:none !important", css)
        self.assertNotIn("background-image:none !important", css)

    def test_css_default_layouts_paint_every_section(self):
        css = css_for_layouts(normalize_all_layouts({}))
        for key, _label in SECTION_CHOICES:
            self.assertIn(
                f'[data-table-section="{key}"] table th:not(:last-child)',
                css,
            )
            self.assertNotIn(
                f'[data-table-section="{key}"] table th{{border-left:none',
                css,
            )

    def test_css_row_border_hides_body_only(self):
        css = css_for_layouts(
            {
                "reports": {
                    "row_height_px": 36,
                    "col_border": True,
                    "row_border": False,
                    "header_border": True,
                    "width_locked": False,
                }
            }
        )
        # Body rows lose their horizontal separators…
        self.assertIn("border-bottom-color:transparent !important", css)
        # …but the header underline/shadow is its own control, untouched here.
        self.assertNotIn("box-shadow:none !important", css)
        # Column borders still paint when that toggle is on.
        self.assertIn(
            f"background-image:linear-gradient({COLUMN_BORDER_COLOR},{COLUMN_BORDER_COLOR}) !important",
            css,
        )

    def test_css_header_border_hides_header_shadow(self):
        css = css_for_layouts(
            {
                "reports": {
                    "row_height_px": 36,
                    "col_border": True,
                    "row_border": True,
                    "header_border": False,
                    "width_locked": False,
                }
            }
        )
        self.assertIn("box-shadow:none !important", css)
        self.assertIn(".table-scroll thead th", css)
