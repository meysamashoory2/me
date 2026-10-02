import jdatetime

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserProfile
from catalog.models import MoldMachineCompatibility, PhysicalMold
from production.models import ProductionProgram

from .approval_service import (
    StaleApprovalCandidate,
    validate_approval_candidate,
    validate_manager_confirmations,
)
from .models import (
    PhysicalMoldReservation,
    PlanApprovalDecision,
    PlanApprovalRevision,
    PlannedProductionPath,
    WeeklyPlan,
    WeeklyPlanLine,
)
from .tests_path_foundation import PathFoundationTestCase


class ApprovalCandidateTests(PathFoundationTestCase):
    def confirm_compatibility(self, *, mold=None, machine=None):
        return MoldMachineCompatibility.objects.create_with_status(
            physical_mold=mold or self.mold_a,
            machine=machine or self.machine_a,
            status=MoldMachineCompatibility.Status.CONFIRMED,
            changed_by=self.user,
            reason="test setup",
        )

    def test_zero_path_requirement_blocks_whole_candidate(self):
        second = WeeklyPlanLine.objects.create(
            item=self.item, mold=self.mold_type, quantity=10, cycle=5
        )
        self.create_path()
        with self.assertRaises(ValidationError) as caught:
            validate_approval_candidate(self.plan)
        self.assertIn(f"requirement {second.pk} has no active planned path", caught.exception.messages)

    def test_inactive_only_path_is_treated_as_zero_paths(self):
        self.create_path(is_active=False)
        with self.assertRaises(ValidationError):
            validate_approval_candidate(self.plan)

    def test_under_exact_and_over_allocation_are_all_valid(self):
        self.confirm_compatibility()
        path = self.create_path(planned_share_quantity=35000)
        candidate = validate_approval_candidate(self.plan)
        self.assertEqual(candidate.requirements[0].unassigned_quantity, 5000)
        self.assertEqual(candidate.requirements[0].overassigned_quantity, 0)

        path.planned_share_quantity = 40000
        path.save(update_fields=["planned_share_quantity"])
        candidate = validate_approval_candidate(self.plan)
        self.assertEqual(candidate.requirements[0].unassigned_quantity, 0)
        self.assertEqual(candidate.requirements[0].overassigned_quantity, 0)

        path.planned_share_quantity = 45000
        path.save(update_fields=["planned_share_quantity"])
        candidate = validate_approval_candidate(self.plan)
        self.assertEqual(candidate.requirements[0].overassigned_quantity, 5000)

    def test_unknown_and_needs_review_require_explicit_manager_confirmation(self):
        unknown_path = self.create_path(sequence=1)
        review_mold = PhysicalMold.objects.create(
            code="S2B1-REVIEW", mold_type=self.mold_type
        )
        review_path = self.create_path(physical_mold=review_mold, sequence=2)
        MoldMachineCompatibility.objects.create_with_status(
            physical_mold=review_mold,
            machine=self.machine_a,
            status=MoldMachineCompatibility.Status.NEEDS_REVIEW,
            changed_by=self.user,
            reason="test setup",
        )
        candidate = validate_approval_candidate(self.plan)
        kinds = {(d.planned_path_id, d.decision_type) for d in candidate.required_decisions}
        self.assertIn((unknown_path.pk, "unknown_compatibility"), kinds)
        self.assertIn((review_path.pk, "needs_review_compatibility"), kinds)
        with self.assertRaises(ValidationError):
            validate_manager_confirmations(candidate, [], user=self.user)
        validate_manager_confirmations(
            candidate,
            [
                {"planned_path_id": path_id, "decision_type": kind, "confirmed": True}
                for path_id, kind in kinds
            ],
            user=self.user,
        )
        self.assertFalse(
            MoldMachineCompatibility.objects.filter(
                physical_mold=self.mold_a,
                machine=self.machine_a,
                status=MoldMachineCompatibility.Status.CONFIRMED,
            ).exists()
        )

    def test_incompatible_is_a_hard_block(self):
        self.create_path()
        MoldMachineCompatibility.objects.create_with_status(
            physical_mold=self.mold_a,
            machine=self.machine_a,
            status=MoldMachineCompatibility.Status.INCOMPATIBLE,
            changed_by=self.user,
            reason="test setup",
        )
        with self.assertRaises(ValidationError):
            validate_approval_candidate(self.plan)

    def test_under_repair_boundaries_and_missing_ready_date(self):
        cases = (
            ("BEFORE", jdatetime.date(1405, 2, 1), False),
            ("SAME", jdatetime.date(1405, 2, 2), False),
            ("AFTER", jdatetime.date(1405, 2, 3), True),
        )
        for index, (suffix, start, accepted) in enumerate(cases, start=1):
            mold = PhysicalMold.objects.create(
                code=f"S2B1-REPAIR-{suffix}",
                mold_type=self.mold_type,
                status=PhysicalMold.Status.UNDER_REPAIR,
                estimated_ready_date=jdatetime.date(1405, 2, 2),
            )
            self.confirm_compatibility(mold=mold)
            path = self.create_path(
                physical_mold=mold,
                planned_start_date=start,
                planned_end_date=jdatetime.date(1405, 2, 5),
                sequence=index,
            )
            if accepted:
                candidate = validate_approval_candidate(self.plan)
                self.assertIn(
                    (path.pk, "under_repair_future_use"),
                    {(d.planned_path_id, d.decision_type) for d in candidate.required_decisions},
                )
            else:
                with self.assertRaises(ValidationError):
                    validate_approval_candidate(self.plan)
            path.delete()

        no_date = PhysicalMold.objects.create(
            code="S2B1-REPAIR-NODATE",
            mold_type=self.mold_type,
            status=PhysicalMold.Status.UNDER_REPAIR,
        )
        self.confirm_compatibility(mold=no_date)
        self.create_path(physical_mold=no_date, sequence=1)
        with self.assertRaises(ValidationError):
            validate_approval_candidate(self.plan)

    def test_fingerprint_is_deterministic_and_detects_material_changes(self):
        self.confirm_compatibility()
        path = self.create_path()
        original = validate_approval_candidate(self.plan).fingerprint
        self.assertEqual(original, validate_approval_candidate(self.plan).fingerprint)

        changes = (
            ("planned_share_quantity", 21000),
            ("machine", self.machine_b),
            ("physical_mold", self.mold_b),
            ("planned_end_date", jdatetime.date(1405, 2, 4)),
        )
        for field, value in changes:
            path.refresh_from_db()
            setattr(path, field, value)
            path.save(update_fields=[field])
            changed = validate_approval_candidate(self.plan).fingerprint
            self.assertNotEqual(changed, original)
            original = changed

        with self.assertRaises(StaleApprovalCandidate):
            validate_approval_candidate(self.plan, submitted_fingerprint="0" * 64)

    def test_validation_has_no_operational_side_effects(self):
        compatibility = MoldMachineCompatibility.objects.create(
            physical_mold=self.mold_a, machine=self.machine_a
        )
        original_status = compatibility.status
        original_mold_status = self.mold_a.status
        self.create_path()
        validate_approval_candidate(self.plan)
        compatibility.refresh_from_db()
        self.mold_a.refresh_from_db()
        self.assertEqual(compatibility.status, original_status)
        self.assertEqual(self.mold_a.status, original_mold_status)
        self.assertFalse(PlanApprovalRevision.objects.exists())
        self.assertFalse(PhysicalMoldReservation.objects.exists())
        self.assertFalse(ProductionProgram.objects.exists())


class ApprovalDecisionEvidenceTests(PathFoundationTestCase):
    def create_decision(self, **overrides):
        path = overrides.pop("planned_path", None) or self.create_path()
        revision = overrides.pop("approval_revision", None) or self.create_revision(
            status=PlanApprovalRevision.Status.PREPARING,
            approved_by=None,
            approved_at=None,
        )
        values = {
            "approval_revision": revision,
            "planned_path": path,
            "decision_type": PlanApprovalDecision.DecisionType.UNKNOWN_COMPATIBILITY,
            "outcome": PlanApprovalDecision.Outcome.APPROVED,
            "condition_snapshot": {"compatibility_status": "unknown"},
            "decided_by": self.user,
            "reason": "",
        }
        values.update(overrides)
        return PlanApprovalDecision.objects.create(**values)

    def test_reason_is_optional_and_evidence_is_retained(self):
        decision = self.create_decision()
        self.assertEqual(decision.reason, "")
        self.assertEqual(decision.outcome, PlanApprovalDecision.Outcome.APPROVED)
        self.assertEqual(decision.decided_by, self.user)
        self.assertIsNotNone(decision.decided_at)
        self.assertEqual(decision.condition_snapshot["compatibility_status"], "unknown")

    def test_evidence_is_immutable_across_normal_orm_paths(self):
        decision = self.create_decision()
        decision.reason = "changed"
        with self.assertRaises(ValueError):
            decision.save()
        with self.assertRaises(ValueError):
            PlanApprovalDecision.objects.filter(pk=decision.pk).update(reason="changed")
        decision.reason = "changed"
        with self.assertRaises(ValueError):
            PlanApprovalDecision.objects.bulk_update([decision], ["reason"])
        with self.assertRaises(ValueError):
            decision.delete()
        with self.assertRaises(ValueError):
            PlanApprovalDecision.objects.filter(pk=decision.pk).delete()
        self.assertTrue(PlanApprovalDecision.objects.filter(pk=decision.pk).exists())

    def test_duplicate_decision_evidence_is_database_rejected(self):
        first = self.create_decision()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_decision(
                approval_revision=first.approval_revision,
                planned_path=first.planned_path,
            )

    def test_non_manager_cannot_create_decision_evidence(self):
        expert = get_user_model().objects.create_user(
            username="s2b1-decision-expert", password="test-only"
        )
        profile, _ = UserProfile.objects.get_or_create(user=expert)
        profile.role = Role.PLANNING_EXPERT
        profile.save(update_fields=["role"])
        with self.assertRaises(ValueError):
            self.create_decision(decided_by=expert)

    def test_non_manager_bulk_create_cannot_bypass_decision_authority(self):
        expert = get_user_model().objects.create_user(
            username="s2b1-bulk-decision-expert", password="test-only"
        )
        profile, _ = UserProfile.objects.get_or_create(user=expert)
        profile.role = Role.PLANNING_EXPERT
        profile.save(update_fields=["role"])
        path = self.create_path()
        revision = self.create_revision(
            status=PlanApprovalRevision.Status.PREPARING,
            approved_by=None,
            approved_at=None,
        )
        decision = PlanApprovalDecision(
            approval_revision=revision,
            planned_path=path,
            decision_type=PlanApprovalDecision.DecisionType.UNKNOWN_COMPATIBILITY,
            condition_snapshot={"compatibility_status": "unknown"},
            decided_by=expert,
        )
        with self.assertRaises(ValueError):
            PlanApprovalDecision.objects.bulk_create([decision])


class ReservationPreparingLifecycleTests(PathFoundationTestCase):
    def test_preparing_reservation_can_activate_once_then_activation_time_is_immutable(self):
        path = self.create_path()
        revision = self.create_revision(
            status=PlanApprovalRevision.Status.PREPARING,
            approved_by=None,
            approved_at=None,
        )
        reservation = self.create_reservation(
            path,
            approval_revision=revision,
            status=PhysicalMoldReservation.Status.PREPARING,
            activated_at=None,
        )
        activated_at = timezone.now()
        PhysicalMoldReservation.objects.filter(pk=reservation.pk).update(
            status=PhysicalMoldReservation.Status.ACTIVE,
            activated_at=activated_at,
        )
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(reservation.activated_at, activated_at)
        with self.assertRaises(ValueError):
            PhysicalMoldReservation.objects.filter(pk=reservation.pk).update(
                activated_at=timezone.now()
            )


class ApprovalPermissionAndLegacyGateTests(PathFoundationTestCase):
    def make_user(self, username, role):
        user = get_user_model().objects.create_user(username=username, password="test-only")
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = role
        profile.save(update_fields=["role"])
        return user

    def test_only_manager_or_superuser_passes_approval_authorization(self):
        self.create_path()
        validate_approval_candidate(
            self.plan, user=self.user, require_approval_permission=True
        )
        for role in (Role.PLANNING_EXPERT, Role.PLANNING_CLERK, Role.VIEWER):
            user = self.make_user(f"s2b1-{role}", role)
            with self.assertRaises(PermissionDenied):
                validate_approval_candidate(
                    self.plan, user=user, require_approval_permission=True
                )

    def test_creator_expert_cannot_approve_but_can_still_open_edit_mode(self):
        expert = self.make_user("s2b1-owner-expert", Role.PLANNING_EXPERT)
        self.plan.created_by = expert
        self.plan.save(update_fields=["created_by"])
        self.client.force_login(expert)
        denied = self.client.post(
            reverse("plan_set_status", args=[self.plan.pk]),
            {"status": WeeklyPlan.Status.APPROVED},
        )
        self.assertEqual(denied.status_code, 403)
        response = self.client.post(
            reverse("plan_operate", args=[self.plan.pk]), {"action": "edit"}
        )
        self.assertEqual(response.status_code, 302)

    def test_path_based_legacy_approval_is_blocked_before_any_program_creation(self):
        self.create_path()
        second_item = self.item.__class__.objects.create(
            plan=self.plan,
            subgroup=self.subgroup,
            unit=self.unit,
            machine=self.machine_b,
            product=self.product,
            mold=self.mold_type,
            mold_change_weekday=0,
            mold_change_date=jdatetime.date(1405, 2, 2),
        )
        WeeklyPlanLine.objects.create(item=second_item, quantity=1, cycle=1)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("plan_set_status", args=[self.plan.pk]),
            {"status": WeeklyPlan.Status.APPROVED},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, WeeklyPlan.Status.DRAFT)
        self.assertFalse(ProductionProgram.objects.exists())

    def test_legacy_plan_without_path_retains_manager_approval_behavior(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("plan_set_status", args=[self.plan.pk]),
            {"status": WeeklyPlan.Status.APPROVED},
        )
        self.assertEqual(response.status_code, 302)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, WeeklyPlan.Status.APPROVED)
        self.assertTrue(ProductionProgram.objects.filter(item=self.item).exists())
