import jdatetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserProfile
from catalog.models import (
    Machine,
    MoldMachineCompatibility,
    MoldOption,
    PhysicalMold,
    Product,
    ProductGroup,
    ProductSubGroup,
    ProductionUnit,
)
from production.models import ProductionProgram

from .models import (
    PhysicalMoldReservation,
    PlanApprovalRevision,
    PlannedProductionPath,
    Weekday,
    WeeklyPlan,
    WeeklyPlanItem,
    WeeklyPlanLine,
)
from .path_foundation import (
    active_reservation_overlaps,
    classify_compatibility,
    classify_mold_status,
    validate_proposed_path,
)


class PathFoundationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="slice2a-manager", password="test-only"
        )
        profile, _ = UserProfile.objects.get_or_create(user=cls.user)
        profile.role = Role.PLANNING_MANAGER
        profile.save(update_fields=["role"])
        cls.group = ProductGroup.objects.create(name="Slice 2A group")
        cls.subgroup = ProductSubGroup.objects.create(
            name="Slice 2A subgroup", group=cls.group
        )
        cls.unit = ProductionUnit.objects.create(number=920, name="Slice 2A unit")
        cls.machine_a = Machine.objects.create(number="S2A-A", unit=cls.unit)
        cls.machine_b = Machine.objects.create(number="S2A-B", unit=cls.unit)
        cls.product = Product.objects.create(
            code="SLICE-2A-PRODUCT", name="Slice 2A product", subgroup=cls.subgroup
        )
        cls.mold_type = MoldOption.objects.create(
            code="SLICE-2A-MOLD-TYPE", label="Slice 2A mold type", copies=4
        )
        cls.mold_a = PhysicalMold.objects.create(
            code="SLICE-2A-MOLD-A", mold_type=cls.mold_type
        )
        cls.mold_b = PhysicalMold.objects.create(
            code="SLICE-2A-MOLD-B", mold_type=cls.mold_type
        )
        cls.plan = WeeklyPlan.objects.create(
            program_number="SLICE-2A-PLAN",
            date=jdatetime.date(1405, 2, 1),
            created_by=cls.user,
        )
        cls.item = WeeklyPlanItem.objects.create(
            plan=cls.plan,
            subgroup=cls.subgroup,
            unit=cls.unit,
            machine=cls.machine_a,
            product=cls.product,
            mold=cls.mold_type,
            mold_change_weekday=Weekday.SHANBE,
            mold_change_date=jdatetime.date(1405, 2, 1),
        )
        cls.requirement = WeeklyPlanLine.objects.create(
            item=cls.item,
            mold=cls.mold_type,
            quantity=40000,
            cycle=30,
        )

    def create_path(self, **overrides):
        values = {
            "requirement": self.requirement,
            "machine": self.machine_a,
            "physical_mold": self.mold_a,
            "planned_share_quantity": 20000,
            "planned_start_date": jdatetime.date(1405, 2, 1),
            "planned_end_date": jdatetime.date(1405, 2, 3),
            "sequence": self.requirement.planned_paths.count() + 1,
            "created_by": self.user,
            "updated_by": self.user,
        }
        values.update(overrides)
        return PlannedProductionPath.objects.create(**values)

    def create_revision(self, **overrides):
        values = {
            "plan": self.plan,
            "revision_number": self.plan.approval_revisions.count() + 1,
            "status": PlanApprovalRevision.Status.ACTIVE,
            "approved_by": self.user,
            "approved_at": timezone.now(),
        }
        values.update(overrides)
        return PlanApprovalRevision.objects.create(**values)

    def create_reservation(self, path=None, **overrides):
        path = path or self.create_path()
        revision = overrides.pop("approval_revision", None) or self.create_revision()
        values = {
            "approval_revision": revision,
            "source_path": path,
            "requirement": path.requirement,
            "machine": path.machine,
            "physical_mold": path.physical_mold,
            "approved_planned_share_quantity": path.planned_share_quantity,
            "reserved_start_date": path.planned_start_date,
            "reserved_end_date": path.planned_end_date,
            "compatibility_status_snapshot": MoldMachineCompatibility.Status.UNKNOWN,
            "mold_status_snapshot": path.physical_mold.status,
            "estimated_ready_date_snapshot": path.physical_mold.estimated_ready_date,
            "status": PhysicalMoldReservation.Status.ACTIVE,
            "activated_at": timezone.now(),
        }
        values.update(overrides)
        return PhysicalMoldReservation.objects.create(**values)


class PlannedProductionPathTests(PathFoundationTestCase):
    def test_multiple_paths_and_all_assignment_total_variants_are_informational(self):
        self.create_path(planned_share_quantity=20000, sequence=1)
        self.create_path(
            machine=self.machine_b,
            physical_mold=self.mold_b,
            planned_share_quantity=15000,
            sequence=2,
        )
        self.assertEqual(self.requirement.assigned_planned_share_total, 35000)
        self.assertEqual(self.requirement.assignment_difference, -5000)
        self.assertEqual(self.requirement.unassigned_quantity, 5000)
        self.assertEqual(self.requirement.overassigned_quantity, 0)

        self.requirement.planned_paths.filter(sequence=2).update(
            planned_share_quantity=20000
        )
        self.assertEqual(self.requirement.assigned_planned_share_total, 40000)
        self.assertEqual(self.requirement.assignment_difference, 0)

        self.requirement.planned_paths.filter(sequence=2).update(
            planned_share_quantity=25000
        )
        self.assertEqual(self.requirement.assigned_planned_share_total, 45000)
        self.assertEqual(self.requirement.assignment_difference, 5000)
        self.assertEqual(self.requirement.unassigned_quantity, 0)
        self.assertEqual(self.requirement.overassigned_quantity, 5000)

    def test_zero_share_same_machine_different_molds_and_parallel_paths_are_valid(self):
        first = self.create_path(planned_share_quantity=0, sequence=1)
        second = self.create_path(physical_mold=self.mold_b, sequence=2)
        third_mold = PhysicalMold.objects.create(
            code="SLICE-2A-MOLD-C", mold_type=self.mold_type
        )
        third = self.create_path(
            machine=self.machine_b, physical_mold=third_mold, sequence=3
        )
        self.assertEqual(first.planned_share_quantity, 0)
        self.assertEqual(first.machine, second.machine)
        self.assertNotEqual(first.physical_mold, second.physical_mold)
        self.assertNotEqual(first.machine, third.machine)

    def test_inclusive_same_day_is_valid_and_reversed_range_is_rejected(self):
        same_day = self.create_path(
            planned_start_date=jdatetime.date(1405, 2, 2),
            planned_end_date=jdatetime.date(1405, 2, 2),
        )
        same_day.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_path(
                planned_start_date=jdatetime.date(1405, 2, 3),
                planned_end_date=jdatetime.date(1405, 2, 2),
                sequence=2,
            )

    def test_master_references_are_protected(self):
        path = self.create_path()
        with self.assertRaises(ProtectedError):
            path.machine.delete()
        with self.assertRaises(ProtectedError):
            path.physical_mold.delete()

    def test_inactive_paths_do_not_contribute_to_totals(self):
        self.create_path(planned_share_quantity=10000, is_active=False)
        self.assertEqual(self.requirement.assigned_planned_share_total, 0)


class SnapshotAndOverlapTests(PathFoundationTestCase):
    def test_only_one_active_revision_is_allowed_per_plan(self):
        self.create_revision()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_revision(revision_number=2)

    def test_activated_revision_approval_evidence_is_immutable_across_orm_paths(self):
        revision = self.create_revision()
        original_approved_at = revision.approved_at
        revision.approved_at = timezone.now()
        with self.assertRaises(ValueError):
            revision.save()

        with self.assertRaises(ValueError):
            PlanApprovalRevision.objects.filter(pk=revision.pk).update(
                approved_at=timezone.now()
            )

        revision.refresh_from_db()
        revision.approved_at = timezone.now()
        with self.assertRaises(ValueError):
            PlanApprovalRevision.objects.bulk_update([revision], ["approved_at"])

        revision.refresh_from_db()
        self.assertEqual(revision.approved_at, original_approved_at)

    def test_preparing_revision_can_receive_approval_evidence(self):
        revision = self.create_revision(
            status=PlanApprovalRevision.Status.PREPARING,
            approved_by=None,
            approved_at=None,
        )
        approved_at = timezone.now()
        updated = PlanApprovalRevision.objects.filter(pk=revision.pk).update(
            status=PlanApprovalRevision.Status.ACTIVE,
            approved_by=self.user,
            approved_at=approved_at,
        )
        self.assertEqual(updated, 1)
        revision.refresh_from_db()
        self.assertEqual(revision.status, PlanApprovalRevision.Status.ACTIVE)
        self.assertEqual(revision.approved_by, self.user)
        self.assertEqual(revision.approved_at, approved_at)

    def test_only_preparing_revisions_are_deletable(self):
        preparing = self.create_revision(
            status=PlanApprovalRevision.Status.PREPARING,
            approved_by=None,
            approved_at=None,
        )
        preparing.delete()
        self.assertFalse(PlanApprovalRevision.objects.filter(pk=preparing.pk).exists())

        protected = self.create_revision(revision_number=2)
        with self.assertRaises(ValueError):
            protected.delete()
        with self.assertRaises(ValueError):
            PlanApprovalRevision.objects.filter(pk=protected.pk).delete()
        self.assertTrue(PlanApprovalRevision.objects.filter(pk=protected.pk).exists())

    def test_mixed_revision_queryset_delete_is_rejected_without_partial_deletion(self):
        preparing = self.create_revision(
            status=PlanApprovalRevision.Status.PREPARING,
            approved_by=None,
            approved_at=None,
        )
        other_plan = WeeklyPlan.objects.create(
            program_number="SLICE-2A-OTHER-PLAN",
            date=jdatetime.date(1405, 2, 8),
            created_by=self.user,
        )
        protected = PlanApprovalRevision.objects.create(
            plan=other_plan,
            revision_number=1,
            status=PlanApprovalRevision.Status.SUPERSEDED,
            approved_by=self.user,
            approved_at=timezone.now(),
        )
        with self.assertRaises(ValueError):
            PlanApprovalRevision.objects.filter(
                pk__in=[preparing.pk, protected.pk]
            ).delete()
        self.assertTrue(PlanApprovalRevision.objects.filter(pk=preparing.pk).exists())
        self.assertTrue(PlanApprovalRevision.objects.filter(pk=protected.pk).exists())

    def test_snapshot_does_not_follow_later_path_edits(self):
        path = self.create_path()
        reservation = self.create_reservation(path)
        path.planned_share_quantity = 9000
        path.planned_end_date = jdatetime.date(1405, 2, 8)
        path.machine = self.machine_b
        path.save()
        reservation.refresh_from_db()
        self.assertEqual(reservation.approved_planned_share_quantity, 20000)
        self.assertEqual(reservation.machine, self.machine_a)
        self.assertEqual(reservation.reserved_end_date, jdatetime.date(1405, 2, 3))

    def test_snapshot_fields_are_application_immutable_but_lifecycle_can_change(self):
        reservation = self.create_reservation()
        reservation.approved_planned_share_quantity = 1
        with self.assertRaises(ValueError):
            reservation.save()
        with self.assertRaises(ValueError):
            PhysicalMoldReservation.objects.filter(pk=reservation.pk).update(
                machine=self.machine_b
            )
        PhysicalMoldReservation.objects.filter(pk=reservation.pk).update(
            status=PhysicalMoldReservation.Status.RELEASED,
            released_at=timezone.now(),
        )
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, PhysicalMoldReservation.Status.RELEASED)

    def test_reservation_snapshot_rejects_instance_and_queryset_deletion(self):
        reservation = self.create_reservation()
        with self.assertRaises(ValueError):
            reservation.delete()
        with self.assertRaises(ValueError):
            PhysicalMoldReservation.objects.filter(pk=reservation.pk).delete()
        self.assertTrue(
            PhysicalMoldReservation.objects.filter(pk=reservation.pk).exists()
        )

    def test_no_implicit_revision_or_reservation_creation(self):
        self.create_path()
        self.assertFalse(PlanApprovalRevision.objects.exists())
        self.assertFalse(PhysicalMoldReservation.objects.exists())

    def test_active_overlap_is_inclusive_and_scoped_to_same_mold(self):
        reservation = self.create_reservation()
        self.assertTrue(
            active_reservation_overlaps(
                physical_mold=self.mold_a,
                start_date=jdatetime.date(1405, 2, 3),
                end_date=jdatetime.date(1405, 2, 5),
            ).filter(pk=reservation.pk).exists()
        )
        self.assertFalse(
            active_reservation_overlaps(
                physical_mold=self.mold_a,
                start_date=jdatetime.date(1405, 2, 4),
                end_date=jdatetime.date(1405, 2, 5),
            ).exists()
        )
        self.assertFalse(
            active_reservation_overlaps(
                physical_mold=self.mold_b,
                start_date=jdatetime.date(1405, 2, 1),
                end_date=jdatetime.date(1405, 2, 5),
            ).exists()
        )

    def test_non_active_reservations_do_not_block(self):
        self.create_reservation(status=PhysicalMoldReservation.Status.SUPERSEDED)
        self.assertFalse(
            active_reservation_overlaps(
                physical_mold=self.mold_a,
                start_date=jdatetime.date(1405, 2, 1),
                end_date=jdatetime.date(1405, 2, 3),
            ).exists()
        )

    def test_approved_history_protects_requirement_and_source_path(self):
        path = self.create_path()
        self.create_reservation(path)
        with self.assertRaises(ProtectedError):
            path.delete()
        with self.assertRaises(ProtectedError):
            self.requirement.delete()


class ClassificationTests(PathFoundationTestCase):
    def test_all_compatibility_states_are_classified_without_mutation(self):
        expected = {
            MoldMachineCompatibility.Status.CONFIRMED: (True, False, True),
            MoldMachineCompatibility.Status.INCOMPATIBLE: (False, False, False),
            MoldMachineCompatibility.Status.NEEDS_REVIEW: (False, True, False),
            MoldMachineCompatibility.Status.UNKNOWN: (False, True, False),
        }
        molds = [self.mold_a, self.mold_b]
        for index, (status, flags) in enumerate(expected.items()):
            mold = molds[index] if index < 2 else PhysicalMold.objects.create(
                code=f"SLICE-2A-CLASS-{index}", mold_type=self.mold_type
            )
            compatibility = MoldMachineCompatibility.objects.create_with_status(
                physical_mold=mold,
                machine=self.machine_a,
                status=status,
                changed_by=self.user,
                reason="classification test",
            )
            history_count = compatibility.history.count()
            result = classify_compatibility(
                physical_mold=mold, machine=self.machine_a
            )
            self.assertEqual(
                (result.operationally_allowed, result.requires_manager_decision,
                 result.smart_planner_candidate),
                flags,
            )
            compatibility.refresh_from_db()
            self.assertEqual(compatibility.status, status)
            self.assertEqual(compatibility.history.count(), history_count)

    def test_mold_status_classification_and_proposed_validation_have_no_side_effects(self):
        under_repair = PhysicalMold.objects.create(
            code="SLICE-2A-REPAIR",
            mold_type=self.mold_type,
            status=PhysicalMold.Status.UNDER_REPAIR,
            estimated_ready_date=jdatetime.date(1405, 2, 2),
        )
        same_day = classify_mold_status(
            physical_mold=under_repair,
            planned_start_date=jdatetime.date(1405, 2, 2),
        )
        self.assertFalse(same_day.future_candidate)
        future = classify_mold_status(
            physical_mold=under_repair,
            planned_start_date=jdatetime.date(1405, 2, 3),
        )
        self.assertTrue(future.future_candidate)
        no_date = PhysicalMold.objects.create(
            code="SLICE-2A-REPAIR-NODATE",
            mold_type=self.mold_type,
            status=PhysicalMold.Status.UNDER_REPAIR,
        )
        self.assertFalse(classify_mold_status(physical_mold=no_date).future_candidate)

        path = PlannedProductionPath(
            requirement=self.requirement,
            machine=self.machine_a,
            physical_mold=under_repair,
            planned_share_quantity=100,
            planned_start_date=jdatetime.date(1405, 2, 3),
            planned_end_date=jdatetime.date(1405, 2, 4),
        )
        result = validate_proposed_path(path)
        self.assertTrue(result["requires_manager_decision"])
        self.assertEqual(PlannedProductionPath.objects.count(), 0)
        self.assertEqual(PlanApprovalRevision.objects.count(), 0)
        self.assertEqual(PhysicalMoldReservation.objects.count(), 0)


class LegacyIsolationTests(PathFoundationTestCase):
    def test_legacy_fields_and_models_remain_intact(self):
        self.assertEqual(self.mold_type.copies, 4)
        self.assertEqual(str(self.mold_type.change_time_hours), "2.00")
        self.assertEqual(self.item.machine, self.machine_a)
        self.assertEqual(self.item.mold, self.mold_type)
        self.assertEqual(self.requirement.mold, self.mold_type)
        self.assertEqual(PhysicalMold.objects.count(), 2)
        self.assertFalse(ProductionProgram.objects.exists())

    def test_legacy_approval_endpoint_does_not_create_foundation_records(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("plan_set_status", args=[self.plan.pk]),
            {"status": WeeklyPlan.Status.APPROVED},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProductionProgram.objects.filter(item=self.item).exists())
        self.assertFalse(PlanApprovalRevision.objects.exists())
        self.assertFalse(PhysicalMoldReservation.objects.exists())
