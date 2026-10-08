import jdatetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, UserProfile
from catalog.models import (
    DeviationReason,
    Machine,
    MoldOption,
    PhysicalMold,
    Product,
    ProductGroup,
    ProductSubGroup,
    ProductionUnit,
)
from planning.models import PlannedProductionPath, WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine

from .models import (
    ProductionDeviation,
    ProductionExecutionPath,
    ProductionProgram,
    RevisionEvidenceQuerySet,
    ProductionTargetDecision,
    ProductionTargetRevision,
)
from .path_execution import (
    activate_execution_path,
    get_or_create_unstarted_execution_path,
    register_path_production,
)
from .target_revision_service import (
    DuplicatePendingTargetRevision,
    StaleTargetRevision,
    decide_target_revision,
    request_scrap_compensation,
    request_target_revision,
)
from .target_semantics import RequiredGoodUnavailable


class TargetRevisionWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = get_user_model().objects.create_user(username="c2-manager")
        cls.operator = get_user_model().objects.create_user(username="c2-operator")
        cls.viewer = get_user_model().objects.create_user(username="c2-viewer")
        for user, role in (
            (cls.manager, Role.PLANNING_MANAGER),
            (cls.operator, Role.PLANNING_EXPERT),
            (cls.viewer, Role.VIEWER),
        ):
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = role
            profile.save(update_fields=["role"])
        group = ProductGroup.objects.create(name="C2 group")
        subgroup = ProductSubGroup.objects.create(name="C2 subgroup", group=group)
        cls.unit = ProductionUnit.objects.create(number=982, name="C2 unit")
        cls.machine = Machine.objects.create(number="C2-MACHINE", unit=cls.unit)
        cls.product = Product.objects.create(code="C2-P", name="C2 product", subgroup=subgroup)
        cls.mold_type = MoldOption.objects.create(code="C2-T", label="C2 mold")
        cls.mold = PhysicalMold.objects.create(code="C2-M-1", mold_type=cls.mold_type)
        cls.other_mold = PhysicalMold.objects.create(code="C2-M-2", mold_type=cls.mold_type)
        cls.plan = WeeklyPlan.objects.create(
            program_number="C2-LEGACY", date=jdatetime.date(1406, 4, 1), created_by=cls.manager
        )
        cls.item = WeeklyPlanItem.objects.create(
            plan=cls.plan, subgroup=subgroup, unit=cls.unit, machine=cls.machine,
            product=cls.product, mold=cls.mold_type, mold_change_weekday=0,
            mold_change_date=cls.plan.date,
        )
        cls.line = WeeklyPlanLine.objects.create(
            item=cls.item, mold=cls.mold_type, quantity=100, cycle=30
        )
        cls.reason = DeviationReason.objects.create(
            label="C2 machine reason", code=DeviationReason.Code.MACHINE_BREAKDOWN,
            applicability=DeviationReason.Applicability.PRODUCTION,
        )
        cls.planning_reason = DeviationReason.objects.create(
            label="C2 planning reason", code=DeviationReason.Code.PRIORITY_CHANGE,
            applicability=DeviationReason.Applicability.PLANNING,
        )

    def make_path(self, *, mold=None, production=True, gross=40, scrap=5):
        planned = PlannedProductionPath.objects.create(
            requirement=self.line, machine=self.machine, physical_mold=mold or self.mold,
            planned_share_quantity=100, planned_start_date=self.plan.date,
            planned_end_date=self.plan.date + jdatetime.timedelta(days=2),
            sequence=self.line.planned_paths.count() + 1,
        )
        path, _ = get_or_create_unstarted_execution_path(planned, actor=self.manager)
        activate_execution_path(path, actor=self.manager)
        if production:
            register_path_production(
                path, date=self.plan.date, shots=gross, active_cavities=1,
                scrap_pieces=scrap, actor=self.operator,
            )
        return path

    def request(self, path, target, **kwargs):
        return request_target_revision(
            path,
            requested_target=target,
            reason=kwargs.pop("reason", self.reason),
            actor=kwargs.pop("actor", self.operator),
            **kwargs,
        )

    def test_request_authorization_and_reason_rules(self):
        path = self.make_path()
        with self.assertRaises(PermissionDenied):
            self.request(path, 120, actor=self.viewer)
        with self.assertRaises(ValidationError):
            self.request(path, 120, reason=self.planning_reason)
        self.reason.is_active = False
        self.reason.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            self.request(path, 120)

    def test_request_requires_production_eligible_status_change_and_floor(self):
        unstarted = self.make_path(mold=self.other_mold, production=False)
        with self.assertRaises(ValidationError):
            self.request(unstarted, 120)
        path = self.make_path()
        with self.assertRaises(ValidationError):
            self.request(path, 39)
        with self.assertRaises(ValidationError):
            self.request(path, 100)
        ProductionExecutionPath.objects.filter(pk=path.pk)._update_for_execution_service(
            status=ProductionExecutionPath.Status.TEMPORARY_STOP
        )
        self.assertEqual(self.request(path, 120).requested_target, 120)

    def test_full_stop_and_completed_are_blocked(self):
        for status in (
            ProductionExecutionPath.Status.FULL_STOP,
            ProductionExecutionPath.Status.STATISTICS_COMPLETED,
        ):
            path = self.make_path(mold=PhysicalMold.objects.create(
                code=f"C2-{status}", mold_type=self.mold_type
            ))
            ProductionExecutionPath.objects.filter(pk=path.pk)._update_for_execution_service(status=status)
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.request(path, 120)

    def test_one_pending_request_and_no_request_side_effects(self):
        path = self.make_path()
        first = self.request(path, 120)
        with self.assertRaises(DuplicatePendingTargetRevision):
            self.request(path, 130)
        path.refresh_from_db()
        self.assertEqual(path.current_good_target, 100)
        self.assertFalse(ProductionTargetDecision.objects.exists())
        self.assertFalse(ProductionDeviation.objects.exists())
        self.assertEqual(first.delta, 20)

    def test_direct_orm_request_creation_paths_are_rejected(self):
        path = self.make_path()
        values = {
            "execution_path": path,
            "original_target": 100,
            "previous_effective_target": 100,
            "requested_target": 120,
            "delta": 20,
            "source": ProductionTargetRevision.Source.MANUAL,
            "requester": self.operator,
            "reason": self.reason,
        }
        with self.assertRaisesRegex(ValueError, "canonical target-revision service"):
            ProductionTargetRevision.objects.create(**values)
        with self.assertRaisesRegex(ValueError, "canonical target-revision service"):
            ProductionTargetRevision.objects.get_or_create(
                execution_path=path, requested_target=120, defaults=values
            )
        with self.assertRaisesRegex(ValueError, "canonical target-revision service"):
            ProductionTargetRevision.objects.update_or_create(
                execution_path=path, requested_target=120, defaults=values
            )
        instance = ProductionTargetRevision(**values)
        with self.assertRaisesRegex(ValueError, "canonical target-revision service"):
            instance.save_base(force_insert=True)
        self.assertFalse(ProductionTargetRevision.objects.exists())

    def test_generic_service_cannot_impersonate_scrap_compensation(self):
        path = self.make_path()
        with self.assertRaisesRegex(ValidationError, "Unsupported target-revision source"):
            request_target_revision(
                path,
                requested_target=120,
                reason=self.reason,
                actor=self.operator,
                source=ProductionTargetRevision.Source.SCRAP_COMPENSATION,
            )
        self.assertFalse(ProductionTargetRevision.objects.exists())

    def test_approval_increase_creates_one_decision_and_deviation(self):
        path = self.make_path()
        request = self.request(path, 130)
        result = decide_target_revision(
            request, outcome=ProductionTargetDecision.Outcome.APPROVED, actor=self.manager
        )
        path.refresh_from_db()
        self.assertEqual(path.current_good_target, 130)
        self.assertEqual(path.initial_good_target, 100)
        self.assertEqual(result.decision.signed_delta, 30)
        self.assertEqual(result.deviation.explained_quantity, 30)
        self.assertEqual(result.deviation.target_decision, result.decision)
        self.assertEqual(request.deviations.count(), 1)
        with self.assertRaises(ValidationError):
            decide_target_revision(
                request, outcome=ProductionTargetDecision.Outcome.APPROVED, actor=self.manager
            )

    def test_decrease_equal_to_gross_and_signed_delta(self):
        path = self.make_path(gross=40)
        request = self.request(path, 40)
        result = decide_target_revision(
            request, outcome=ProductionTargetDecision.Outcome.APPROVED, actor=self.manager
        )
        path.refresh_from_db()
        self.assertEqual(path.current_good_target, 40)
        self.assertEqual(result.decision.signed_delta, -60)
        self.assertEqual(result.deviation.explained_quantity, 60)

    def test_manager_modification_and_noop_are_validated(self):
        path = self.make_path()
        request = self.request(path, 130)
        result = decide_target_revision(
            request, outcome=ProductionTargetDecision.Outcome.MODIFIED,
            decided_target=125, actor=self.manager, note="manager adjustment",
        )
        self.assertEqual(result.decision.decided_target, 125)
        self.assertEqual(result.decision.signed_delta, 25)
        second = self.make_path(mold=self.other_mold)
        no_op = self.request(second, 120)
        with self.assertRaises(ValidationError):
            decide_target_revision(
                no_op, outcome=ProductionTargetDecision.Outcome.MODIFIED,
                decided_target=100, actor=self.manager,
            )

    def test_rejection_and_decision_authorization(self):
        path = self.make_path()
        request = self.request(path, 120)
        with self.assertRaises(PermissionDenied):
            decide_target_revision(
                request, outcome=ProductionTargetDecision.Outcome.REJECTED,
                actor=self.operator,
            )
        result = decide_target_revision(
            request, outcome=ProductionTargetDecision.Outcome.REJECTED,
            actor=self.manager, note="not required",
        )
        path.refresh_from_db()
        self.assertEqual(path.current_good_target, 100)
        self.assertIsNone(result.deviation)
        self.assertFalse(ProductionDeviation.objects.exists())

    def test_deviation_failure_rolls_back_decision_and_target_update(self):
        path = self.make_path()
        request = self.request(path, 130)
        with patch.object(
            RevisionEvidenceQuerySet,
            "create",
            side_effect=RuntimeError("forced deviation failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced deviation failure"):
                decide_target_revision(
                    request,
                    outcome=ProductionTargetDecision.Outcome.APPROVED,
                    actor=self.manager,
                )
        path.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(path.current_good_target, 100)
        self.assertEqual(request.state, ProductionTargetRevision.State.PENDING)
        self.assertFalse(ProductionTargetDecision.objects.exists())
        self.assertFalse(ProductionDeviation.objects.exists())

    def test_stale_request_is_not_rewritten(self):
        path = self.make_path()
        request = self.request(path, 120)
        ProductionExecutionPath.objects.filter(pk=path.pk)._update_counter_target_for_revision(
            current_good_target=110, updated_by=self.manager
        )
        with self.assertRaises(StaleTargetRevision):
            decide_target_revision(
                request, outcome=ProductionTargetDecision.Outcome.APPROVED,
                actor=self.manager,
            )
        self.assertFalse(ProductionTargetDecision.objects.exists())

    def test_evidence_is_immutable_and_database_unique(self):
        path = self.make_path()
        request = self.request(path, 120)
        result = decide_target_revision(
            request, outcome=ProductionTargetDecision.Outcome.APPROVED, actor=self.manager
        )
        for evidence in (request, result.decision, result.deviation):
            with self.subTest(model=type(evidence).__name__), self.assertRaises(ValueError):
                evidence.delete()
        duplicate = ProductionDeviation(
            execution_path=path, category=ProductionDeviation.Category.TARGET_CHANGE,
            reason=self.reason, explained_quantity=20, actor=self.manager,
            target_revision=request,
        )
        with self.assertRaises((IntegrityError, ValidationError)), transaction.atomic():
            duplicate.save()
        with self.assertRaises(ValueError):
            ProductionTargetDecision.objects.filter(pk=result.decision.pk).update(note="changed")
        with self.assertRaises(ValueError):
            ProductionTargetDecision.objects.filter(pk=result.decision.pk).delete()
        direct = ProductionTargetDecision(
            request=request,
            outcome=ProductionTargetDecision.Outcome.APPROVED,
            decided_target=120,
            signed_delta=20,
            cumulative_gross_at_decision=40,
            decided_by=self.manager,
        )
        with self.assertRaisesRegex(ValueError, "canonical target-revision service"):
            direct.save()

    def test_independent_paths_and_old_program_identity(self):
        first = self.make_path()
        second = self.make_path(mold=self.other_mold)
        self.request(first, 120)
        self.request(second, 130)
        self.assertEqual(ProductionTargetRevision.objects.count(), 2)
        legacy_program = ProductionProgram.objects.create(item=self.item)
        with self.assertRaises(ValidationError):
            request_target_revision(
                legacy_program, requested_target=120, reason=self.reason, actor=self.operator
            )

    def test_scrap_compensation_requires_final_lineage(self):
        path = self.make_path(gross=100, scrap=50)
        with self.assertRaisesRegex(RequiredGoodUnavailable, "Final-path approval lineage"):
            request_scrap_compensation(path, actor=self.operator)

    def test_endpoints_are_post_only_and_enforce_backend_permissions(self):
        path = self.make_path()
        request_url = reverse("target_revision_request", args=[path.pk])
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(request_url).status_code, 405)
        response = self.client.post(
            request_url,
            {
                "requested_target": 120,
                "reason": self.reason.pk,
                "source": ProductionTargetRevision.Source.MANUAL,
                "note": "operator request",
            },
        )
        self.assertEqual(response.status_code, 201)
        revision = ProductionTargetRevision.objects.get(pk=response.json()["request_id"])
        decide_url = reverse("target_revision_decide", args=[revision.pk])
        self.assertEqual(self.client.get(decide_url).status_code, 405)
        self.assertEqual(
            self.client.post(
                decide_url,
                {"outcome": ProductionTargetDecision.Outcome.REJECTED, "note": "no"},
            ).status_code,
            403,
        )
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.post(
                decide_url,
                {"outcome": ProductionTargetDecision.Outcome.REJECTED, "note": "no"},
            ).status_code,
            200,
        )

    def test_request_endpoint_preserves_csrf_and_ignores_spoofed_source(self):
        path = self.make_path()
        url = reverse("target_revision_request", args=[path.pk])
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.operator)
        self.assertEqual(
            csrf_client.post(
                url,
                {
                    "requested_target": 120,
                    "reason": self.reason.pk,
                    "source": ProductionTargetRevision.Source.MANUAL,
                },
            ).status_code,
            403,
        )
        self.client.force_login(self.operator)
        response = self.client.post(
            url,
            {
                "requested_target": 120,
                "reason": self.reason.pk,
                "source": ProductionTargetRevision.Source.SCRAP_COMPENSATION,
            },
        )
        self.assertEqual(response.status_code, 201)
        revision = ProductionTargetRevision.objects.get(execution_path=path)
        self.assertEqual(revision.source, ProductionTargetRevision.Source.MANUAL)
