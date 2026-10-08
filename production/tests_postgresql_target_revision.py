import threading
from unittest import skipUnless

import jdatetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase

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
from production.models import ProductionTargetDecision
from production.path_execution import (
    activate_execution_path,
    get_or_create_unstarted_execution_path,
    register_path_production,
)
from production.target_revision_service import (
    decide_target_revision,
    request_target_revision,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql", "PostgreSQL integration test"
)


@POSTGRESQL_ONLY
class PostgreSQLTargetRevisionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(username="pg-c2-manager")
        self.operator = User.objects.create_user(username="pg-c2-operator")
        for user, role in (
            (self.manager, Role.PLANNING_MANAGER),
            (self.operator, Role.PLANNING_EXPERT),
        ):
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = role
            profile.save(update_fields=["role"])
        group = ProductGroup.objects.create(name="PG C2 group")
        subgroup = ProductSubGroup.objects.create(name="PG C2 subgroup", group=group)
        unit = ProductionUnit.objects.create(number=980, name="PG C2 unit")
        machine = Machine.objects.create(number="PG-C2-MACHINE", unit=unit)
        product = Product.objects.create(code="PG-C2-P", name="PG C2 product", subgroup=subgroup)
        mold_type = MoldOption.objects.create(code="PG-C2-T", label="PG C2 mold")
        mold = PhysicalMold.objects.create(code="PG-C2-M", mold_type=mold_type)
        plan = WeeklyPlan.objects.create(
            program_number="PG-C2-PLAN",
            date=jdatetime.date(1406, 5, 1),
            created_by=self.manager,
        )
        item = WeeklyPlanItem.objects.create(
            plan=plan,
            subgroup=subgroup,
            unit=unit,
            machine=machine,
            product=product,
            mold=mold_type,
            mold_change_weekday=0,
            mold_change_date=plan.date,
        )
        line = WeeklyPlanLine.objects.create(item=item, mold=mold_type, quantity=100, cycle=30)
        planned = PlannedProductionPath.objects.create(
            requirement=line,
            machine=machine,
            physical_mold=mold,
            planned_share_quantity=100,
            planned_start_date=plan.date,
            planned_end_date=plan.date + jdatetime.timedelta(days=2),
            sequence=1,
        )
        self.path, _ = get_or_create_unstarted_execution_path(planned, actor=self.manager)
        activate_execution_path(self.path, actor=self.manager)
        register_path_production(
            self.path,
            date=plan.date,
            shots=40,
            active_cavities=1,
            scrap_pieces=0,
            actor=self.operator,
        )
        self.production_date = plan.date
        self.reason = DeviationReason.objects.create(
            label="PG C2 reason",
            code=DeviationReason.Code.MACHINE_BREAKDOWN,
            applicability=DeviationReason.Applicability.PRODUCTION,
        )

    def parallel(self, functions):
        barrier = threading.Barrier(len(functions))
        lock = threading.Lock()
        results = []

        def worker(function):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                result = function()
            except (ValidationError, IntegrityError) as error:
                result = type(error).__name__
            except Exception as error:  # pragma: no cover - diagnostic detail
                result = f"unexpected:{type(error).__name__}:{error}"
            finally:
                close_old_connections()
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker, args=(function,)) for function in functions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads), "possible deadlock")
        self.assertFalse(any(str(result).startswith("unexpected:") for result in results), results)
        return results

    def create_request(self, target=60):
        return request_target_revision(
            self.path.pk,
            requested_target=target,
            reason=DeviationReason.objects.get(pk=self.reason.pk),
            actor=get_user_model().objects.get(pk=self.operator.pk),
        )

    def test_concurrent_request_creation_accepts_exactly_one(self):
        results = self.parallel([lambda: self.create_request().pk, lambda: self.create_request().pk])
        self.assertEqual(sum(isinstance(result, int) for result in results), 1, results)
        self.assertEqual(self.path.target_revisions.filter(decision__isnull=True).count(), 1)

    def test_concurrent_decisions_create_one_decision_and_one_deviation(self):
        request = self.create_request(60)

        def decide():
            return decide_target_revision(
                request.pk,
                outcome=ProductionTargetDecision.Outcome.APPROVED,
                actor=get_user_model().objects.get(pk=self.manager.pk),
            ).decision.pk

        results = self.parallel([decide, decide])
        self.assertEqual(sum(isinstance(result, int) for result in results), 1, results)
        self.assertEqual(ProductionTargetDecision.objects.filter(request=request).count(), 1)
        self.assertEqual(request.deviations.count(), 1)

    def test_production_first_revalidates_floor_without_deadlock(self):
        request = self.create_request(50)

        def produce():
            path = type(self.path).objects.get(pk=self.path.pk)
            return register_path_production(
                path,
                date=self.production_date + jdatetime.timedelta(days=1),
                shots=20,
                active_cavities=1,
                scrap_pieces=0,
                actor=get_user_model().objects.get(pk=self.operator.pk),
            ).pk

        def decide():
            return decide_target_revision(
                request.pk,
                outcome=ProductionTargetDecision.Outcome.APPROVED,
                actor=get_user_model().objects.get(pk=self.manager.pk),
            ).decision.pk

        results = self.parallel([produce, decide])
        self.path.refresh_from_db()
        self.assertIn(self.path.current_good_target, (50, 100))
        self.assertEqual(self.path.day_entries.count(), 2)
        if self.path.current_good_target == 100:
            self.assertFalse(ProductionTargetDecision.objects.filter(request=request).exists())
        else:
            self.assertTrue(ProductionTargetDecision.objects.filter(request=request).exists())

    def test_serialized_production_first_rejects_target_below_new_gross(self):
        request = self.create_request(50)
        register_path_production(
            self.path,
            date=self.production_date + jdatetime.timedelta(days=1),
            shots=20,
            active_cavities=1,
            scrap_pieces=0,
            actor=self.operator,
        )
        with self.assertRaises(ValidationError):
            decide_target_revision(
                request,
                outcome=ProductionTargetDecision.Outcome.APPROVED,
                actor=self.manager,
            )
        self.path.refresh_from_db()
        self.assertEqual(self.path.current_good_target, 100)

    def test_serialized_approval_first_allows_later_overproduction(self):
        request = self.create_request(50)
        decide_target_revision(
            request,
            outcome=ProductionTargetDecision.Outcome.APPROVED,
            actor=self.manager,
        )
        register_path_production(
            self.path,
            date=self.production_date + jdatetime.timedelta(days=1),
            shots=20,
            active_cavities=1,
            scrap_pieces=0,
            actor=self.operator,
        )
        self.path.refresh_from_db()
        self.assertEqual(self.path.current_good_target, 50)
        self.assertEqual(sum(self.path.day_entries.values_list("gross_pieces", flat=True)), 60)

    def test_database_one_to_one_rejects_duplicate_decision(self):
        request = self.create_request(60)
        result = decide_target_revision(
            request,
            outcome=ProductionTargetDecision.Outcome.APPROVED,
            actor=self.manager,
        )
        duplicate = ProductionTargetDecision(
            request=request,
            outcome=ProductionTargetDecision.Outcome.APPROVED,
            decided_target=60,
            signed_delta=-40,
            cumulative_gross_at_decision=40,
            decided_by=self.manager,
        )
        duplicate._target_revision_service_create = True
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save_base(force_insert=True)
        self.assertEqual(result.request.decision.pk, result.decision.pk)
