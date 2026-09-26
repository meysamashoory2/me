"""Tests for pipe production-time calculation engine and hub."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase

from accounts.models import Role, UserProfile
from catalog.pipe_calc.constants import LINE_GENERAL, LINE_PROTECT, LINE_TIP, PROTECT_SIZES
from catalog.pipe_calc.engine import (
    CalcItemInput,
    aggregate_times,
    calc_bom_needs,
    calc_depot,
    calc_production_time,
)
from catalog.pipe_calc.models import PipeLengthCut, PipeProductLine, PipeSizeProfile
from catalog.pipe_calc.seed import seed_pipe_calc_defaults
from catalog.pipe_calc.services import run_line_aggregate, run_scenario


class PipeCalcEngineTests(TestCase):
    def test_line_and_billing_time(self):
        item = CalcItemInput(
            key="p",
            pieces=100,
            cut_length_mm=318,  # ~0.318 m
            line_speed_m_per_min=10.0,
            billing_pieces_per_hour=300.0,
            socket_ends=1,
            pack_qty=50,
            needs_billing=True,
        )
        res = calc_production_time(item)
        self.assertAlmostEqual(res.meters, 31.8, places=2)
        # 31.8 m / 10 m/min = 3.18 min = 190.8 s
        self.assertAlmostEqual(res.line.seconds, 190.8, places=1)
        # 100 pcs / 300 per hour = 1200 s
        self.assertAlmostEqual(res.billing.seconds, 1200.0, places=1)
        self.assertEqual(res.packs, 2.0)

    def test_two_socket_doubles_billing(self):
        one = calc_production_time(
            CalcItemInput(
                key="a",
                pieces=60,
                cut_length_mm=1000,
                line_speed_m_per_min=10,
                billing_pieces_per_hour=120,
                socket_ends=1,
            )
        )
        two = calc_production_time(
            CalcItemInput(
                key="b",
                pieces=60,
                cut_length_mm=1000,
                line_speed_m_per_min=10,
                billing_pieces_per_hour=120,
                socket_ends=2,
            )
        )
        self.assertAlmostEqual(two.billing.seconds, one.billing.seconds * 2, places=1)

    def test_depot_shortage_and_voucher(self):
        d = calc_depot(ceiling=1000, stock=700, voucher_qty=100)
        self.assertEqual(d.empty_space, 300)
        self.assertEqual(d.stock_after_voucher, 600)
        self.assertEqual(d.empty_after_voucher, 400)
        self.assertEqual(d.fill_to_ceiling, 300)

    def test_bom_order_signal(self):
        bom = calc_bom_needs(
            10,
            [
                {
                    "code": "M1",
                    "name": "ماده",
                    "unit": "kg",
                    "qty_per_unit": 2,
                    "available": 5,
                }
            ],
        )
        self.assertTrue(bom.has_shortage)
        self.assertEqual(bom.lines[0].qty_needed, 20)
        self.assertEqual(bom.lines[0].qty_short, 15)
        self.assertTrue(bom.lines[0].order_suggested)

    def test_aggregate(self):
        a = calc_production_time(
            CalcItemInput(
                key="1",
                pieces=10,
                cut_length_mm=1000,
                line_speed_m_per_min=10,
                billing_pieces_per_hour=100,
            )
        )
        b = calc_production_time(
            CalcItemInput(
                key="2",
                pieces=20,
                cut_length_mm=1000,
                line_speed_m_per_min=10,
                billing_pieces_per_hour=100,
            )
        )
        agg = aggregate_times([a, b])
        self.assertEqual(agg.pieces, 30)
        self.assertEqual(agg.item_count, 2)
        self.assertAlmostEqual(agg.line_seconds, a.line.seconds + b.line.seconds, places=1)


class PipeCalcFactoryFormulaTests(TestCase):
    def test_protect_110_two_socket_locked_example(self):
        from catalog.pipe_calc.engine import days_1dp, hours_1dp
        from catalog.pipe_calc.matrix import calc_production_matrix_row

        row = calc_production_matrix_row(
            length_code="200cm_2s",
            label="لوله 110 دو متری دوسر سوکت",
            qty=800,
            cut_length_mm=2140,
            line_speed_m_per_min=3.0,
            socket_ends=2,
            needs_billing=True,
            size_mm=110,
            pack_qty=6,
            spacers_per_pack=3,
            pipe_cap_per_piece=0,
            cover_cm=254,
            cover_g_per_m=164,
            billing_cycle_s=30,
            billing_cavities=2,
            socket_cap_bag=150,
            pipe_cap_bag=120,
            spacer_bag=30,
            oring_bag=867,
            kg_per_meter=0.945,
            mix=[{"name": "ZB332", "percent": 17.7, "bucket": "all"}],
        )
        self.assertAlmostEqual(row.line_seconds, 34240.0, delta=1)
        self.assertEqual(hours_1dp(row.line_seconds), 9.5)
        self.assertEqual(days_1dp(row.line_seconds), 0.4)
        self.assertAlmostEqual(row.billing_seconds, 24000.0, places=1)
        self.assertEqual(hours_1dp(row.billing_seconds), 6.7)
        self.assertEqual(row.billing_shots, 800.0)
        self.assertEqual(row.spacers, 402)
        self.assertEqual(row.pipe_caps, 0)
        self.assertEqual(row.socket_caps, 1600)
        self.assertEqual(row.orings, 1600)
        self.assertAlmostEqual(row.oring_bags, 1600 / 867, places=8)
        self.assertAlmostEqual(row.socket_cap_bags, 1600 / 150, places=8)
        self.assertAlmostEqual(row.cover_kg, 55.819, places=3)
        self.assertAlmostEqual(row.cover_rolls, 55.81904 / 25, places=7)
        self.assertAlmostEqual(row.materials[0]["kg_total"], 286.3577, places=4)

    def test_one_socket_components_and_package_ceiling(self):
        from catalog.pipe_calc.accessories import calc_accessories

        acc = calc_accessories(
            qty=7,
            size_mm=110,
            socket_ends=1,
            pack_qty=6,
            spacers_per_pack=3,
            pipe_cap_per_piece=1,
            cover_cm=254,
            cover_g_per_m=164,
            socket_cap_bag=150,
            pipe_cap_bag=120,
            spacer_bag=30,
            oring_bag=867,
        )
        self.assertEqual(acc["packs"], 2)
        self.assertEqual(acc["orings"], 7)
        self.assertEqual(acc["socket_caps"], 7)
        self.assertEqual(acc["pipe_caps"], 7)
        self.assertEqual(acc["spacers"], 6)

    def test_size_200_skips_spacers(self):
        from catalog.pipe_calc.accessories import calc_accessories

        acc = calc_accessories(
            qty=10,
            size_mm=200,
            socket_ends=1,
            pack_qty=2,
            spacers_per_pack=3,
            pipe_cap_per_piece=1,
            cover_cm=78,
            cover_g_per_m=160,
            socket_cap_bag=36,
            pipe_cap_bag=36,
            spacer_bag=0,
            oring_bag=150,
        )
        self.assertEqual(acc["spacers"], 0)
        self.assertEqual(acc["pipe_caps"], 10)
        self.assertEqual(acc["packs"], 5)


class PipeCalcSeedAndServiceTests(TestCase):
    def setUp(self):
        seed_pipe_calc_defaults()

    def test_protect_sizes_and_lengths(self):
        line = PipeProductLine.objects.get(code=LINE_PROTECT)
        self.assertFalse(line.is_scaffold)
        self.assertTrue(line.needs_billing)
        sizes = list(
            PipeSizeProfile.objects.filter(line=line).values_list("size_mm", flat=True)
        )
        self.assertEqual(sorted(sizes), list(PROTECT_SIZES))
        profile = PipeSizeProfile.objects.get(line=line, size_mm=110)
        self.assertEqual(profile.length_cuts.count(), 10)
        self.assertEqual(profile.layers.count(), 1)
        cut_30 = PipeLengthCut.objects.get(size_profile=profile, length_code="30cm_1s")
        cut_50 = PipeLengthCut.objects.get(size_profile=profile, length_code="50cm_1s")
        coupler = PipeLengthCut.objects.get(size_profile=profile, length_code="coupler")
        cut_110 = PipeLengthCut.objects.get(size_profile=profile, length_code="200cm_2s")
        self.assertEqual(cut_110.sku_code, "70211020")
        self.assertEqual(cut_110.cut_length_mm, 2140)
        self.assertEqual(float(cut_110.line_speed_m_per_min), 3.0)
        self.assertEqual(cut_110.depot_ceiling, 450)
        self.assertEqual(float(profile.billing_cycle_seconds), 30)
        self.assertLess(cut_30.cut_length_mm, cut_50.cut_length_mm)
        self.assertEqual(coupler.label, "رابط")

    def test_general_no_40_200_triple_layer(self):
        line = PipeProductLine.objects.get(code=LINE_GENERAL)
        sizes = set(
            PipeSizeProfile.objects.filter(line=line).values_list("size_mm", flat=True)
        )
        self.assertNotIn(40, sizes)
        self.assertNotIn(200, sizes)
        profile = PipeSizeProfile.objects.get(line=line, size_mm=110)
        self.assertEqual(profile.layers.count(), 3)
        two_m = profile.length_cuts.get(length_code="200cm_2s")
        self.assertEqual(two_m.depot_ceiling, 4000)

    def test_factory_sync_preserves_existing_stock_ceilings(self):
        line = PipeProductLine.objects.get(code=LINE_PROTECT)
        profile = PipeSizeProfile.objects.get(line=line, size_mm=110)
        cut = profile.length_cuts.get(length_code="200cm_2s")
        self.assertEqual(cut.depot_ceiling, 450)

        profile.depot_ceiling = 0
        profile.save(update_fields=["depot_ceiling"])
        cut.depot_ceiling = 1234
        cut.save(update_fields=["depot_ceiling"])
        seed_pipe_calc_defaults()

        profile.refresh_from_db()
        cut.refresh_from_db()
        self.assertEqual(profile.depot_ceiling, 0)
        self.assertEqual(cut.depot_ceiling, 1234)

    def test_tip_is_scaffold(self):
        tip = PipeProductLine.objects.get(code=LINE_TIP)
        self.assertTrue(tip.is_scaffold)
        self.assertEqual(PipeSizeProfile.objects.filter(line=tip).count(), 0)

    def test_scenario_runs(self):
        line = PipeProductLine.objects.get(code=LINE_PROTECT)
        profile = (
            PipeSizeProfile.objects.filter(line=line, size_mm=50)
            .prefetch_related("length_cuts", "layers")
            .first()
        )
        profile.stock_on_hand = 100
        profile.depot_ceiling = 500
        profile.save(update_fields=["stock_on_hand", "depot_ceiling"])
        length = profile.length_cuts.get(length_code="100cm_1s")
        scenario = run_scenario(
            profile=profile, length=length, pieces=50, voucher_qty=20
        )
        self.assertGreater(scenario.time.line.seconds, 0)
        self.assertGreater(scenario.time.billing.seconds, 0)
        self.assertEqual(scenario.depot.empty_space, 400)
        self.assertEqual(scenario.depot.empty_after_voucher, 420)
        self.assertTrue(scenario.layers)

    def test_aggregate_batch(self):
        line = PipeProductLine.objects.get(code=LINE_PROTECT)
        data = run_line_aggregate(
            line,
            [
                {"size_mm": 40, "length_code": "50cm_1s", "pieces": 10},
                {"size_mm": 110, "length_code": "200cm_1s", "pieces": 10},
            ],
        )
        self.assertEqual(data["aggregate"]["item_count"], 2)
        self.assertEqual(data["aggregate"]["pieces"], 20)
        self.assertEqual(len(data["details"]), 2)


class PipeCalcViewTests(TestCase):
    def setUp(self):
        seed_pipe_calc_defaults()
        self.user = User.objects.create_user("pipeuser", password="pass12345")
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"role": Role.PLANNING_MANAGER},
        )
        self.client = Client()
        self.client.login(username="pipeuser", password="pass12345")

    def test_hub_loads_protect(self):
        resp = self.client.get("/data/pipe-calc/?line=protect")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "لوله‌های پروتکت")
        self.assertContains(resp, ">سایز</span>")
        self.assertContains(resp, "سقف دپو")
        self.assertContains(resp, '<option value="110">110</option>', html=True)
        self.assertContains(resp, "زمان تولید لوله")
        self.assertContains(resp, "اورینگ مورد نیاز")
        self.assertContains(resp, 'div class="pcx-stack"')
        self.assertNotContains(resp, "زمان اکسترود")
        self.assertNotContains(resp, "شات بلینگ")
        self.assertNotContains(resp, "اسکلت")
        self.assertNotContains(resp, "بعداً تکمیل می‌شود")

    def test_matrix_workspace_has_three_tabs_and_scoped_selectors(self):
        resp = self.client.get("/data/pipe-calc/?line=protect&size=110")
        html = resp.content.decode()
        self.assertEqual(html.count("data-pcx-tab="), 3)
        for label in (
            "برنامه دپو و نیاز تولید",
            "جدول محاسباتی لوله و مواد",
            "تعاریف اولیه",
        ):
            self.assertContains(resp, label)
        tabs = html.index('class="tabs pcx-workspace-tabs"')
        depot_start = html.index('data-pcx-panel="depot"')
        calculation_start = html.index('data-pcx-panel="calculation"')
        definitions_start = html.index('data-pcx-panel="definitions"')
        depot_panel = html[depot_start:calculation_start]
        calculation_panel = html[calculation_start:definitions_start]
        definitions_panel = html[definitions_start:]
        self.assertGreater(depot_start, tabs)
        self.assertIn('id="pcx-size"', depot_panel)
        self.assertIn('id="pcx-qty-source"', depot_panel)
        self.assertNotIn('id="pcx-size"', html[tabs:depot_start])
        self.assertNotIn('id="pcx-qty-source"', html[tabs:depot_start])
        self.assertNotIn('id="pcx-size"', calculation_panel)
        self.assertNotIn('id="pcx-qty-source"', calculation_panel)
        self.assertIn('id="pcx-defs-size"', definitions_panel)
        self.assertNotIn('id="pcx-qty-source"', definitions_panel)
        self.assertEqual(html.count('class="pcx-select"'), 3)
        self.assertNotIn("pcx-defs-dialog", html)
        self.assertIn('data-pcx-panel="definitions"', html)
        option_block = depot_panel[depot_panel.index('id="pcx-size"'):]
        self.assertNotIn("Ø", option_block)
        self.assertNotIn("پارامترهای کارخانه برای همین سایز", html)
        self.assertNotIn("شات بلینگ", html)

    def test_pipe_tables_use_only_the_approved_opt_in_profiles(self):
        resp = self.client.get("/data/pipe-calc/?line=protect&size=110")
        html = resp.content.decode()
        self.assertIn(
            'class="pcx-table erp-table-profile--compact" id="pcx-depot-table"',
            html,
        )
        self.assertIn(
            'class="pcx-table pcx-table-calc erp-table-profile--report"',
            html,
        )
        self.assertIn(
            'class="pcx-defs-table erp-table-profile--data-entry"',
            html,
        )
        self.assertEqual(html.count("erp-table-profile--"), 3)

        plan_template = (
            settings.BASE_DIR / "templates" / "planning" / "plan_list.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("erp-table-profile--", plan_template)

    def test_profile_css_uses_local_density_tokens_and_existing_gray_header(self):
        css = (settings.BASE_DIR / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )
        for profile in ("compact", "report", "data-entry"):
            marker = f".erp-table-profile--{profile} {{"
            start = css.index(marker)
            block = css[start : css.index("}", start)]
            self.assertIn("--table-row-height:", block)
            self.assertIn("--table-header-height:", block)
            self.assertIn("--table-header-bg: #fafaf9;", block)

    def test_duplicate_pipe_card_headings_are_absent(self):
        resp = self.client.get("/data/pipe-calc/?line=protect&size=110")
        html = resp.content.decode()
        self.assertEqual(html.count("برنامه دپو و نیاز تولید"), 1)
        self.assertEqual(html.count("تعاریف اولیه"), 1)
        self.assertEqual(html.count("جدول محاسباتی لوله و مواد"), 1)
        self.assertIn('id="pcx-result-title"', html)
        self.assertIn(
            'resultTitle.textContent = "نتایج محاسبات سایز " + String(matrix.size_mm)',
            html,
        )

    def test_results_show_calculated_size_without_cross_tab_selectors(self):
        resp = self.client.get("/data/pipe-calc/?line=protect&size=110")
        html = resp.content.decode()
        calculation_start = html.index('data-pcx-panel="calculation"')
        definitions_start = html.index('data-pcx-panel="definitions"')
        calculation_panel = html[calculation_start:definitions_start]
        self.assertIn('id="pcx-result-title"', calculation_panel)
        self.assertIn("هنوز محاسبه‌ای انجام نشده است", calculation_panel)
        self.assertNotIn('id="pcx-size"', calculation_panel)
        self.assertNotIn('id="pcx-qty-source"', calculation_panel)
        self.assertIn('resultTitle.textContent = "نتایج محاسبات سایز " + String(matrix.size_mm)', html)
        self.assertNotIn('resultTitle.textContent = "نتایج محاسبات سایز Ø', html)

    def test_definitions_size_state_is_independent_and_all_fields_remain(self):
        resp = self.client.get("/data/pipe-calc/?line=protect&size=110")
        html = resp.content.decode()
        self.assertIn("var defsMatrix = matrix;", html)
        self.assertIn('defsSizeSel.addEventListener("change"', html)
        self.assertIn("defsMatrix = data.matrix || defsMatrix;", html)
        self.assertNotIn("matrix = data.matrix || defsMatrix;", html)
        self.assertIn('size_mm: parseInt(defsSizeSel.value, 10)', html)
        for field in (
            "billing_cycle_seconds",
            "billing_cavities",
            "kg_per_meter",
            "cover_g_per_m",
            "socket_cap_bag_qty",
            "pipe_cap_bag_qty",
            "spacer_bag_qty",
            "oring_bag_qty",
        ):
            self.assertIn('sizeDefInput("' + field + '"', html)

    def test_selected_size_context_is_used_across_workspace(self):
        resp = self.client.get("/data/pipe-calc/?line=protect&size=200")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["selected_size_mm"], 200)
        self.assertEqual(resp.context["matrix"]["size_mm"], 200)
        self.assertEqual(resp.context["matrix"]["size_defs"]["size_mm"], 200)
        self.assertTrue(
            all(row["length_code"] for row in resp.context["matrix"]["depot_rows"])
        )

    def test_matrix_run_endpoint(self):
        resp = self.client.post(
            "/data/pipe-calc/run/",
            data='{"line":"protect","size_mm":75,"mode":"matrix","qty_source":"deduct_stock"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        matrix = payload["matrix"]
        self.assertTrue(matrix["depot_rows"])
        self.assertEqual(matrix["depot_rows"][-1]["length_code"], "coupler")
        self.assertEqual(matrix["depot_rows"][-1]["label"], "رابط")
        self.assertIn("production", matrix)
        self.assertIn("totals", matrix["production"])
        totals = matrix["production"]["totals"]
        self.assertIn("line_hours", totals)
        self.assertIn("line_days", totals)
        self.assertIn("oring_bags", totals)
        self.assertEqual(matrix["size_defs"]["billing_cycle_seconds"], 32.0)

    def test_run_endpoint_legacy_scenario(self):
        resp = self.client.post(
            "/data/pipe-calc/run/",
            {
                "line": "protect",
                "size_mm": "75",
                "length_code": "100cm_1s",
                "pieces": "25",
                "voucher_qty": "5",
            },
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertIn("time", payload["result"])
        self.assertIn("depot", payload["result"])
        self.assertGreater(payload["result"]["time"]["total"]["seconds"], 0)

    def test_tabs_and_title(self):
        resp = self.client.get("/data/pipe-calc/")
        self.assertContains(resp, "محاسبات تولید")
        self.assertContains(resp, "لوله‌های جنرال سایلنت")
        self.assertContains(resp, "لوله‌های سایلنت ۱۰")
        self.assertContains(resp, "نوار آبیاری (تیپ)")
        self.assertContains(resp, "لوله فلت (PC)")
        self.assertNotContains(resp, "اسکلت")


class PipeCalcMatrixEngineTests(TestCase):
    def test_depot_matrix_row_math(self):
        from catalog.pipe_calc.matrix import calc_depot_matrix_row

        row = calc_depot_matrix_row(
            length_code="100cm_1s",
            label="۱ متری",
            depot_ceiling=1000,
            stock=700,
            voucher=100,
            avg_monthly_sales=200,
        )
        self.assertEqual(row.remaining_after_voucher, 600)
        self.assertEqual(row.months_remaining, 3.0)
        self.assertEqual(row.depot_remaining_pct, 60.0)
        self.assertEqual(row.deduct_from_depot_stock, 300)
        self.assertEqual(row.deduct_from_depot_remaining, 400)
        self.assertEqual(row.required_qty, 300)


class PipeCalcDefsApiTests(TestCase):
    def setUp(self):
        seed_pipe_calc_defaults()
        self.user = User.objects.create_user("pipedefs", password="pass12345")
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"role": Role.PLANNING_MANAGER},
        )
        self.client = Client()
        self.client.login(username="pipedefs", password="pass12345")

    def test_save_defs_and_readonly_hub(self):
        profile = PipeSizeProfile.objects.filter(line__code=LINE_PROTECT, size_mm=110).first()
        self.assertIsNotNone(profile)
        cut = profile.length_cuts.filter(is_active=True).first()
        resp = self.client.post(
            "/data/pipe-calc/defs/",
            data=__import__("json").dumps(
                {
                    "rows": [
                        {
                            "id": cut.id,
                            "depot_ceiling": 1500,
                            "avg_monthly_sales": 12.5,
                            "line_speed_m_per_min": 6.25,
                        }
                    ]
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))
        cut.refresh_from_db()
        self.assertEqual(cut.depot_ceiling, 1500)
        self.assertEqual(float(cut.line_speed_m_per_min), 6.25)
        hub = self.client.get("/data/pipe-calc/?line=protect&size=110")
        self.assertContains(hub, "تعاریف اولیه")
        self.assertContains(hub, 'data-pcx-panel="definitions"')
        self.assertNotContains(hub, "pcx-defs-dialog")
        self.assertContains(hub, "کد کالا")
        self.assertNotContains(hub, 'data-field="depot_ceiling"')
