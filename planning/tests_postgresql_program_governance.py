import threading
from unittest import skipUnless

import jdatetime

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import Role, UserProfile
from planning.models import WeeklyPlan, WeeklyProgramCycle, WeeklyProgramSequence
from planning.program_governance import create_program_source, initialize_program_numbering


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql", "PostgreSQL integration test"
)


@POSTGRESQL_ONLY
class ProgramNumberConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.manager = get_user_model().objects.create_user(username="pg-governance-manager")
        profile, _ = UserProfile.objects.get_or_create(user=self.manager)
        profile.role = Role.PLANNING_MANAGER
        profile.save(update_fields=["role"])
        initialize_program_numbering(
            starting_program_number=1000, actor=self.manager
        )

    def test_concurrent_allocations_are_serialized_and_contiguous(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []
        lock = threading.Lock()

        def allocate(day):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.manager.pk)
                barrier.wait(timeout=10)
                plan = create_program_source(
                    origin=WeeklyPlan.PlanningMode.SYSTEMIC,
                    program_date=day,
                    actor=actor,
                )
                with lock:
                    results.append(plan.governance_cycle.program_number)
            except Exception as error:  # captured for an assertion in the test thread
                with lock:
                    errors.append(error)
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=allocate, args=(jdatetime.date(1405, 10, 1),)),
            threading.Thread(target=allocate, args=(jdatetime.date(1405, 10, 8),)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [1000, 1001])
        self.assertEqual(
            list(WeeklyProgramCycle.objects.order_by("program_number").values_list(
                "program_number", flat=True
            )),
            [1000, 1001],
        )
        self.assertEqual(WeeklyProgramSequence.objects.get().last_allocated_number, 1001)
