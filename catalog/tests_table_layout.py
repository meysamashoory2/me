"""Unit tests for per-menu table display helpers."""

from __future__ import annotations

from django.test import SimpleTestCase

from catalog.table_layout import (
    COLUMN_BORDER_COLOR,
    DEFAULT_HEADER_COLOR,
    SECTION_CHOICES,
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

    def test_reset_layout_part_restores_factory_colors(self):
        dirty = default_layout("excel")
        dirty["header_color"] = "#ff0000"
        dirty["header_alpha"] = 40
        dirty["header_height_px"] = 80
        dirty["row_height_px"] = 12
        dirty["row_selected_color"] = "#00ff00"
        header = reset_layout_part(dirty, "header", "excel")
        self.assertEqual(header["header_color"], DEFAULT_HEADER_COLOR)
        self.assertEqual(header["header_alpha"], 100)
        self.assertEqual(header["header_height_px"], 36)
        self.assertEqual(header["row_height_px"], 12)
        body = reset_layout_part(header, "body", "excel")
        self.assertEqual(body["row_height_px"], 36)
        self.assertEqual(body["row_selected_color"], default_layout("excel")["row_selected_color"])
        self.assertEqual(body["header_color"], DEFAULT_HEADER_COLOR)

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

    def test_css_does_not_clip_ops_columns(self):
        css = css_for_layouts(
            {
                "excel": {
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
        self.assertIn("max-height:none !important", css)
        self.assertIn("td:not(.col-ops)", css)
        self.assertIn('[data-table-section="excel"] .pcx-table', css)
        self.assertIn("--table-row-height:20px", css)
        self.assertIn("--table-header-height:20px", css)

    def test_pipe_calc_css_targets_pcx_tables(self):
        css = css_for_layouts(
            {
                "pipe_calc": {
                    "row_height_px": 48,
                    "header_height_px": 40,
                    "col_border": True,
                    "row_border": True,
                    "width_locked": True,
                }
            }
        )
        self.assertIn('[data-table-section="pipe_calc"] .pcx-table', css)
        self.assertIn('[data-table-section="pipe_calc"] .pcx-table td:not(.col-ops)', css)
        self.assertIn("height:var(--table-row-height) !important", css)
        self.assertIn("height:var(--table-header-height) !important", css)
        self.assertIn("background-color:var(--table-header-bg) !important", css)
        self.assertIn(".pcx-table thead th", css)
        self.assertIn("color-mix(in srgb,var(--table-row-selected)", css)
