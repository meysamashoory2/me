import jdatetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import Role, UserProfile
from catalog.models import Machine, MoldMachineCompatibility, MoldOption, PhysicalMold, Product, ProductGroup, ProductSubGroup, ProductionUnit
from production.models import ProductionDeviation, ProductionTargetRevision
from production.path_execution import (
    edit_unstarted_execution_path,
    get_or_create_unstarted_execution_path,
    register_path_production,
)
from production.target_semantics import RequiredGoodUnavailable, snapshot_for_execution_path

from .final_merge import create_manual_final
from .models import PhysicalMoldReservation, PlannedProductionPath, PlanningDeviation, PlanningRevision, WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine
from .operational_approval import (
    StalePathApproval,
    _create_approval_evidence,
    approve_path,
    edit_path,
    preview_path_approval,
)
from .preproduction_revision import ImpactLevel, RevisionImpactConflict, apply_preproduction_revision, preview_preproduction_revision
from .program_governance import approve_manual_source, create_program_source, initialize_program_numbering
from .reservation_integrity import PhysicalMoldReservationConflict


class PreProductionRevisionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = get_user_model().objects.create_user(username="b3b-manager")
        profile, _ = UserProfile.objects.get_or_create(user=cls.manager)
        profile.role = Role.PLANNING_MANAGER
        profile.save(update_fields=["role"])
        cls.expert = get_user_model().objects.create_user(username="b3b-expert")
        profile, _ = UserProfile.objects.get_or_create(user=cls.expert)
        profile.role = Role.PLANNING_EXPERT
        profile.save(update_fields=["role"])
        cls.group = ProductGroup.objects.create(name="B3B group")
        cls.subgroup = ProductSubGroup.objects.create(name="B3B subgroup", group=cls.group)
        cls.unit = ProductionUnit.objects.create(number=984, name="B3B unit")
        cls.machine_a = Machine.objects.create(number="B3B-A", unit=cls.unit)
        cls.machine_b = Machine.objects.create(number="B3B-B", unit=cls.unit)
        cls.product = Product.objects.create(code="B3B-P", name="B3B product", subgroup=cls.subgroup)
        cls.mold_type = MoldOption.objects.create(code="B3B-T", label="B3B mold type")
        cls.mold_a = PhysicalMold.objects.create(code="B3B-M-A", mold_type=cls.mold_type)
        cls.mold_b = PhysicalMold.objects.create(code="B3B-M-B", mold_type=cls.mold_type)
        cls.mold_unknown = PhysicalMold.objects.create(code="B3B-M-U", mold_type=cls.mold_type)
        cls.mold_review = PhysicalMold.objects.create(code="B3B-M-R", mold_type=cls.mold_type)
        cls.mold_bad = PhysicalMold.objects.create(code="B3B-M-X", mold_type=cls.mold_type)
        for mold in (cls.mold_a, cls.mold_b):
            for machine in (cls.machine_a, cls.machine_b):
                MoldMachineCompatibility.objects.create_with_status(physical_mold=mold, machine=machine, status="confirmed", changed_by=cls.manager, reason="B3B fixture")
        MoldMachineCompatibility.objects.create_with_status(physical_mold=cls.mold_review, machine=cls.machine_a, status="needs_review", changed_by=cls.manager, reason="B3B fixture")
        MoldMachineCompatibility.objects.create_with_status(physical_mold=cls.mold_bad, machine=cls.machine_a, status="incompatible", changed_by=cls.manager, reason="B3B fixture")

    def setUp(self):
        initialize_program_numbering(starting_program_number=1300, actor=self.manager)
        self.next_number = 1300
        self.path, self.final_revision = self.make_final_path(jdatetime.date(1405, 12, 15))
        preview = preview_path_approval(self.path)
        self.approved = approve_path(self.path.pk, submitted_fingerprint=preview.fingerprint, actor=self.manager)

    def make_final_path(self, date, *, mold=None, machine=None, path_start=None):
        number = self.next_number
        self.next_number += 1
        manual = create_program_source(origin=WeeklyPlan.PlanningMode.MANUAL, program_date=date, actor=self.manager, requested_program_number=number)
        machine = machine or self.machine_a
        mold = mold or self.mold_a
        item = WeeklyPlanItem.objects.create(plan=manual, subgroup=self.subgroup, unit=self.unit, machine=machine, product=self.product, mold=self.mold_type, mold_change_weekday=0, mold_change_date=date)
        line = WeeklyPlanLine.objects.create(item=item, mold=self.mold_type, quantity=200, cycle=30)
        path_start = path_start or date
        PlannedProductionPath.objects.create(requirement=line, machine=machine, physical_mold=mold, planned_share_quantity=200, planned_start_date=path_start, planned_end_date=path_start + jdatetime.timedelta(days=2), created_by=self.manager, updated_by=self.manager)
        approve_manual_source(manual.pk, actor=self.manager)
        revision, _ = create_manual_final(cycle_id=manual.governance_cycle_id, actor=self.manager)
        return revision.final_plan.items.get().lines.get().planned_paths.get(), revision

    def preview(self, **overrides):
        reservation = self.path.operational_approvals.order_by("-sequence").first().reservation
        execution = self.approved.execution_path
        values = dict(machine_id=execution.machine_id, physical_mold_id=execution.physical_mold_id, target=execution.current_good_target, start_date=reservation.reserved_start_date, end_date=reservation.reserved_end_date)
        values.update(overrides)
        return preview_preproduction_revision(self.path, **values), values

    def apply(self, **overrides):
        preview, values = self.preview(**overrides)
        return apply_preproduction_revision(self.path.pk, submitted_fingerprint=preview.fingerprint, actor=self.manager, **values)

    def test_target_revision_is_revision_only_and_preserves_final(self):
        result = self.apply(target=250)
        self.approved.execution_path.refresh_from_db()
        self.path.refresh_from_db()
        self.assertEqual(self.approved.execution_path.current_good_target, 250)
        self.assertEqual(self.approved.execution_path.required_good_quantity, 200)
        self.assertEqual(snapshot_for_execution_path(self.approved.execution_path).required_good, 200)
        self.assertEqual(self.path.planned_share_quantity, 200)
        self.assertEqual(result.revision.before_snapshot["planned_share_quantity"], 200)
        self.assertEqual(result.revision.after_snapshot["planned_share_quantity"], 250)
        self.assertEqual(result.revision.actor, self.manager)
        self.assertFalse(PlanningDeviation.objects.exists())
        self.assertFalse(ProductionDeviation.objects.exists())
        self.assertFalse(ProductionTargetRevision.objects.exists())

    def test_duplicate_approval_evidence_for_one_final_path_is_not_a_conflict(self):
        self.apply(target=250)
        self.assertEqual(self.path.operational_approvals.count(), 2)
        self.assertEqual(self.approved.execution_path.required_good_quantity, 200)

    def test_final_path_without_operational_approval_has_no_required_good_fallback(self):
        unapproved, _ = self.make_final_path(jdatetime.date(1406, 2, 1), mold=self.mold_b)
        execution, _ = get_or_create_unstarted_execution_path(unapproved, actor=self.manager)
        with self.assertRaisesRegex(RequiredGoodUnavailable, "no immutable path-approval"):
            _ = execution.required_good_quantity

    def _make_governed_source_execution_without_final(self, *, origin, date):
        number = self.next_number
        self.next_number += 1
        source = create_program_source(
            origin=origin,
            program_date=date,
            actor=self.manager,
            requested_program_number=number,
        )
        item = WeeklyPlanItem.objects.create(
            plan=source, subgroup=self.subgroup, unit=self.unit, machine=self.machine_b,
            product=self.product, mold=self.mold_type, mold_change_weekday=0,
            mold_change_date=date,
        )
        requirement = WeeklyPlanLine.objects.create(
            item=item, mold=self.mold_type, quantity=200, cycle=30
        )
        planned = PlannedProductionPath.objects.create(
            requirement=requirement, machine=self.machine_b, physical_mold=self.mold_b,
            planned_share_quantity=200, planned_start_date=date,
            planned_end_date=date + jdatetime.timedelta(days=2),
            created_by=self.manager, updated_by=self.manager,
        )
        execution, _ = get_or_create_unstarted_execution_path(planned, actor=self.manager)
        return source, execution

    def test_governed_manual_source_without_final_fails_closed_without_side_effects(self):
        source, execution = self._make_governed_source_execution_without_final(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            date=jdatetime.date(1406, 2, 8),
        )
        original_target = execution.current_good_target
        with self.assertRaisesRegex(RequiredGoodUnavailable, "Manual/System source"):
            snapshot_for_execution_path(execution)
        execution.refresh_from_db()
        self.assertIsNotNone(source.governance_cycle_id)
        self.assertEqual(execution.current_good_target, original_target)
        self.assertFalse(execution.operational_approvals.exists())
        self.assertFalse(ProductionTargetRevision.objects.filter(execution_path=execution).exists())
        self.assertFalse(ProductionDeviation.objects.filter(execution_path=execution).exists())

    def test_governed_system_source_without_final_fails_closed_without_side_effects(self):
        source, execution = self._make_governed_source_execution_without_final(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            date=jdatetime.date(1406, 2, 15),
        )
        original_target = execution.current_good_target
        with self.assertRaisesRegex(RequiredGoodUnavailable, "Manual/System source"):
            snapshot_for_execution_path(execution)
        execution.refresh_from_db()
        self.assertIsNotNone(source.governance_cycle_id)
        self.assertEqual(execution.current_good_target, original_target)
        self.assertFalse(execution.operational_approvals.exists())
        self.assertFalse(ProductionTargetRevision.objects.filter(execution_path=execution).exists())
        self.assertFalse(ProductionDeviation.objects.filter(execution_path=execution).exists())

    def test_conflicting_final_allocations_for_one_execution_fail_closed(self):
        manual = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=jdatetime.date(1406, 3, 1),
            actor=self.manager,
            requested_program_number=self.next_number,
        )
        self.next_number += 1
        item = WeeklyPlanItem.objects.create(
            plan=manual, subgroup=self.subgroup, unit=self.unit, machine=self.machine_b,
            product=self.product, mold=self.mold_type, mold_change_weekday=0,
            mold_change_date=manual.date,
        )
        line = WeeklyPlanLine.objects.create(
            item=item, mold=self.mold_type, quantity=500, cycle=30
        )
        for sequence, share in ((1, 200), (2, 300)):
            PlannedProductionPath.objects.create(
                requirement=line, machine=self.machine_b, physical_mold=self.mold_b,
                planned_share_quantity=share,
                planned_start_date=manual.date + jdatetime.timedelta(days=sequence * 3),
                planned_end_date=manual.date + jdatetime.timedelta(days=sequence * 3 + 1),
                sequence=sequence, created_by=self.manager, updated_by=self.manager,
            )
        approve_manual_source(manual.pk, actor=self.manager)
        revision, _ = create_manual_final(
            cycle_id=manual.governance_cycle_id, actor=self.manager
        )
        paths = list(
            revision.final_plan.items.get().lines.get().planned_paths.order_by("sequence")
        )
        approved = approve_path(
            paths[0].pk,
            submitted_fingerprint=preview_path_approval(paths[0]).fingerprint,
            actor=self.manager,
        )
        _create_approval_evidence(
            using="default",
            planned_path=paths[1],
            execution_path=approved.execution_path,
            reservation=approved.reservation,
            sequence=1,
            action="approved",
            path_fingerprint="conflicting-lineage-fixture",
            after_snapshot={"planned_share_quantity": 300},
            actor=self.manager,
        )
        with self.assertRaisesRegex(RequiredGoodUnavailable, "conflicting Good allocations"):
            _ = approved.execution_path.required_good_quantity

    def test_noop_does_not_create_revision_or_replace_reservation(self):
        preview, values = self.preview()
        with self.assertRaises(ValidationError):
            apply_preproduction_revision(self.path.pk, submitted_fingerprint=preview.fingerprint, actor=self.manager, **values)
        self.assertFalse(PlanningRevision.objects.exists())
        self.assertEqual(PhysicalMoldReservation.objects.count(), 1)

    def test_machine_mold_and_date_changes_replace_reservation(self):
        old = self.approved.reservation
        result = self.apply(machine_id=self.machine_b.pk, physical_mold_id=self.mold_b.pk, start_date=jdatetime.date(1405, 12, 20), end_date=jdatetime.date(1405, 12, 22))
        old.refresh_from_db()
        result.operation.execution_path.refresh_from_db()
        self.assertEqual(old.status, PhysicalMoldReservation.Status.SUPERSEDED)
        self.assertEqual(result.operation.reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(result.operation.execution_path.machine_id, self.machine_b.pk)
        self.assertEqual(result.operation.execution_path.physical_mold_id, self.mold_b.pk)
        self.assertEqual(result.operation.reservation.reserved_start_date, jdatetime.date(1405, 12, 20))
        self.path.refresh_from_db()
        self.assertEqual(self.path.machine_id, self.machine_a.pk)
        self.assertEqual(self.path.physical_mold_id, self.mold_a.pk)

    def test_compatibility_rules_and_manager_exception_evidence(self):
        with self.assertRaises(ValidationError):
            self.preview(physical_mold_id=self.mold_bad.pk)
        with self.assertRaises(ValidationError):
            self.preview(physical_mold_id=self.mold_unknown.pk)
        preview, values = self.preview(physical_mold_id=self.mold_unknown.pk, confirmations=["unknown"])
        result = apply_preproduction_revision(self.path.pk, submitted_fingerprint=preview.fingerprint, actor=self.manager, **values)
        self.assertIn("unknown", result.operation.approval.manager_confirmations)
        self.assertIn("unknown", result.revision.context["manager_confirmations"])
        self.assertFalse(MoldMachineCompatibility.objects.filter(physical_mold=self.mold_unknown, machine=self.machine_a).exists())

    def test_needs_review_requires_frozen_confirmation(self):
        with self.assertRaises(ValidationError):
            self.preview(physical_mold_id=self.mold_review.pk)
        result = self.apply(physical_mold_id=self.mold_review.pk, confirmations=["needs_review"])
        self.assertIn("needs_review", result.operation.approval.manager_confirmations)

    def test_conflict_and_potential_impact_create_no_deviation(self):
        other, _ = self.make_final_path(jdatetime.date(1405, 12, 14), mold=self.mold_b, path_start=jdatetime.date(1405, 12, 15))
        preview, _ = self.preview(physical_mold_id=self.mold_b.pk)
        self.assertEqual(preview.impact.level, ImpactLevel.POTENTIAL_IMPACT)
        other_preview = preview_path_approval(other)
        other_approved = approve_path(other.pk, submitted_fingerprint=other_preview.fingerprint, actor=self.manager)
        preview, values = self.preview(physical_mold_id=self.mold_b.pk)
        self.assertEqual(preview.impact.level, ImpactLevel.ACTUAL_CONFLICT)
        with self.assertRaises(RevisionImpactConflict) as caught:
            apply_preproduction_revision(self.path.pk, submitted_fingerprint=preview.fingerprint, actor=self.manager, **values)
        self.assertEqual(caught.exception.impact.affected[0].reservation_id, other_approved.reservation.pk)
        self.assertFalse(PlanningDeviation.objects.exists())
        self.assertFalse(PlanningRevision.objects.exists())

    def test_active_execution_ownership_wins_after_reservation_dates(self):
        other, _ = self.make_final_path(jdatetime.date(1405, 12, 25), mold=self.mold_b)
        other_approved = approve_path(other.pk, submitted_fingerprint=preview_path_approval(other).fingerprint, actor=self.manager)
        register_path_production(other_approved.execution_path, date=jdatetime.date(1405, 12, 25), shots=1, active_cavities=1, scrap_pieces=0, actor=self.manager)
        preview, _ = self.preview(physical_mold_id=self.mold_b.pk, start_date=jdatetime.date(1406, 1, 10), end_date=jdatetime.date(1406, 1, 11))
        self.assertEqual(preview.impact.level, ImpactLevel.ACTUAL_CONFLICT)
        self.assertEqual(preview.impact.affected[0].execution_path_id, other_approved.execution_path.pk)

    def test_first_production_stale_and_permission_boundaries(self):
        preview, values = self.preview(target=210)
        with self.assertRaises(PermissionDenied):
            apply_preproduction_revision(self.path.pk, submitted_fingerprint=preview.fingerprint, actor=self.expert, **values)
        changed_proposal = dict(values, target=211)
        with self.assertRaises(StalePathApproval):
            apply_preproduction_revision(self.path.pk, submitted_fingerprint=preview.fingerprint, actor=self.manager, **changed_proposal)
        self.apply(target=220)
        with self.assertRaises(StalePathApproval):
            apply_preproduction_revision(self.path.pk, submitted_fingerprint=preview.fingerprint, actor=self.manager, **values)
        register_path_production(self.approved.execution_path, date=jdatetime.date(1405, 12, 15), shots=1, active_cavities=1, scrap_pieces=0, actor=self.manager)
        with self.assertRaises(ValidationError):
            self.preview(target=230)

    def test_first_production_after_preview_blocks_apply_even_with_stale_status(self):
        preview, values = self.preview(target=230)
        register_path_production(
            self.approved.execution_path,
            date=jdatetime.date(1405, 12, 15),
            shots=1,
            active_cavities=1,
            scrap_pieces=0,
            actor=self.manager,
        )
        type(self.approved.execution_path).objects.filter(
            pk=self.approved.execution_path.pk
        )._update_for_execution_service(status="awaiting")
        with self.assertRaises(ValidationError):
            apply_preproduction_revision(
                self.path.pk,
                submitted_fingerprint=preview.fingerprint,
                actor=self.manager,
                **values,
            )

    def test_preview_binds_operational_identity_and_impact(self):
        original_preview, original_values = self.preview(target=210)
        self.apply(target=220)
        self.apply(target=200)
        with self.assertRaises(StalePathApproval):
            apply_preproduction_revision(
                self.path.pk,
                submitted_fingerprint=original_preview.fingerprint,
                actor=self.manager,
                **original_values,
            )

        other, _ = self.make_final_path(
            jdatetime.date(1405, 12, 14),
            mold=self.mold_b,
            path_start=jdatetime.date(1405, 12, 15),
        )
        impact_preview, impact_values = self.preview(physical_mold_id=self.mold_b.pk)
        self.assertEqual(impact_preview.impact.level, ImpactLevel.POTENTIAL_IMPACT)
        approve_path(
            other.pk,
            submitted_fingerprint=preview_path_approval(other).fingerprint,
            actor=self.manager,
        )
        with self.assertRaises(StalePathApproval):
            apply_preproduction_revision(
                self.path.pk,
                submitted_fingerprint=impact_preview.fingerprint,
                actor=self.manager,
                **impact_values,
            )

    def test_governed_execution_cannot_bypass_revision_service(self):
        execution = self.approved.execution_path
        with self.assertRaises(ValidationError):
            edit_unstarted_execution_path(
                execution,
                machine=self.machine_b,
                physical_mold=self.mold_b,
                current_good_target=250,
                actor=self.manager,
                before_snapshot={},
            )
        execution.refresh_from_db()
        self.assertEqual(execution.machine_id, self.machine_a.pk)
        self.assertEqual(execution.physical_mold_id, self.mold_a.pk)
        self.assertEqual(execution.current_good_target, 200)
        self.assertFalse(PlanningRevision.objects.exists())

    def test_reservation_boundaries_use_inclusive_overlap_semantics(self):
        other, _ = self.make_final_path(
            jdatetime.date(1405, 12, 20),
            mold=self.mold_b,
            path_start=jdatetime.date(1405, 12, 20),
        )
        approved = approve_path(
            other.pk,
            submitted_fingerprint=preview_path_approval(other).fingerprint,
            actor=self.manager,
        )
        for start, end in (
            (jdatetime.date(1405, 12, 20), jdatetime.date(1405, 12, 20)),
            (jdatetime.date(1405, 12, 22), jdatetime.date(1405, 12, 22)),
        ):
            preview, _ = self.preview(
                physical_mold_id=self.mold_b.pk,
                start_date=start,
                end_date=end,
            )
            self.assertEqual(preview.impact.level, ImpactLevel.ACTUAL_CONFLICT)
        non_overlapping, _ = self.preview(
            physical_mold_id=self.mold_b.pk,
            start_date=approved.reservation.reserved_end_date + jdatetime.timedelta(days=1),
            end_date=approved.reservation.reserved_end_date + jdatetime.timedelta(days=1),
        )
        self.assertNotEqual(non_overlapping.impact.level, ImpactLevel.ACTUAL_CONFLICT)

    def test_atomic_rollback_on_reservation_or_revision_failure(self):
        old = self.approved.reservation
        with patch("planning.preproduction_revision.activate_reservation", side_effect=RuntimeError("activation failed")):
            with self.assertRaises(RuntimeError):
                self.apply(target=210)
        old.refresh_from_db()
        self.approved.execution_path.refresh_from_db()
        self.assertEqual(old.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(self.approved.execution_path.current_good_target, 200)
        self.assertEqual(PhysicalMoldReservation.objects.count(), 1)
        with patch("planning.preproduction_revision.PlanningRevision.save", side_effect=RuntimeError("revision failed")):
            with self.assertRaises(RuntimeError):
                self.apply(target=215)
        old.refresh_from_db()
        self.approved.execution_path.refresh_from_db()
        self.assertEqual(old.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(self.approved.execution_path.current_good_target, 200)
        self.assertEqual(PlanningRevision.objects.count(), 0)
        self.assertEqual(self.path.operational_approvals.count(), 1)
        with patch(
            "planning.preproduction_revision.PlanningDeviation.objects.using",
            side_effect=RuntimeError("post-revision failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.apply(target=218)
        old.refresh_from_db()
        self.approved.execution_path.refresh_from_db()
        self.assertEqual(old.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(self.approved.execution_path.current_good_target, 200)
        self.assertEqual(PlanningRevision.objects.count(), 0)
        self.assertEqual(self.path.operational_approvals.count(), 1)

    def test_governed_edit_routes_canonical_while_legacy_remains_compatible(self):
        preview, values = self.preview(target=230)
        result = edit_path(self.path.pk, submitted_fingerprint=preview.fingerprint, actor=self.manager, **values)
        self.assertEqual(result.approval.action, "edited")
        self.assertEqual(PlanningRevision.objects.get().after_snapshot["planned_share_quantity"], 230)
        self.path.refresh_from_db()
        self.assertEqual(self.path.planned_share_quantity, 200)
