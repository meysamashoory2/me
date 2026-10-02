from decimal import Decimal
from datetime import datetime
from unittest.mock import patch

import jdatetime
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .models import (
    Machine,
    MoldMachineCompatibility,
    MoldMachineCompatibilityHistory,
    MoldMachinePerformance,
    MoldOption,
    PhysicalMold,
    Product,
    ProductGroup,
    ProductKind,
    ProductMold,
    ProductSubGroup,
    ProductionUnit,
)


class MoldFoundationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unit = ProductionUnit.objects.create(number=91, name="Injection test unit")
        cls.machine_a = Machine.objects.create(unit=cls.unit, number="A-1")
        cls.machine_b = Machine.objects.create(unit=cls.unit, number="A-2")
        cls.group = ProductGroup.objects.create(name="Step 5 test group", kind=ProductKind.FITTING)
        cls.subgroup = ProductSubGroup.objects.create(group=cls.group, name="Step 5 components")
        cls.product = Product.objects.create(
            code="STEP5-COMP-001",
            name="Step 5 component",
            subgroup=cls.subgroup,
        )
        cls.mold_type = MoldOption.objects.create(
            code="STEP5-TYPE",
            label="Step 5 legacy mold type",
            copies=2,
            change_time_hours=Decimal("2.00"),
        )
        cls.physical_mold = PhysicalMold.objects.create(
            code="STEP5-PHYSICAL-001",
            mold_type=cls.mold_type,
        )


class PhysicalMoldTests(MoldFoundationTestCase):
    def test_code_is_unique(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PhysicalMold.objects.create(
                code=self.physical_mold.code,
                mold_type=self.mold_type,
            )

    def test_all_operational_statuses_are_valid(self):
        for index, status in enumerate(PhysicalMold.Status.values, start=1):
            mold = PhysicalMold(
                code=f"STEP5-STATUS-{index}",
                mold_type=self.mold_type,
                status=status,
            )
            mold.full_clean()

    def test_invalid_operational_status_is_rejected(self):
        mold = PhysicalMold(
            code="STEP5-INVALID-STATUS",
            mold_type=self.mold_type,
            status="broken_unknown_state",
        )
        with self.assertRaises(ValidationError):
            mold.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PhysicalMold.objects.create(
                code="STEP5-INVALID-STATUS-DB",
                mold_type=self.mold_type,
                status="broken_unknown_state",
            )

    def test_under_repair_supports_known_or_unknown_ready_date(self):
        unknown = PhysicalMold.objects.create(
            code="STEP5-REPAIR-UNKNOWN",
            mold_type=self.mold_type,
            status=PhysicalMold.Status.UNDER_REPAIR,
        )
        known = PhysicalMold.objects.create(
            code="STEP5-REPAIR-KNOWN",
            mold_type=self.mold_type,
            status=PhysicalMold.Status.UNDER_REPAIR,
            estimated_ready_date=jdatetime.date(1405, 8, 1),
        )
        self.assertIsNone(unknown.estimated_ready_date)
        self.assertEqual(known.estimated_ready_date, jdatetime.date(1405, 8, 1))

    def test_setup_time_uses_physical_override_or_three_hour_foundation_default(self):
        self.assertEqual(self.physical_mold.effective_setup_time_hours, Decimal("3.00"))
        self.physical_mold.setup_time_hours = Decimal("4.50")
        self.assertEqual(self.physical_mold.effective_setup_time_hours, Decimal("4.50"))

    def test_legacy_mold_and_product_mold_behavior_are_preserved(self):
        link = ProductMold.objects.create(
            product=self.product,
            mold=self.mold_type,
            slot=2,
        )
        self.mold_type.refresh_from_db()
        self.assertEqual(self.mold_type.copies, 2)
        self.assertEqual(self.mold_type.change_time_hours, Decimal("2.00"))
        self.assertEqual(link.mold, self.mold_type)
        self.assertEqual(link.product, self.product)
        self.assertEqual(self.physical_mold.mold_type, self.mold_type)


class MoldMachineCompatibilityTests(MoldFoundationTestCase):
    def test_pair_is_unique(self):
        MoldMachineCompatibility.objects.create(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            MoldMachineCompatibility.objects.create(
                physical_mold=self.physical_mold,
                machine=self.machine_a,
            )

    def test_exact_business_states_are_supported_and_distinct(self):
        self.assertEqual(
            set(MoldMachineCompatibility.Status.values),
            {"confirmed", "needs_review", "unknown", "incompatible"},
        )
        self.assertNotEqual(
            MoldMachineCompatibility.Status.UNKNOWN,
            MoldMachineCompatibility.Status.INCOMPATIBLE,
        )
        for index, status in enumerate(MoldMachineCompatibility.Status.values, start=1):
            machine = Machine.objects.create(unit=self.unit, number=f"STATE-{index}")
            record = MoldMachineCompatibility.objects.create_with_status(
                physical_mold=self.physical_mold,
                machine=machine,
                status=status,
            )
            self.assertEqual(record.status, status)

    def test_invalid_state_is_rejected_by_supported_creation_path(self):
        with self.assertRaises(ValueError):
            MoldMachineCompatibility.objects.create_with_status(
                physical_mold=self.physical_mold,
                machine=self.machine_a,
                status="not_a_business_state",
            )

    def test_many_to_many_cardinality_is_supported(self):
        second_mold = PhysicalMold.objects.create(
            code="STEP5-PHYSICAL-002",
            mold_type=self.mold_type,
        )
        MoldMachineCompatibility.objects.bulk_create([
            MoldMachineCompatibility(
                physical_mold=self.physical_mold,
                machine=self.machine_a,
            ),
            MoldMachineCompatibility(
                physical_mold=self.physical_mold,
                machine=self.machine_b,
            ),
            MoldMachineCompatibility(
                physical_mold=second_mold,
                machine=self.machine_a,
            ),
        ])
        self.assertEqual(self.physical_mold.machine_compatibilities.count(), 2)
        self.assertEqual(self.machine_a.mold_compatibilities.count(), 2)

    def test_status_change_creates_immutable_audit_evidence(self):
        user = get_user_model().objects.create_user(username="step5-manager")
        compatibility = MoldMachineCompatibility.objects.create(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
            status=MoldMachineCompatibility.Status.UNKNOWN,
        )
        event = compatibility.change_status(
            MoldMachineCompatibility.Status.INCOMPATIBLE,
            changed_by=user,
            reason="Machine platen dimensions do not fit.",
        )
        compatibility.refresh_from_db()
        self.assertEqual(compatibility.status, MoldMachineCompatibility.Status.INCOMPATIBLE)
        self.assertEqual(event.previous_status, MoldMachineCompatibility.Status.UNKNOWN)
        self.assertEqual(event.new_status, MoldMachineCompatibility.Status.INCOMPATIBLE)
        self.assertEqual(event.changed_by, user)
        self.assertEqual(event.reason, "Machine platen dimensions do not fit.")
        self.assertIsNotNone(event.changed_at)
        event.reason = "Rewritten"
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            event.delete()

    def test_noop_status_change_does_not_create_false_history(self):
        compatibility = MoldMachineCompatibility.objects.create(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
        )
        compatibility.change_status(MoldMachineCompatibility.Status.CONFIRMED)
        self.assertEqual(MoldMachineCompatibilityHistory.objects.count(), 1)
        event = compatibility.change_status(MoldMachineCompatibility.Status.CONFIRMED)
        self.assertIsNone(event)
        self.assertEqual(MoldMachineCompatibilityHistory.objects.count(), 1)

    def test_direct_instance_save_cannot_change_status(self):
        compatibility = MoldMachineCompatibility.objects.create(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
        )
        compatibility.status = MoldMachineCompatibility.Status.CONFIRMED
        with self.assertRaises(ValueError):
            compatibility.save()
        compatibility.refresh_from_db()
        self.assertEqual(compatibility.status, MoldMachineCompatibility.Status.UNKNOWN)
        self.assertFalse(MoldMachineCompatibilityHistory.objects.exists())

    def test_bulk_status_update_is_rejected(self):
        compatibility = MoldMachineCompatibility.objects.create(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
        )
        with self.assertRaises(ValueError):
            MoldMachineCompatibility.objects.filter(pk=compatibility.pk).update(
                status=MoldMachineCompatibility.Status.CONFIRMED
            )
        compatibility.refresh_from_db()
        self.assertEqual(compatibility.status, MoldMachineCompatibility.Status.UNKNOWN)

    def test_new_record_must_start_unknown(self):
        compatibility = MoldMachineCompatibility.objects.create(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
        )
        self.assertEqual(compatibility.status, MoldMachineCompatibility.Status.UNKNOWN)
        with self.assertRaises(ValueError):
            MoldMachineCompatibility.objects.create(
                physical_mold=self.physical_mold,
                machine=self.machine_b,
                status=MoldMachineCompatibility.Status.CONFIRMED,
            )

    def test_supported_confirmed_creation_is_audited(self):
        user = get_user_model().objects.create_user(username="step5-confirmed")
        compatibility = MoldMachineCompatibility.objects.create_with_status(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
            status=MoldMachineCompatibility.Status.CONFIRMED,
            changed_by=user,
            reason="Factory validation completed.",
        )
        event = compatibility.history.get()
        self.assertEqual(event.previous_status, MoldMachineCompatibility.Status.UNKNOWN)
        self.assertEqual(event.new_status, MoldMachineCompatibility.Status.CONFIRMED)
        self.assertEqual(event.changed_by, user)
        self.assertEqual(event.reason, "Factory validation completed.")

    def test_supported_incompatible_creation_is_audited(self):
        compatibility = MoldMachineCompatibility.objects.create_with_status(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
            status=MoldMachineCompatibility.Status.INCOMPATIBLE,
            reason="Physical dimensions do not fit.",
        )
        event = compatibility.history.get()
        self.assertEqual(event.previous_status, MoldMachineCompatibility.Status.UNKNOWN)
        self.assertEqual(event.new_status, MoldMachineCompatibility.Status.INCOMPATIBLE)

    def test_admin_change_uses_audited_transition(self):
        user = get_user_model().objects.create_user(username="step5-admin-change")
        request = RequestFactory().post("/admin/catalog/moldmachinecompatibility/")
        request.user = user
        compatibility = MoldMachineCompatibility.objects.create(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
        )
        compatibility.status = MoldMachineCompatibility.Status.CONFIRMED
        model_admin = admin.site._registry[MoldMachineCompatibility]
        model_admin.save_model(request, compatibility, form=None, change=True)
        compatibility.refresh_from_db()
        event = compatibility.history.get()
        self.assertEqual(compatibility.status, MoldMachineCompatibility.Status.CONFIRMED)
        self.assertEqual(event.previous_status, MoldMachineCompatibility.Status.UNKNOWN)
        self.assertEqual(event.new_status, MoldMachineCompatibility.Status.CONFIRMED)
        self.assertEqual(event.changed_by, user)
        self.assertEqual(event.reason, "")

    def test_admin_non_unknown_creation_is_audited(self):
        user = get_user_model().objects.create_user(username="step5-admin-create")
        request = RequestFactory().post("/admin/catalog/moldmachinecompatibility/add/")
        request.user = user
        compatibility = MoldMachineCompatibility(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
            status=MoldMachineCompatibility.Status.INCOMPATIBLE,
        )
        model_admin = admin.site._registry[MoldMachineCompatibility]
        model_admin.save_model(request, compatibility, form=None, change=False)
        compatibility.refresh_from_db()
        event = compatibility.history.get()
        self.assertEqual(compatibility.status, MoldMachineCompatibility.Status.INCOMPATIBLE)
        self.assertEqual(event.previous_status, MoldMachineCompatibility.Status.UNKNOWN)
        self.assertEqual(event.new_status, MoldMachineCompatibility.Status.INCOMPATIBLE)

    def test_history_failure_rolls_back_status_change(self):
        compatibility = MoldMachineCompatibility.objects.create(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
        )
        with patch.object(
            MoldMachineCompatibilityHistory,
            "save",
            side_effect=RuntimeError("history unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                compatibility.change_status(MoldMachineCompatibility.Status.CONFIRMED)
        compatibility.refresh_from_db()
        self.assertEqual(compatibility.status, MoldMachineCompatibility.Status.UNKNOWN)
        self.assertFalse(MoldMachineCompatibilityHistory.objects.exists())

    def test_history_queryset_update_and_delete_are_rejected(self):
        compatibility = MoldMachineCompatibility.objects.create_with_status(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
            status=MoldMachineCompatibility.Status.CONFIRMED,
        )
        event = compatibility.history.get()
        with self.assertRaises(ValueError):
            MoldMachineCompatibilityHistory.objects.filter(pk=event.pk).update(
                reason="rewritten"
            )
        with self.assertRaises(ValueError):
            MoldMachineCompatibilityHistory.objects.filter(pk=event.pk).delete()

    def test_database_alias_is_preserved_by_supported_creation_and_change(self):
        compatibility = MoldMachineCompatibility.objects.using("default").create_with_status(
            physical_mold=self.physical_mold,
            machine=self.machine_a,
            status=MoldMachineCompatibility.Status.NEEDS_REVIEW,
            reason="Alias test",
        )
        self.assertEqual(compatibility._state.db, "default")
        self.assertEqual(compatibility.history.get()._state.db, "default")


class MoldMachinePerformanceTests(MoldFoundationTestCase):
    @staticmethod
    def effective_at(hour, minute=0):
        return timezone.make_aware(datetime(2026, 10, 1, hour, minute))

    def create_performance(self, **overrides):
        values = {
            "product": self.product,
            "physical_mold": self.physical_mold,
            "machine": self.machine_a,
            "cycle_seconds": 30,
            "active_cavities": 2,
            "effective_from": self.effective_at(8),
        }
        values.update(overrides)
        return MoldMachinePerformance.objects.create(**values)

    def test_positive_cycle_and_cavities_are_accepted(self):
        record = self.create_performance()
        self.assertEqual(record.cycle_seconds, 30)
        self.assertEqual(record.active_cavities, 2)

    def test_zero_and_negative_cycle_are_rejected_by_database(self):
        for value in (0, -1):
            with self.assertRaises(IntegrityError), transaction.atomic():
                self.create_performance(cycle_seconds=value)

    def test_zero_and_negative_cavities_are_rejected_by_database(self):
        for value in (0, -1):
            with self.assertRaises(IntegrityError), transaction.atomic():
                self.create_performance(active_cavities=value)

    def test_same_date_different_times_are_retained(self):
        first = self.create_performance()
        later = self.create_performance(
            cycle_seconds=32,
            effective_from=self.effective_at(14, 30),
        )
        self.assertNotEqual(first.pk, later.pk)
        self.assertEqual(first.effective_from.date(), later.effective_from.date())
        self.assertEqual(
            MoldMachinePerformance.objects.filter(
                product=self.product,
                physical_mold=self.physical_mold,
                machine=self.machine_a,
            ).count(),
            2,
        )

    def test_exact_effective_timestamp_is_unique(self):
        self.create_performance()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_performance(cycle_seconds=31)

    def test_latest_valid_record_respects_intraday_change(self):
        morning = self.create_performance(cycle_seconds=100)
        afternoon = self.create_performance(
            cycle_seconds=92,
            effective_from=self.effective_at(14, 30),
        )
        base = MoldMachinePerformance.objects.filter(
            product=self.product,
            physical_mold=self.physical_mold,
            machine=self.machine_a,
        )
        before_change = (
            base.filter(effective_from__lte=self.effective_at(12))
            .order_by("-effective_from")
            .first()
        )
        after_change = (
            base.filter(effective_from__lte=self.effective_at(15))
            .order_by("-effective_from")
            .first()
        )
        self.assertEqual(before_change.pk, morning.pk)
        self.assertEqual(after_change.pk, afternoon.pk)
        self.assertEqual(before_change.cycle_seconds, 100)
        self.assertEqual(after_change.cycle_seconds, 92)
