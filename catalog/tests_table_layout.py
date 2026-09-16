"""Unit tests for global table display helpers."""

from __future__ import annotations

from django.test import SimpleTestCase

from catalog.table_layout import (
    COLUMN_BORDER_COLOR,
    CSS_HOST,
    DEFAULT_HEADER_COLOR,
    GLOBAL_LAYOUT_KEY,
    clamp_row_height,
    css_for_layouts,
    default_layout,
    default_width_locked,
    normalize_all_layouts,
    reset_layout_part,
)


class TableLayoutHelpersTests(SimpleTestCase):
    def test_clamp_row_height_range(self):
        self.assertEqual(clamp_row_height(5), 5)
        self.assertEqual(clamp_row_height(120), 120)
        self.assertEqual(clamp_row_height(4), 5)
        self.assertEqual(clamp_row_height(200), 120)
        self.assertEqual(clamp_row_height("x"), 36)

    def test_default_lock_is_on(self):
        self.assertTrue(default_width_locked("reports"))
        self.assertTrue(default_width_locked("planning"))
        self.assertTrue(default_width_locked("product_data"))

    def test_normalize_empty_uses_defaults(self):
        layouts = normalize_all_layouts({})
        self.assertEqual(set(layouts), {GLOBAL_LAYOUT_KEY})
        self.assertTrue(layouts[GLOBAL_LAYOUT_KEY]["width_locked"])
        self.assertTrue(layouts[GLOBAL_LAYOUT_KEY]["col_border"])
        self.assertEqual(layouts[GLOBAL_LAYOUT_KEY]["row_height_px"], 36)
        self.assertEqual(layouts[GLOBAL_LAYOUT_KEY]["header_color"], DEFAULT_HEADER_COLOR)

    def test_reset_layout_part_restores_factory_colors(self):
        dirty = default_layout("all")
        dirty["header_color"] = "#ff0000"
        dirty["header_alpha"] = 40
        dirty["header_height_px"] = 80
        dirty["row_height_px"] = 12
        dirty["row_selected_color"] = "#00ff00"
        header = reset_layout_part(dirty, "header", "all")
        self.assertEqual(header["header_color"], DEFAULT_HEADER_COLOR)
        self.assertEqual(header["header_alpha"], 100)
        self.assertEqual(header["header_height_px"], 36)
        self.assertEqual(header["row_height_px"], 12)
        body = reset_layout_part(header, "body", "all")
        self.assertEqual(body["row_height_px"], 36)
        self.assertEqual(body["row_selected_color"], default_layout("all")["row_selected_color"])
        self.assertEqual(body["header_color"], DEFAULT_HEADER_COLOR)

    def test_normalize_uses_legacy_locks_when_layouts_empty(self):
        layouts = normalize_all_layouts(
            {},
            legacy_height=40,
            legacy_locks={"reports": True, "planning": False},
        )
        self.assertEqual(layouts[GLOBAL_LAYOUT_KEY]["row_height_px"], 40)
        self.assertTrue(layouts[GLOBAL_LAYOUT_KEY]["width_locked"])

    def test_css_is_global_and_skips_accordion_menus(self):
        css = css_for_layouts(
            {
                GLOBAL_LAYOUT_KEY: {
                    "row_height_px": 8,
                    "col_border": False,
                    "row_border": True,
                    "width_locked": True,
                }
            }
        )
        self.assertIn(f"{CSS_HOST}{{--table-row-height:8px", css)
        self.assertIn("border-left:none !important", css)
        self.assertIn(f"{CSS_HOST} table.table th", css)
        self.assertIn(".system-accordion", css)
        self.assertIn("background-color:transparent !important", css)
        self.assertNotIn('data-table-section="planning"', css)

    def test_css_paints_column_borders_when_shown(self):
        css = css_for_layouts(
            {
                GLOBAL_LAYOUT_KEY: {
                    "row_height_px": 36,
                    "col_border": True,
                    "row_border": True,
                    "header_border": True,
                    "width_locked": True,
                }
            }
        )
        self.assertNotIn("border-inline-end:1px solid", css)
        self.assertIn(
            f"background-image:linear-gradient({COLUMN_BORDER_COLOR},{COLUMN_BORDER_COLOR}) !important",
            css,
        )
        self.assertIn(f"{CSS_HOST} table.table th:not(:last-child)", css)
        self.assertIn(f"{CSS_HOST} .pcx-table td:not(:last-child)", css)

    def test_css_row_border_hides_body_only(self):
        css = css_for_layouts(
            {
                GLOBAL_LAYOUT_KEY: {
                    "row_height_px": 36,
                    "col_border": True,
                    "row_border": False,
                    "header_border": True,
                    "width_locked": True,
                }
            }
        )
        self.assertIn("border-bottom-color:transparent !important", css)
        self.assertNotIn("box-shadow:none !important", css)

    def test_css_header_border_hides_header_shadow(self):
        css = css_for_layouts(
            {
                GLOBAL_LAYOUT_KEY: {
                    "row_height_px": 36,
                    "col_border": True,
                    "row_border": True,
                    "header_border": False,
                    "width_locked": True,
                }
            }
        )
        self.assertIn("box-shadow:none !important", css)
        self.assertIn(".table-scroll thead th", css)

    def test_css_does_not_clip_ops_columns(self):
        css = css_for_layouts(
            {
                GLOBAL_LAYOUT_KEY: {
                    "row_height_px": 20,
                    "header_height_px": 20,
                    "col_border": True,
                    "row_border": True,
                    "width_locked": True,
                }
            }
        )
        self.assertIn("td.col-ops", css)
        self.assertIn("overflow:visible !important", css)
        self.assertIn("td:not(.col-ops)", css)
        self.assertIn(f"{CSS_HOST} .pcx-table", css)
        self.assertIn("--table-row-height:20px", css)

    def test_pipe_calc_css_targets_pcx_tables(self):
        css = css_for_layouts(
            {
                GLOBAL_LAYOUT_KEY: {
                    "row_height_px": 48,
                    "header_height_px": 40,
                    "col_border": True,
                    "row_border": True,
                    "width_locked": True,
                }
            }
        )
        self.assertIn(f"{CSS_HOST} .pcx-table", css)
        self.assertIn(f"{CSS_HOST} .pcx-table td:not(.col-ops)", css)
        self.assertIn("height:var(--table-row-height) !important", css)
        self.assertIn("background-color:var(--table-header-bg) !important", css)
        self.assertIn("color-mix(in srgb,var(--table-row-selected)", css)
