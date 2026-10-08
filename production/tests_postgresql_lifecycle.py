import threading
from unittest import skipUnless

import jdatetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import Role, UserProfile
from catalog.models import (
    DeviationReason,
    Machine,
    MoldMachineCompatibility,
    MoldOption,
    PhysicalMold,
    Product,
    ProductGroup,
    ProductSubGroup,
    ProductionUnit,
)
from planning.models import (
    FinalProgramRevision,
    PhysicalMoldReservation,
    PlannedProductionPath,
    ProgramSourceApproval,
    Weekday,
    WeeklyPlan,
    WeeklyPlanItem,
    WeeklyPlanLine,
    WeeklyProgramCycle,
)
from planning.operational_approval import approve_path, preview_path_approval

from .execution_lifecycle import full_stop_execution_path
from .models import ProductionExecutionEvent, ProductionExecutionPath, ProductionTargetDecision
from .path_execution import register_path_production
from .target_revision_service import decide_target_revision, request_target_revision


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql", "PostgreSQL integration test"
)


@POSTGRESQL_ONLY
class PostgreSQLLifecycleConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(username="pg-close-manager")
        self.operator = User.objects.create_user(username="pg-close-operator")
        for user, role in (
            (self.manager, Role.PLANNING_MANAGER),
            (self.operator, Role.PLANNING_EXPERT),
        ):
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = role
            profile.save(update_fields=["role"])
        group = ProductGroup.objects.create(name="PG close group")
        subgroup = ProductSubGroup.objects.create(name="PG close subgroup", group=group)
        unit = ProductionUnit.objects.create(number=978, name="PG close unit")
        machine = Machine.objects.create(number="PG-CLOSE-MACHINE", unit=unit)
        product = Product.objects.create(code="PG-CLOSE-P", name="PG close product", subgroup=subgroup)
        mold_type = MoldOption.objects.create(code="PG-CLOSE-T", label="PG close mold")
        mold = PhysicalMold.objects.create(code="PG-CLOSE-M", mold_type=mold_type)
        cycle = WeeklyProgramCycle.objects.create(
            program_number=99002,
            program_date=jdatetime.date(1406, 7, 1),
            created_by=self.manager,
        )
        manual = WeeklyPlan.objects.create(
            program_number="PG-CLOSE-MANUAL",
            date=cycle.program_date,
            planning_mode=WeeklyPlan.PlanningMode.MANUAL,
            governance_cycle=cycle,
            created_by=self.manager,
        )
        final = WeeklyPlan(
            program_number="PG-CLOSE-FINAL",
            date=cycle.program_date,
            planning_mode=WeeklyPlan.PlanningMode.FINAL,
            governance_cycle=cycle,
            created_by=self.manager,
        )
        final._merge_service_create = True
        final.save(force_insert=True)
        cycle.final_plan = final
        cycle.save(update_fields=["final_plan"])
        approval = ProgramSourceApproval(
            source_plan=manual,
            approval_type=ProgramSourceApproval.ApprovalType.MANUAL,
            content_fingerprint="c" * 64,
            content_snapshot={},
            approved_by=self.manager,
        )
        approval._governance_service_create = True
        approval.save(force_insert=True)
        item = WeeklyPlanItem.objects.create(
            plan=final,
            subgroup=subgroup,
            unit=unit,
            machine=machine,
            product=product,
            mold=mold_type,
            mold_change_weekday=Weekday.SHANBE,
            mold_change_date=cycle.program_date,
        )
        requirement = WeeklyPlanLine.objects.create(
            item=item, mold=mold_type, quantity=1000, cycle=20
        )
        planned = PlannedProductionPath.objects.create(
            requirement=requirement,
            machine=machine,
            physical_mold=mold,
            planned_share_quantity=1000,
            planned_start_date=cycle.program_date,
            planned_end_date=cycle.program_date + jdatetime.timedelta(days=2),
            sequence=1,
            created_by=self.manager,
            updated_by=self.manager,
        )
        revision = FinalProgramRevision(
            cycle=cycle,
            revision_number=1,
            final_plan=final,
            manual_source=manual,
            status=FinalProgramRevision.Status.EFFECTIVE,
            source_review_fingerprint="a" * 64,
            result_fingerprint="b" * 64,
            approved_by=self.manager,
        )
        revision._merge_service_create = True
        revision.save(force_insert=True)
        preview = preview_path_approval(planned, user=self.manager, require_permission=True)
        operation = approve_path(
            planned.pk,
            submitted_fingerprint=preview.fingerprint,
            actor=self.manager,
            confirmations=[MoldMachineCompatibility.Status.UNKNOWN],
        )
        self.path = operation.execution_path
        self.reservation = operation.reservation
        register_path_production(
            self.path,
            date=cycle.program_date,
            shots=10,
            active_cavities=1,
            scrap_pieces=1,
            actor=self.operator,
        )
        self.reason = DeviationReason.objects.create(
            label="PG close reason",
            code=DeviationReason.Code.MACHINE_BREAKDOWN,
            applicability=DeviationReason.Applicability.PRODUCTION,
        )

    def _parallel(self, functions):
        barrier = threading.Barrier(len(functions))
        lock = threading.Lock()
        outcomes = []

        def worker(function):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                value = function()
                outcome = ("ok", value)
            except ValidationError as error:
                outcome = ("validation", str(error))
            except Exception as error:
                outcome = ("error", f"{type(error).__name__}: {error}")
            finally:
                close_old_connections()
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=worker, args=(function,)) for function in functions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=25)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        return outcomes

    def _stop(self):
        return full_stop_execution_path(
            self.path.pk,
            actor=get_user_model().objects.get(pk=self.manager.pk),
            shortfall_reason=DeviationReason.objects.get(pk=self.reason.pk),
        ).event.pk

    def test_concurrent_full_stop_creates_one_closeout(self):
        outcomes = self._parallel([self._stop, self._stop])
        self.assertEqual([kind for kind, _ in outcomes].count("ok"), 1, outcomes)
        self.assertEqual([kind for kind, _ in outcomes].count("validation"), 1, outcomes)
        self.assertEqual(
            ProductionExecutionEvent.objects.filter(
                execution_path=self.path,
                event_type=ProductionExecutionEvent.EventType.FULL_STOP,
            ).count(),
            1,
        )
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, PhysicalMoldReservation.Status.RELEASED)

    def test_concurrent_production_and_full_stop_are_serialized(self):
        def produce():
            return register_path_production(
                ProductionExecutionPath.objects.get(pk=self.path.pk),
                date=self.path.requirement_program.requirement.item.plan.date
                + jdatetime.timedelta(days=1),
                shots=5,
                active_cavities=1,
                scrap_pieces=0,
                actor=get_user_model().objects.get(pk=self.operator.pk),
            ).pk

        outcomes = self._parallel([produce, self._stop])
        self.assertFalse(any(kind == "error" for kind, _ in outcomes), outcomes)
        self.assertEqual([kind for kind, _ in outcomes].count("ok"), 1 if any(kind == "validation" for kind, _ in outcomes) else 2)
        self.path.refresh_from_db()
        self.assertEqual(self.path.status, ProductionExecutionPath.Status.FULL_STOP)

    def test_concurrent_target_decision_and_full_stop_preserve_integrity(self):
        request = request_target_revision(
            self.path,
            requested_target=1100,
            reason=self.reason,
            actor=self.operator,
        )

        def decide():
            return decide_target_revision(
                request.pk,
                outcome=ProductionTargetDecision.Outcome.APPROVED,
                actor=get_user_model().objects.get(pk=self.manager.pk),
            ).decision.pk

        outcomes = self._parallel([decide, self._stop])
        self.assertFalse(any(kind == "error" for kind, _ in outcomes), outcomes)
        self.assertTrue(ProductionTargetDecision.objects.filter(request=request).exists())
        self.path.refresh_from_db()
        self.assertIn(
            self.path.status,
            {ProductionExecutionPath.Status.RUNNING, ProductionExecutionPath.Status.FULL_STOP},
        )
