import threading
from unittest import skipUnless

import jdatetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import Role, UserProfile
from catalog.models import (
    Machine,
    MoldOption,
    PhysicalMold,
    Product,
    ProductGroup,
    ProductSubGroup,
    ProductionUnit,
)
from planning.models import PlannedProductionPath, Weekday, WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine
from production.models import (
    ProductionDayEntry,
    ProductionExecutionEvent,
    ProductionExecutionPath,
    ProductionRequirementProgram,
)
from production.path_execution import (
    activate_execution_path,
    get_or_create_unstarted_execution_path,
    register_path_production,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql", "PostgreSQL integration test"
)


@POSTGRESQL_ONLY
class PostgreSQLExecutionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="pg-operator")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.role = Role.PLANNING_EXPERT
        profile.save(update_fields=["role"])
        group = ProductGroup.objects.create(name="PG execution group")
        subgroup = ProductSubGroup.objects.create(name="PG execution subgroup", group=group)
        unit = ProductionUnit.objects.create(number=981, name="PG execution unit")
        self.machine = Machine.objects.create(number="PG-E-A", unit=unit)
        product = Product.objects.create(code="PG-E-P", name="PG product", subgroup=subgroup)
        mold_type = MoldOption.objects.create(code="PG-E-MT", label="PG mold type")
        self.mold = PhysicalMold.objects.create(code="PG-E-M-A", mold_type=mold_type)
        plan = WeeklyPlan.objects.create(
            program_number="PG-E-PLAN",
            date=jdatetime.date(1405, 8, 1),
            created_by=self.user,
        )
        item = WeeklyPlanItem.objects.create(
            plan=plan,
            subgroup=subgroup,
            unit=unit,
            machine=self.machine,
            product=product,
            mold=mold_type,
            mold_change_weekday=Weekday.SHANBE,
            mold_change_date=jdatetime.date(1405, 8, 1),
        )
        self.requirement = WeeklyPlanLine.objects.create(
            item=item, mold=mold_type, quantity=1000, cycle=20
        )
        self.planned = PlannedProductionPath.objects.create(
            requirement=self.requirement,
            machine=self.machine,
            physical_mold=self.mold,
            planned_share_quantity=1000,
            planned_start_date=jdatetime.date(1405, 8, 1),
            planned_end_date=jdatetime.date(1405, 8, 5),
            sequence=1,
            created_by=self.user,
            updated_by=self.user,
        )

    def _parallel(self, function, count=2):
        barrier = threading.Barrier(count)
        lock = threading.Lock()
        results = []

        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                outcome = function(index)
            except ValidationError:
                outcome = "validation"
            except Exception as error:
                outcome = f"error:{type(error).__name__}:{error}"
            finally:
                close_old_connections()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        return results

    def test_concurrent_unstarted_path_creation_returns_one_identity(self):
        def create(_index):
            planned = PlannedProductionPath.objects.get(pk=self.planned.pk)
            path, created = get_or_create_unstarted_execution_path(
                planned, actor=get_user_model().objects.get(pk=self.user.pk)
            )
            return (path.pk, created)

        results = self._parallel(create)
        self.assertFalse(any(isinstance(result, str) for result in results), results)
        self.assertEqual(len({result[0] for result in results}), 1)
        self.assertEqual(sum(1 for _pk, created in results if created), 1)
        self.assertEqual(ProductionExecutionPath.objects.count(), 1)
        self.assertEqual(ProductionRequirementProgram.objects.count(), 1)
        self.assertEqual(PhysicalMold.objects.count(), 1)

    def _active_path(self):
        path, _ = get_or_create_unstarted_execution_path(self.planned, actor=self.user)
        activate_execution_path(path, actor=self.user)
        path.refresh_from_db()
        return path

    def test_concurrent_same_day_production_accepts_exactly_one(self):
        path = self._active_path()

        def produce(_index):
            current = ProductionExecutionPath.objects.get(pk=path.pk)
            actor = get_user_model().objects.get(pk=self.user.pk)
            entry = register_path_production(
                current,
                date=jdatetime.date(1405, 8, 1),
                shots=10,
                active_cavities=2,
                scrap_pieces=0,
                actor=actor,
            )
            return entry.pk

        results = self._parallel(produce)
        self.assertEqual(sum(isinstance(result, int) for result in results), 1, results)
        self.assertEqual(results.count("validation"), 1, results)
        self.assertEqual(ProductionDayEntry.objects.filter(execution_path=path).count(), 1)
        self.assertEqual(
            ProductionExecutionEvent.objects.filter(
                execution_path=path,
                event_type=ProductionExecutionEvent.EventType.FIRST_PRODUCTION,
            ).count(),
            1,
        )

    def test_concurrent_different_day_production_accepts_both(self):
        path = self._active_path()

        def produce(index):
            current = ProductionExecutionPath.objects.get(pk=path.pk)
            actor = get_user_model().objects.get(pk=self.user.pk)
            entry = register_path_production(
                current,
                date=jdatetime.date(1405, 8, 1 + index),
                shots=10,
                active_cavities=2,
                scrap_pieces=0,
                actor=actor,
            )
            return entry.pk

        results = self._parallel(produce)
        self.assertEqual(sum(isinstance(result, int) for result in results), 2, results)
        self.assertEqual(ProductionDayEntry.objects.filter(execution_path=path).count(), 2)
        self.assertEqual(
            ProductionExecutionEvent.objects.filter(
                execution_path=path,
                event_type=ProductionExecutionEvent.EventType.PRODUCTION_ACCEPTED,
            ).count(),
            2,
        )
        self.assertEqual(
            ProductionExecutionEvent.objects.filter(
                execution_path=path,
                event_type=ProductionExecutionEvent.EventType.FIRST_PRODUCTION,
            ).count(),
            1,
        )
