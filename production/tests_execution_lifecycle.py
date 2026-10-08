import jdatetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, UserProfile
from catalog.models import DeviationReason, MoldMachineCompatibility, PhysicalMold
from planning.models import (
    FinalProgramRevision,
    PathOperationalApproval,
    PhysicalMoldReservation,
    ProgramSourceApproval,
    WeeklyPlan,
    WeeklyProgramCycle,
)
from planning.operational_approval import approve_path, preview_path_approval
from planning.tests_path_foundation import PathFoundationTestCase

from .execution_lifecycle import (
    complete_execution_statistics,
    full_stop_execution_path,
    resume_execution_path,
    temporary_stop_execution_path,
)
from .models import (
    ProductionDeviation,
    ProductionExecutionEvent,
    ProductionExecutionPath,
    RevisionEvidenceQuerySet,
)
from .path_execution import register_path_production


class ExecutionLifecycleTests(PathFoundationTestCase):
    confirmations = [MoldMachineCompatibility.Status.UNKNOWN]

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.operator = get_user_model().objects.create_user(
            username="closeout-operator", password="test-only"
        )
        profile, _ = UserProfile.objects.get_or_create(user=cls.operator)
        profile.role = Role.PLANNING_EXPERT
        profile.save(update_fields=["role"])
        cls.viewer = get_user_model().objects.create_user(username="closeout-viewer")
        profile, _ = UserProfile.objects.get_or_create(user=cls.viewer)
        profile.role = Role.VIEWER
        profile.save(update_fields=["role"])
        cls.reason = DeviationReason.objects.create(
            label="Lifecycle machine breakdown",
            code=DeviationReason.Code.MACHINE_BREAKDOWN,
            applicability=DeviationReason.Applicability.PRODUCTION,
        )
        cls.other_reason = DeviationReason.objects.create(
            label="Lifecycle quality problem",
            code=DeviationReason.Code.QUALITY_PROBLEM,
            applicability=DeviationReason.Applicability.PRODUCTION,
        )
        cls.note_reason = DeviationReason.objects.create(
            label="Lifecycle other",
            code=DeviationReason.Code.OTHER,
            applicability=DeviationReason.Applicability.PRODUCTION,
            requires_note=True,
        )
        cycle = WeeklyProgramCycle.objects.create(
            program_number=99001,
            program_date=cls.plan.date,
            created_by=cls.user,
        )
        manual = WeeklyPlan.objects.create(
            program_number="CLOSEOUT-MANUAL",
            date=cls.plan.date,
            planning_mode=WeeklyPlan.PlanningMode.MANUAL,
            governance_cycle=cycle,
            created_by=cls.user,
        )
        cls.plan.governance_cycle = cycle
        cls.plan.planning_mode = WeeklyPlan.PlanningMode.FINAL
        cls.plan.save(update_fields=["governance_cycle", "planning_mode"])
        cycle.final_plan = cls.plan
        cycle.save(update_fields=["final_plan"])
        source_approval = ProgramSourceApproval(
            source_plan=manual,
            approval_type=ProgramSourceApproval.ApprovalType.MANUAL,
            content_fingerprint="c" * 64,
            content_snapshot={},
            approved_by=cls.user,
        )
        source_approval._governance_service_create = True
        try:
            source_approval.save(force_insert=True)
        finally:
            del source_approval._governance_service_create
        revision = FinalProgramRevision(
            cycle=cycle,
            revision_number=1,
            final_plan=cls.plan,
            manual_source=manual,
            status=FinalProgramRevision.Status.EFFECTIVE,
            source_review_fingerprint="a" * 64,
            result_fingerprint="b" * 64,
            approved_by=cls.user,
        )
        revision._merge_service_create = True
        try:
            revision.save(force_insert=True)
        finally:
            del revision._merge_service_create

    def approve(self, planned_path):
        preview = preview_path_approval(
            planned_path, user=self.user, require_permission=True
        )
        return approve_path(
            planned_path.pk,
            submitted_fingerprint=preview.fingerprint,
            actor=self.user,
            confirmations=self.confirmations,
        )

    def running_path(self, *, gross=100, scrap=10, target=20000, mold=None):
        planned = self.create_path(
            physical_mold=mold or self.mold_a,
            planned_share_quantity=target,
            sequence=self.requirement.planned_paths.count() + 1,
        )
        approved = self.approve(planned)
        register_path_production(
            approved.execution_path,
            date=jdatetime.date(1405, 2, 1),
            shots=gross,
            active_cavities=1,
            scrap_pieces=scrap,
            actor=self.operator,
        )
        approved.execution_path.refresh_from_db()
        return approved

    def stop_kwargs(self):
        return {"shortfall_reason": self.reason, "shortfall_note": "closeout"}

    def test_temporary_stop_resume_and_later_production(self):
        approved = self.running_path()
        event = temporary_stop_execution_path(
            approved.execution_path, actor=self.operator, reason=self.reason
        )
        approved.execution_path.refresh_from_db()
        approved.reservation.refresh_from_db()
        self.assertEqual(approved.execution_path.status, ProductionExecutionPath.Status.TEMPORARY_STOP)
        self.assertEqual(event.metadata["reason_code"], self.reason.code)
        self.assertEqual(approved.reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        with self.assertRaises(ValidationError):
            register_path_production(
                approved.execution_path,
                date=jdatetime.date(1405, 2, 2),
                shots=1,
                active_cavities=1,
                scrap_pieces=0,
                actor=self.operator,
            )
        resume_execution_path(approved.execution_path, actor=self.operator)
        register_path_production(
            approved.execution_path,
            date=jdatetime.date(1405, 2, 2),
            shots=1,
            active_cavities=1,
            scrap_pieces=0,
            actor=self.operator,
        )
        approved.execution_path.refresh_from_db()
        self.assertEqual(approved.execution_path.status, ProductionExecutionPath.Status.RUNNING)

    def test_transition_permissions_and_invalid_states(self):
        approved = self.running_path()
        with self.assertRaises(PermissionDenied):
            temporary_stop_execution_path(
                approved.execution_path, actor=self.viewer, reason=self.reason
            )
        with self.assertRaises(PermissionDenied):
            full_stop_execution_path(
                approved.execution_path, actor=self.operator, **self.stop_kwargs()
            )
        with self.assertRaises(ValidationError):
            resume_execution_path(approved.execution_path, actor=self.operator)
        temporary_stop_execution_path(
            approved.execution_path, actor=self.operator, reason=self.reason
        )
        with self.assertRaises(ValidationError):
            temporary_stop_execution_path(
                approved.execution_path, actor=self.operator, reason=self.reason
            )

    def test_structured_reason_and_required_note(self):
        approved = self.running_path()
        self.reason.is_active = False
        self.reason.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            temporary_stop_execution_path(
                approved.execution_path, actor=self.operator, reason=self.reason
            )
        with self.assertRaises(ValidationError):
            temporary_stop_execution_path(
                approved.execution_path, actor=self.operator, reason=self.note_reason
            )
        event = temporary_stop_execution_path(
            approved.execution_path,
            actor=self.operator,
            reason=self.note_reason,
            note="documented",
        )
        self.assertEqual(event.metadata["note"], "documented")

    def test_full_stop_reconciles_without_summing_and_releases_mold(self):
        approved = self.running_path(gross=12000, scrap=2000, target=20000)
        result = full_stop_execution_path(
            approved.execution_path, actor=self.user, **self.stop_kwargs()
        )
        self.assertEqual(result.snapshot.cumulative_gross, 12000)
        self.assertEqual(result.snapshot.cumulative_good, 10000)
        self.assertEqual(result.snapshot.cumulative_scrap, 2000)
        self.assertEqual(result.snapshot.counter_remaining, 8000)
        self.assertEqual(result.snapshot.good_shortage, 10000)
        self.assertEqual(result.path.current_good_target, 20000)
        self.assertEqual(result.path.status, ProductionExecutionPath.Status.FULL_STOP)
        self.assertEqual(result.reservation.status, PhysicalMoldReservation.Status.RELEASED)
        self.assertIsNotNone(result.reservation.released_at)
        self.assertEqual(len(result.deviations), 1)
        deviation = result.deviations[0]
        self.assertIsNone(deviation.explained_quantity)
        self.assertEqual(deviation.context["closeout"]["counter_remaining"], 8000)
        self.assertEqual(deviation.context["closeout"]["good_shortage"], 10000)
        self.assertEqual(result.event.metadata["reservation_id"], approved.reservation.pk)

    def test_overproduction_dimensions_are_preserved_separately(self):
        approved = self.running_path(gross=120, scrap=0, target=100)
        result = full_stop_execution_path(
            approved.execution_path,
            actor=self.user,
            overproduction_reason=self.other_reason,
        )
        self.assertEqual(result.snapshot.counter_overproduction, 20)
        self.assertEqual(result.snapshot.good_overproduction, 20)
        self.assertEqual(result.deviations[0].category, ProductionDeviation.Category.OVERPRODUCTION)
        self.assertIsNone(result.deviations[0].explained_quantity)

    def test_both_difference_families_require_independent_reasons(self):
        approved = self.running_path(gross=120, scrap=80, target=100)
        with self.assertRaisesRegex(ValidationError, "shortfall"):
            full_stop_execution_path(
                approved.execution_path,
                actor=self.user,
                overproduction_reason=self.other_reason,
            )
        result = full_stop_execution_path(
            approved.execution_path,
            actor=self.user,
            shortfall_reason=self.reason,
            overproduction_reason=self.other_reason,
        )
        self.assertEqual(
            {item.category for item in result.deviations},
            {ProductionDeviation.Category.SHORTFALL, ProductionDeviation.Category.OVERPRODUCTION},
        )

    def test_pending_request_blocks_full_stop(self):
        from .target_revision_service import request_target_revision

        approved = self.running_path()
        request_target_revision(
            approved.execution_path,
            requested_target=21000,
            reason=self.reason,
            actor=self.operator,
        )
        with self.assertRaisesRegex(ValidationError, "unresolved target"):
            full_stop_execution_path(
                approved.execution_path, actor=self.user, **self.stop_kwargs()
            )
        approved.execution_path.refresh_from_db()
        approved.reservation.refresh_from_db()
        self.assertEqual(approved.execution_path.status, ProductionExecutionPath.Status.RUNNING)
        self.assertEqual(approved.reservation.status, PhysicalMoldReservation.Status.ACTIVE)

    def test_missing_active_reservation_fails_closed(self):
        approved = self.running_path()
        PhysicalMoldReservation.objects.filter(pk=approved.reservation.pk).update(
            status=PhysicalMoldReservation.Status.RELEASED,
            released_at=approved.reservation.activated_at,
        )
        with self.assertRaisesRegex(ValidationError, "exactly one"):
            full_stop_execution_path(
                approved.execution_path, actor=self.user, **self.stop_kwargs()
            )

    def test_ambiguous_active_reservation_fails_closed(self):
        approved = self.running_path()
        PhysicalMoldReservation.objects.create(
            approval_revision=None,
            source_path=approved.approval.planned_path,
            requirement=self.requirement,
            machine=self.machine_a,
            physical_mold=self.mold_a,
            execution_path=approved.execution_path,
            approved_planned_share_quantity=approved.approval.planned_path.planned_share_quantity,
            reserved_start_date=approved.approval.planned_path.planned_start_date,
            reserved_end_date=approved.approval.planned_path.planned_end_date,
            compatibility_status_snapshot=MoldMachineCompatibility.Status.UNKNOWN,
            mold_status_snapshot=PhysicalMold.Status.READY,
            status=PhysicalMoldReservation.Status.ACTIVE,
            activated_at=approved.reservation.activated_at,
        )
        with self.assertRaisesRegex(ValidationError, "exactly one"):
            full_stop_execution_path(
                approved.execution_path, actor=self.user, **self.stop_kwargs()
            )
        approved.execution_path.refresh_from_db()
        self.assertEqual(approved.execution_path.status, ProductionExecutionPath.Status.RUNNING)

    def test_mismatched_active_reservation_fails_closed(self):
        approved = self.running_path()
        PhysicalMoldReservation.objects.filter(pk=approved.reservation.pk).update(
            status=PhysicalMoldReservation.Status.RELEASED,
            released_at=approved.reservation.activated_at,
        )
        PhysicalMoldReservation.objects.create(
            approval_revision=None,
            source_path=approved.approval.planned_path,
            requirement=self.requirement,
            machine=self.machine_b,
            physical_mold=self.mold_a,
            execution_path=approved.execution_path,
            approved_planned_share_quantity=approved.approval.planned_path.planned_share_quantity,
            reserved_start_date=approved.approval.planned_path.planned_start_date,
            reserved_end_date=approved.approval.planned_path.planned_end_date,
            compatibility_status_snapshot=MoldMachineCompatibility.Status.UNKNOWN,
            mold_status_snapshot=PhysicalMold.Status.READY,
            status=PhysicalMoldReservation.Status.ACTIVE,
            activated_at=approved.reservation.activated_at,
        )
        with self.assertRaisesRegex(ValidationError, "does not match"):
            full_stop_execution_path(
                approved.execution_path, actor=self.user, **self.stop_kwargs()
            )

    def test_duplicate_full_stop_is_rejected_without_new_evidence(self):
        approved = self.running_path()
        full_stop_execution_path(
            approved.execution_path, actor=self.user, **self.stop_kwargs()
        )
        event_count = approved.execution_path.events.count()
        deviation_count = approved.execution_path.deviations.count()
        with self.assertRaises(ValidationError):
            full_stop_execution_path(
                approved.execution_path, actor=self.user, **self.stop_kwargs()
            )
        self.assertEqual(approved.execution_path.events.count(), event_count)
        self.assertEqual(approved.execution_path.deviations.count(), deviation_count)

    def test_failure_rolls_back_deviation_status_and_release(self):
        approved = self.running_path()
        with patch(
            "production.execution_lifecycle.transition_active_reservation",
            side_effect=RuntimeError("forced release failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced release failure"):
                full_stop_execution_path(
                    approved.execution_path, actor=self.user, **self.stop_kwargs()
                )
        approved.execution_path.refresh_from_db()
        approved.reservation.refresh_from_db()
        self.assertEqual(approved.execution_path.status, ProductionExecutionPath.Status.RUNNING)
        self.assertEqual(approved.reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertFalse(approved.execution_path.deviations.exists())
        self.assertFalse(
            approved.execution_path.events.filter(
                event_type=ProductionExecutionEvent.EventType.FULL_STOP
            ).exists()
        )

    def test_deviation_failure_rolls_back_without_releasing_mold(self):
        approved = self.running_path()
        with patch.object(
            RevisionEvidenceQuerySet,
            "create",
            side_effect=RuntimeError("forced deviation failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced deviation failure"):
                full_stop_execution_path(
                    approved.execution_path, actor=self.user, **self.stop_kwargs()
                )
        approved.execution_path.refresh_from_db()
        approved.reservation.refresh_from_db()
        self.assertEqual(approved.execution_path.status, ProductionExecutionPath.Status.RUNNING)
        self.assertEqual(approved.reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertFalse(approved.execution_path.deviations.exists())

    def test_statistics_completed_and_evidence_are_immutable(self):
        approved = self.running_path()
        stopped = full_stop_execution_path(
            approved.execution_path, actor=self.user, **self.stop_kwargs()
        )
        event = complete_execution_statistics(stopped.path, actor=self.user)
        stopped.path.refresh_from_db()
        self.assertEqual(stopped.path.status, ProductionExecutionPath.Status.STATISTICS_COMPLETED)
        self.assertEqual(event.metadata["full_stop_event_id"], stopped.event.pk)
        with self.assertRaises(ValueError):
            event.delete()
        with self.assertRaises(ValidationError):
            complete_execution_statistics(stopped.path, actor=self.user)

    def test_direct_event_creation_is_rejected(self):
        approved = self.running_path()
        with self.assertRaisesRegex(ValueError, "canonical execution service"):
            ProductionExecutionEvent.objects.create(
                execution_path=approved.execution_path,
                event_type=ProductionExecutionEvent.EventType.FULL_STOP,
            )
        direct = ProductionExecutionEvent(
            execution_path=approved.execution_path,
            event_type=ProductionExecutionEvent.EventType.FULL_STOP,
        )
        with self.assertRaisesRegex(ValueError, "canonical execution service"):
            direct.save_base(force_insert=True)

    def test_endpoints_are_post_only_csrf_protected_and_authorized(self):
        approved = self.running_path()
        url = reverse("execution_path_temporary_stop", args=[approved.execution_path.pk])
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.operator)
        self.assertEqual(csrf_client.post(url, {"reason": self.reason.pk}).status_code, 403)
        self.assertEqual(self.client.post(url, {"reason": self.reason.pk}).status_code, 200)
        full_url = reverse("execution_path_full_stop", args=[approved.execution_path.pk])
        self.assertEqual(
            self.client.post(full_url, {"shortfall_reason": self.reason.pk}).status_code,
            403,
        )
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.post(full_url, {"shortfall_reason": self.reason.pk}).status_code,
            200,
        )


class ExecutionLifecycleLegacyRegressionTests(TestCase):
    def test_legacy_production_program_models_remain_available(self):
        from .models import ProductionProgram

        self.assertTrue(hasattr(ProductionProgram, "Status"))
