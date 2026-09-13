"""Tests for the offline, deterministic auto-planner engine."""

from __future__ import annotations

import jdatetime
from django.test import TestCase

from catalog.models import (
    Machine,
    MoldOption,
    Product,
    ProductGroup,
    ProductMold,
    ProductionUnit,
    ProductSubGroup,
)
from production.models import FittingProduction
from planning.models import CustomerOrder
from planning import auto_planner


class AutoPlannerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unit = ProductionUnit.objects.create(number=1, name="واحد ۱")
        cls.m1 = Machine.objects.create(unit=cls.unit, machine_type="injection", number="101")
        cls.m2 = Machine.objects.create(unit=cls.unit, machine_type="injection", number="102")
        cls.group = ProductGroup.objects.create(name="پیچی")
        cls.sub = ProductSubGroup.objects.create(group=cls.group, name="زانو")

    def _product(self, code, name="کالا", cavities=1, cycle=30, stock=0):
        return Product.objects.create(
            code=code, name=name, subgroup=self.sub,
            main_cavities=cavities, last_cycle=cycle, stock_finished=stock,
        )

    def _order(self, product, qty, *, priority=100, due=None):
        return CustomerOrder.objects.create(
            product_code=product.code, product_name=product.name, product=product,
            quantity=qty, priority=priority, delivery_date=due, is_active=True,
        )

    def _history(self, product, machine, *, cavities=1, cycle=30, day=1):
        return FittingProduction.objects.create(
            unit=self.unit, machine=machine, product=product,
            date=jdatetime.date(1405, 6, day), active_cavities=cavities,
            shot_cycle=cycle, produced_quantity=1,
        )

    def _mold(self, code, label, copies=1):
        return MoldOption.objects.create(code=code, label=label, copies=copies)

    def _link(self, product, mold, slot=1):
        return ProductMold.objects.create(product=product, mold=mold, slot=slot)

    def test_no_mold_defined_is_flagged(self):
        p = self._product("A1")
        self._order(p, 100)
        props = auto_planner.build_auto_proposals()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].produce_qty, 0)
        self.assertTrue(any("قالب" in w for w in props[0].warnings))

    def test_history_machine_and_mold_assigned(self):
        p = self._product("B1", cavities=2, cycle=20)
        self._order(p, 500)
        self._history(p, self.m2, cavities=2, cycle=20, day=5)
        mold = self._mold("MB1", "قالب B")
        self._link(p, mold)
        props = auto_planner.build_auto_proposals()
        self.assertEqual(len(props), 1)
        prop = props[0]
        self.assertEqual(prop.machine_id, self.m2.pk)  # from history, not m1
        self.assertEqual(prop.mold_id, mold.pk)
        self.assertEqual(prop.produce_qty, 500)
        self.assertTrue(any("سابقه" in r for r in prop.reasons))

    def test_capacity_caps_without_splitting(self):
        # cavities=1, cycle=3600s => 1 part/hour => <=96 parts/week on one machine.
        p = self._product("C1", cavities=1, cycle=3600)
        self._order(p, 200)
        self._history(p, self.m1, cavities=1, cycle=3600)
        self._link(p, self._mold("MC1", "قالب C"))
        props = auto_planner.build_auto_proposals()
        prop = props[0]
        self.assertEqual(prop.machine_id, self.m1.pk)
        self.assertLessEqual(prop.produce_qty, 96)
        self.assertGreater(prop.produce_qty, 0)
        self.assertTrue(any("ظرفیت" in w for w in prop.warnings))
        # No second machine was used for the remainder (no splitting).
        self.assertEqual(sum(1 for x in props if x.product_code == "C1"), 1)

    def test_mold_copy_exclusivity_defers_second_product(self):
        shared = self._mold("SHARED", "قالب مشترک", copies=1)
        pa = self._product("D1")
        pb = self._product("D2")
        self._order(pa, 50, priority=1)   # higher priority -> planned first
        self._order(pb, 50, priority=2)
        self._history(pa, self.m1)        # A prefers machine 1
        self._history(pb, self.m2)        # B prefers machine 2 (different)
        self._link(pa, shared)
        self._link(pb, shared)
        props = {p.product_code: p for p in auto_planner.build_auto_proposals()}
        self.assertEqual(props["D1"].machine_id, self.m1.pk)
        self.assertEqual(props["D1"].mold_id, shared.pk)
        # Only one physical copy -> B cannot take a new machine; it is deferred.
        self.assertEqual(props["D2"].produce_qty, 0)
        self.assertIsNone(props["D2"].machine_id)

    def test_two_copies_allow_parallel(self):
        shared = self._mold("SHARED2", "قالب دو نسخه", copies=2)
        pa = self._product("E1")
        pb = self._product("E2")
        self._order(pa, 50, priority=1)
        self._order(pb, 50, priority=2)
        self._history(pa, self.m1)
        self._history(pb, self.m2)
        self._link(pa, shared)
        self._link(pb, shared)
        props = {p.product_code: p for p in auto_planner.build_auto_proposals()}
        self.assertEqual(props["E1"].produce_qty, 50)
        self.assertEqual(props["E2"].produce_qty, 50)
        self.assertNotEqual(props["E1"].machine_id, props["E2"].machine_id)

    def test_due_date_orders_before_priority(self):
        soon = jdatetime.date(1405, 6, 10)
        later = jdatetime.date(1405, 6, 20)
        p_late_due = self._product("F1")
        p_soon_due = self._product("F2")
        # F1 has better priority but a later due date; F2 is due sooner.
        self._order(p_late_due, 10, priority=1, due=later)
        self._order(p_soon_due, 10, priority=9, due=soon)
        for p in (p_late_due, p_soon_due):
            self._history(p, self.m1)
            self._link(p, self._mold(f"M{p.code}", f"قالب {p.code}"))
        props = auto_planner.build_auto_proposals()
        self.assertEqual(props[0].product_code, "F2")  # sooner due date wins

    def test_no_history_falls_back_with_warning(self):
        p = self._product("G1")
        self._order(p, 20)
        self._link(p, self._mold("MG1", "قالب G"))
        props = auto_planner.build_auto_proposals()
        prop = props[0]
        self.assertIn(prop.machine_id, {self.m1.pk, self.m2.pk})
        self.assertEqual(prop.produce_qty, 20)
        self.assertTrue(any("سابقه" in w for w in prop.warnings))

    def test_friday_adds_capacity(self):
        p = self._product("H1", cavities=1, cycle=3600)
        self._order(p, 200)
        self._history(p, self.m1, cavities=1, cycle=3600)
        self._link(p, self._mold("MH1", "قالب H"))
        base = auto_planner.build_auto_proposals()[0].produce_qty
        withfri = auto_planner.build_auto_proposals(friday_machine_ids={self.m1.pk})[0].produce_qty
        self.assertLessEqual(base, 96)
        self.assertGreater(withfri, base)  # Friday raises the weekly budget
