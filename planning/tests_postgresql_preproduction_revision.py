import threading
from unittest import skipUnless

import jdatetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import Role, UserProfile
from catalog.models import Machine, MoldMachineCompatibility, MoldOption, PhysicalMold, Product, ProductGroup, ProductSubGroup, ProductionUnit
from production.models import ProductionDayEntry
from production.path_execution import register_path_production

from .final_merge import create_manual_final
from .models import PlannedProductionPath, PlanningRevision, WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine
from .operational_approval import approve_path, preview_path_approval
from .preproduction_revision import apply_preproduction_revision, preview_preproduction_revision
from .program_governance import approve_manual_source, create_program_source, initialize_program_numbering
from .reservation_integrity import PhysicalMoldReservationConflict


POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "PostgreSQL integration test")


@POSTGRESQL_ONLY
class PreProductionRevisionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.manager = get_user_model().objects.create_user(username="pg-b3b-manager")
        profile, _ = UserProfile.objects.get_or_create(user=self.manager)
        profile.role = Role.PLANNING_MANAGER
        profile.save(update_fields=["role"])
        group = ProductGroup.objects.create(name="PG B3B group")
        subgroup = ProductSubGroup.objects.create(name="PG B3B subgroup", group=group)
        unit = ProductionUnit.objects.create(number=983, name="PG B3B unit")
        self.machine = Machine.objects.create(number="PG-B3B", unit=unit)
        products = [
            Product.objects.create(code=f"PG-B3B-P{i}", name=f"PG B3B product {i}", subgroup=subgroup)
            for i in (1, 2)
        ]
        mold_type = MoldOption.objects.create(code="PG-B3B-T", label="PG B3B mold type")
        molds = [PhysicalMold.objects.create(code=f"PG-B3B-M{i}", mold_type=mold_type) for i in (1, 2, 3)]
        for mold in molds:
            MoldMachineCompatibility.objects.create_with_status(physical_mold=mold, machine=self.machine, status="confirmed", changed_by=self.manager, reason="PG B3B fixture")
        self.shared_mold = molds[2]
        initialize_program_numbering(starting_program_number=1901, actor=self.manager)
        manual = create_program_source(origin=WeeklyPlan.PlanningMode.MANUAL, program_date=jdatetime.date(1405, 12, 20), actor=self.manager, requested_program_number=1901)
        for index, (product, mold) in enumerate(zip(products, molds[:2]), start=1):
            item = WeeklyPlanItem.objects.create(plan=manual, subgroup=subgroup, unit=unit, machine=self.machine, product=product, mold=mold_type, mold_change_weekday=0, mold_change_date=manual.date, sequence=index)
            line = WeeklyPlanLine.objects.create(item=item, mold=mold_type, quantity=200, cycle=30)
            PlannedProductionPath.objects.create(requirement=line, machine=self.machine, physical_mold=mold, planned_share_quantity=200, planned_start_date=manual.date, planned_end_date=manual.date + jdatetime.timedelta(days=2), created_by=self.manager, updated_by=self.manager)
        approve_manual_source(manual.pk, actor=self.manager)
        revision, _ = create_manual_final(cycle_id=manual.governance_cycle_id, actor=self.manager)
        self.paths = list(PlannedProductionPath.objects.filter(requirement__item__plan=revision.final_plan).order_by("pk"))
        self.operations = []
        for path in self.paths:
            self.operations.append(approve_path(path.pk, submitted_fingerprint=preview_path_approval(path).fingerprint, actor=self.manager))

    def _proposal(self, path, operation, **overrides):
        reservation = operation.reservation
        execution = operation.execution_path
        values = dict(machine_id=execution.machine_id, physical_mold_id=execution.physical_mold_id, target=execution.current_good_target, start_date=reservation.reserved_start_date, end_date=reservation.reserved_end_date)
        values.update(overrides)
        preview = preview_preproduction_revision(path.pk, **values)
        return preview.fingerprint, values

    def _parallel(self, functions):
        barrier = threading.Barrier(len(functions))
        results = []
        lock = threading.Lock()

        def worker(function):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                value = function()
            except (ValidationError, PhysicalMoldReservationConflict) as error:
                value = f"rejected:{type(error).__name__}"
            except Exception as error:
                value = f"error:{type(error).__name__}:{error}"
            finally:
                close_old_connections()
            with lock:
                results.append(value)

        threads = [threading.Thread(target=worker, args=(function,)) for function in functions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=25)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        return results

    def test_concurrent_same_mold_assignment_commits_one(self):
        proposals = [self._proposal(path, operation, physical_mold_id=self.shared_mold.pk) for path, operation in zip(self.paths, self.operations)]

        def revise(index):
            def run():
                actor = get_user_model().objects.get(pk=self.manager.pk)
                fingerprint, values = proposals[index]
                return apply_preproduction_revision(self.paths[index].pk, submitted_fingerprint=fingerprint, actor=actor, **values).revision.pk
            return run

        results = self._parallel([revise(0), revise(1)])
        self.assertEqual(sum(isinstance(value, int) for value in results), 1, results)
        self.assertEqual(sum(str(value).startswith("rejected:") for value in results), 1, results)

    def test_revision_race_with_first_production_is_serialized(self):
        path, operation = self.paths[0], self.operations[0]
        fingerprint, values = self._proposal(path, operation, target=210)

        def revise():
            actor = get_user_model().objects.get(pk=self.manager.pk)
            return "revision:" + str(apply_preproduction_revision(path.pk, submitted_fingerprint=fingerprint, actor=actor, **values).revision.pk)

        def produce():
            actor = get_user_model().objects.get(pk=self.manager.pk)
            current = type(operation.execution_path).objects.get(pk=operation.execution_path.pk)
            return "production:" + str(register_path_production(current, date=jdatetime.date(1405, 12, 20), shots=1, active_cavities=1, scrap_pieces=0, actor=actor).pk)

        results = self._parallel([revise, produce])
        self.assertEqual(ProductionDayEntry.objects.filter(execution_path=operation.execution_path).count(), 1)
        self.assertEqual(sum(str(value).startswith("revision:") for value in results) + sum(str(value).startswith("rejected:") for value in results), 1, results)
        self.assertFalse(any(str(value).startswith("error:") for value in results), results)

    def test_concurrent_same_path_revision_rejects_stale_request(self):
        path, operation = self.paths[0], self.operations[0]
        first = self._proposal(path, operation, target=210)
        second = self._proposal(path, operation, target=220)

        def revise(proposal):
            def run():
                actor = get_user_model().objects.get(pk=self.manager.pk)
                fingerprint, values = proposal
                return apply_preproduction_revision(path.pk, submitted_fingerprint=fingerprint, actor=actor, **values).revision.pk
            return run

        results = self._parallel([revise(first), revise(second)])
        self.assertEqual(sum(isinstance(value, int) for value in results), 1, results)
        self.assertEqual(sum(str(value).startswith("rejected:") for value in results), 1, results)
        self.assertEqual(PlanningRevision.objects.filter(planned_path=path).count(), 1)
