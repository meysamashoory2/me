import jdatetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserProfile
from catalog.models import DeviationReason, PhysicalMold
from planning.models import (
    FinalProgramRevision,
    PathOperationalApproval,
    PhysicalMoldReservation,
    WeeklyPlan,
    WeeklyProgramCycle,
)
from planning.tests_path_foundation import PathFoundationTestCase

from .models import ProductionExecutionPath, ProductionProgram, ProductionRequirementProgram
from .path_execution import register_path_production
from .target_revision_service import decide_target_revision, request_target_revision
from .target_semantics import RequiredGoodUnavailable, resolve_required_good
from .views import _governed_path_queryset


class GovernedProductionReadOnlyUITests(PathFoundationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.viewer = get_user_model().objects.create_user(
            username="governed-ui-viewer", password="test-only"
        )
        profile, _ = UserProfile.objects.get_or_create(user=cls.viewer)
        profile.role = Role.VIEWER
        profile.save(update_fields=["role"])
        cls.reason = DeviationReason.objects.create(
            label="Governed UI target reason",
            code=DeviationReason.Code.TARGET_REALLOCATION,
            applicability=DeviationReason.Applicability.PRODUCTION,
        )

    def make_execution(self, *, machine=None, mold=None, target=20000):
        program, _ = ProductionRequirementProgram.objects.get_or_create(
            requirement=self.requirement, defaults={"created_by": self.user}
        )
        path = ProductionExecutionPath.objects.create(
            requirement_program=program,
            machine=machine or self.machine_a,
            physical_mold=mold or self.mold_a,
            initial_good_target=target,
            current_good_target=target,
            status=ProductionExecutionPath.Status.AWAITING,
            created_by=self.user,
        )
        planned = self.create_path(
            machine=path.machine,
            physical_mold=path.physical_mold,
            planned_share_quantity=target,
        )
        revision = self.plan.approval_revisions.first() or self.create_revision()
        reservation = self.create_reservation(
            planned,
            approval_revision=revision,
            execution_path=path,
            approved_planned_share_quantity=target,
        )
        return path, reservation

    def make_governed_final(self, path, reservation):
        cycle = WeeklyProgramCycle.objects.create(
            program_number=88000 + path.pk,
            program_date=self.plan.date,
            created_by=self.user,
        )
        manual = WeeklyPlan.objects.create(
            program_number=f"GOVERNED-UI-MANUAL-{path.pk}",
            date=self.plan.date,
            planning_mode=WeeklyPlan.PlanningMode.MANUAL,
            governance_cycle=cycle,
            created_by=self.user,
        )
        self.plan.governance_cycle = cycle
        self.plan.planning_mode = WeeklyPlan.PlanningMode.FINAL
        self.plan.save(update_fields=["governance_cycle", "planning_mode"])
        cycle.final_plan = self.plan
        cycle.save(update_fields=["final_plan"])
        revision = FinalProgramRevision(
            cycle=cycle,
            revision_number=1,
            final_plan=self.plan,
            manual_source=manual,
            status=FinalProgramRevision.Status.EFFECTIVE,
            source_review_fingerprint="a" * 64,
            result_fingerprint="b" * 64,
            approved_by=self.user,
        )
        revision._merge_service_create = True
        try:
            revision.save(force_insert=True)
        finally:
            del revision._merge_service_create
        approval = PathOperationalApproval(
            planned_path=reservation.source_path,
            execution_path=path,
            reservation=reservation,
            sequence=1,
            action=PathOperationalApproval.Action.APPROVED,
            path_fingerprint="c" * 64,
            after_snapshot={"planned_share_quantity": path.initial_good_target},
            actor=self.user,
        )
        approval._operational_approval_service_create = True
        try:
            approval.save(force_insert=True)
        finally:
            del approval._operational_approval_service_create
        return approval

    def test_authentication_list_identity_metrics_and_reservation(self):
        path, reservation = self.make_execution(target=200)
        register_path_production(
            path,
            date=jdatetime.date(1405, 2, 1),
            shots=50,
            active_cavities=2,
            scrap_pieces=10,
            actor=self.user,
        )
        url = reverse("execution_path_list")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.viewer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, path.requirement_program.program_number)
        self.assertContains(response, self.product.code)
        self.assertContains(response, self.mold_a.code)
        self.assertContains(response, reservation.get_status_display())
        row = response.context["rows"][0]
        self.assertEqual(row["metrics"].cumulative_total_produced, 100)
        self.assertEqual(row["metrics"].cumulative_good, 90)
        self.assertEqual(row["metrics"].counter_remaining, 100)
        self.assertEqual(row["metrics"].good_shortage, 110)

    def test_detail_renders_daily_event_target_decision_and_deviation_history(self):
        path, reservation = self.make_execution(target=200)
        entry = register_path_production(
            path,
            date=jdatetime.date(1405, 2, 1),
            shots=100,
            active_cavities=1,
            scrap_pieces=10,
            actor=self.user,
            description="governed UI daily evidence",
        )
        request = request_target_revision(
            path,
            requested_target=220,
            reason=self.reason,
            actor=self.user,
            note="capacity review",
        )
        decide_target_revision(
            request,
            outcome="approved",
            actor=self.user,
            note="manager approval",
        )
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("execution_path_detail", args=[path.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "governed UI daily evidence")
        self.assertContains(response, "1405/02/01")
        self.assertContains(response, reservation.get_status_display())
        self.assertContains(response, "Governed UI target reason")
        self.assertContains(response, "manager approval")
        self.assertContains(response, "Approved")
        self.assertContains(response, ">220<")
        self.assertTrue(response.context["events"].exists())
        self.assertTrue(response.context["target_revisions"].exists())
        self.assertTrue(response.context["deviations"].exists())
        self.assertNotContains(response, reverse("execution_path_temporary_stop", args=[path.pk]))
        self.assertNotContains(response, reverse("execution_path_full_stop", args=[path.pk]))
        self.assertNotContains(response, reverse("target_revision_request", args=[path.pk]))

    def test_machine_and_status_filters_are_independent(self):
        first, _ = self.make_execution()
        second_mold = PhysicalMold.objects.create(
            code="GOVERNED-UI-MOLD-2", mold_type=self.mold_type
        )
        second, _ = self.make_execution(machine=self.machine_b, mold=second_mold)
        ProductionExecutionPath.objects.filter(pk=second.pk)._update_for_execution_service(
            status=ProductionExecutionPath.Status.CANCELLED,
            status_changed_at=timezone.now(),
        )
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("execution_path_list"),
            {"machine": self.machine_a.pk, "status": ProductionExecutionPath.Status.AWAITING},
        )
        paths = [row["path"].pk for row in response.context["rows"]]
        self.assertEqual(paths, [first.pk])

    def test_governed_missing_final_lineage_fails_closed(self):
        path, _ = self.make_execution(target=321)
        cycle = WeeklyProgramCycle.objects.create(
            program_number=88001,
            program_date=self.plan.date,
            created_by=self.user,
        )
        self.plan.governance_cycle = cycle
        self.plan.save(update_fields=["governance_cycle"])
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("execution_path_detail", args=[path.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["metrics"])
        self.assertContains(response, "نمایش محاسبات مسدود است")
        self.assertContains(response, "Final-path lineage")

    def test_valid_governed_final_path_is_classified_from_real_lineage(self):
        path, reservation = self.make_execution(target=777)
        approval = self.make_governed_final(path, reservation)
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("execution_path_detail", args=[path.pk]))
        self.assertEqual(response.context["lineage_classification"], "governed_final")
        self.assertContains(response, "مسیر نهایی حاکمیتی معتبر")
        self.assertContains(response, f"#{approval.planned_path_id}")

    def test_prefetched_conflicting_real_approvals_fail_closed(self):
        path, reservation = self.make_execution(target=800)
        conflicting_path = self.create_path(
            physical_mold=self.mold_b,
            planned_share_quantity=900,
            sequence=2,
        )
        self.make_governed_final(path, reservation)
        conflicting = PathOperationalApproval(
            planned_path=conflicting_path,
            execution_path=path,
            reservation=reservation,
            sequence=1,
            action=PathOperationalApproval.Action.APPROVED,
            path_fingerprint="d" * 64,
            after_snapshot={"planned_share_quantity": 900},
            actor=self.user,
        )
        conflicting._operational_approval_service_create = True
        try:
            conflicting.save(force_insert=True)
        finally:
            del conflicting._operational_approval_service_create
        loaded = _governed_path_queryset().get(pk=path.pk)
        with self.assertRaisesRegex(RequiredGoodUnavailable, "conflicting Good allocations"):
            resolve_required_good(loaded)

    def test_required_good_prefetch_respects_database_alias(self):
        path, reservation = self.make_execution(target=811)
        self.make_governed_final(path, reservation)
        loaded = _governed_path_queryset().get(pk=path.pk)
        with self.assertNumQueries(0):
            self.assertEqual(resolve_required_good(loaded), 811)
        approvals = list(loaded.operational_approvals.all())
        ordered = MagicMock()
        ordered.__iter__.return_value = iter(approvals)
        filtered = MagicMock()
        filtered.select_related.return_value.order_by.return_value = ordered
        using_queryset = MagicMock()
        using_queryset.filter.return_value = filtered
        with patch(
            "planning.models.PathOperationalApproval.objects.using",
            return_value=using_queryset,
        ) as using_mock:
            self.assertEqual(resolve_required_good(loaded, using="archive"), 811)
        using_mock.assert_called_once_with("archive")

        empty_ordered = MagicMock()
        empty_ordered.__iter__.return_value = iter(())
        empty_filtered = MagicMock()
        empty_filtered.select_related.return_value.order_by.return_value = empty_ordered
        empty_using_queryset = MagicMock()
        empty_using_queryset.filter.return_value = empty_filtered
        final_using_queryset = MagicMock()
        final_using_queryset.filter.return_value.exists.return_value = True
        with (
            patch(
                "planning.models.PathOperationalApproval.objects.using",
                return_value=empty_using_queryset,
            ),
            patch(
                "planning.models.FinalProgramRevision.objects.using",
                return_value=final_using_queryset,
            ) as final_using_mock,
        ):
            with self.assertRaisesRegex(RequiredGoodUnavailable, "no immutable"):
                resolve_required_good(loaded, using="archive")
        final_using_mock.assert_called_once_with("archive")

    def test_ambiguous_lineage_fails_closed_without_partial_metrics(self):
        path, _ = self.make_execution(target=654)
        self.client.force_login(self.viewer)
        with patch(
            "production.views.resolve_required_good",
            side_effect=RequiredGoodUnavailable("Ambiguous Final-path lineage."),
        ):
            response = self.client.get(reverse("execution_path_detail", args=[path.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["metrics"])
        self.assertContains(response, "Ambiguous Final-path lineage")

    def test_legacy_program_does_not_appear_in_governed_workspace(self):
        path, _ = self.make_execution()
        legacy = ProductionProgram.objects.create(item=self.item)
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("execution_path_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, legacy.resolved_uid)
        row = next(item for item in response.context["rows"] if item["path"].pk == path.pk)
        self.assertEqual(row["lineage_classification"], "legacy_compatible")
        self.assertContains(response, "مسیر سازگار قدیمی")
        self.assertNotContains(response, "مسیر نهایی حاکمیتی")

    def test_pending_modified_and_rejected_decisions_are_presented_from_decision_evidence(self):
        path, _ = self.make_execution(target=200)
        register_path_production(
            path, date=jdatetime.date(1405, 2, 1), shots=10,
            active_cavities=1, scrap_pieces=0, actor=self.user,
        )
        pending = request_target_revision(
            path, requested_target=210, reason=self.reason, actor=self.user
        )
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("execution_path_detail", args=[path.pk]))
        self.assertContains(response, "در انتظار تصمیم")
        decide_target_revision(
            pending, outcome="modified", decided_target=215, actor=self.user
        )
        rejected = request_target_revision(
            path, requested_target=230, reason=self.reason, actor=self.user
        )
        decide_target_revision(rejected, outcome="rejected", actor=self.user)
        response = self.client.get(reverse("execution_path_detail", args=[path.pk]))
        self.assertContains(response, "Modified and approved")
        self.assertContains(response, ">215<")
        self.assertContains(response, "Rejected")

    def test_active_reservation_cardinality_is_explicit_and_read_only(self):
        path, reservation = self.make_execution()
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("execution_path_detail", args=[path.pk]))
        self.assertEqual(response.context["reservation_state"], "single")
        PhysicalMoldReservation.objects.filter(pk=reservation.pk).update(
            status=PhysicalMoldReservation.Status.RELEASED,
            released_at=timezone.now(),
        )
        response = self.client.get(reverse("execution_path_detail", args=[path.pk]))
        self.assertEqual(response.context["reservation_state"], "none")
        self.assertContains(response, "رزرو قالب فعال ندارد")

        second_path = self.create_path(
            physical_mold=self.mold_b,
            planned_share_quantity=path.initial_good_target,
            sequence=2,
        )
        revision = self.plan.approval_revisions.first()
        first_active = self.create_reservation(
            reservation.source_path,
            approval_revision=revision,
            execution_path=path,
        )
        second_active = self.create_reservation(
            second_path,
            approval_revision=revision,
            execution_path=path,
        )
        response = self.client.get(reverse("execution_path_detail", args=[path.pk]))
        self.assertEqual(response.context["reservation_state"], "multiple")
        self.assertIsNone(response.context["reservation"])
        self.assertContains(response, "بیش از یک رزرو قالب فعال")
        first_active.refresh_from_db()
        second_active.refresh_from_db()
        self.assertEqual(first_active.status, PhysicalMoldReservation.Status.ACTIVE)
        self.assertEqual(second_active.status, PhysicalMoldReservation.Status.ACTIVE)

    def test_list_query_count_is_bounded_for_multiple_paths(self):
        self.make_execution()
        second_mold = PhysicalMold.objects.create(
            code="GOVERNED-UI-MOLD-Q", mold_type=self.mold_type
        )
        self.make_execution(machine=self.machine_b, mold=second_mold)
        self.client.force_login(self.viewer)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("execution_path_list"))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 15)
