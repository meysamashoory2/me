import threading
from unittest import skipUnless

import jdatetime

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import Role, UserProfile
from planning.final_merge import approve_system_final, system_review_fingerprint
from planning.models import FinalProgramRevision, SystemFinalApproval, WeeklyPlan
from planning.program_governance import (
    approve_manual_source, create_program_source, initialize_program_numbering,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql", "PostgreSQL integration test"
)


@POSTGRESQL_ONLY
class FinalMergeConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.manager = get_user_model().objects.create_user(username="pg-b2-manager")
        profile, _ = UserProfile.objects.get_or_create(user=self.manager)
        profile.role = Role.PLANNING_MANAGER
        profile.save(update_fields=["role"])
        initialize_program_numbering(starting_program_number=1801, actor=self.manager)
        self.manual = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=jdatetime.date(1405, 11, 1), actor=self.manager,
            requested_program_number=1801,
        )
        self.system = create_program_source(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            program_date=self.manual.date, actor=self.manager,
            requested_program_number=1801,
        )
        approve_manual_source(self.manual.pk, actor=self.manager)
        self.fingerprint = system_review_fingerprint(self.system.pk)

    def test_two_final_approval_requests_commit_one_result(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        lock = threading.Lock()

        def approve():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.manager.pk)
                barrier.wait(timeout=10)
                revision, created = approve_system_final(
                    system_source_id=self.system.pk,
                    actor=actor,
                    expected_review_fingerprint=self.fingerprint,
                )
                with lock:
                    results.append((revision.pk, created))
            except Exception as error:
                with lock:
                    errors.append(error)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=approve), threading.Thread(target=approve)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual({pk for pk, _ in results}, {results[0][0]})
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(SystemFinalApproval.objects.count(), 1)
        self.assertEqual(FinalProgramRevision.objects.filter(status="effective").count(), 1)

    def test_final_approval_vs_next_cycle_archival_is_serialized(self):
        barrier = threading.Barrier(2)
        approval_results, approval_errors, next_results, next_errors = [], [], [], []
        lock = threading.Lock()

        def approve():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.manager.pk)
                barrier.wait(timeout=10)
                result = approve_system_final(
                    system_source_id=self.system.pk,
                    actor=actor,
                    expected_review_fingerprint=self.fingerprint,
                )
                with lock:
                    approval_results.append(result)
            except Exception as error:
                with lock:
                    approval_errors.append(error)
            finally:
                close_old_connections()

        def create_next():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.manager.pk)
                barrier.wait(timeout=10)
                result = create_program_source(
                    origin=WeeklyPlan.PlanningMode.SYSTEMIC,
                    program_date=jdatetime.date(1405, 11, 8),
                    actor=actor,
                )
                with lock:
                    next_results.append(result.pk)
            except Exception as error:
                with lock:
                    next_errors.append(error)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=approve), threading.Thread(target=create_next)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(next_errors, [])
        self.assertEqual(len(next_results), 1)
        self.assertEqual(len(approval_results) + len(approval_errors), 1)
        self.system.refresh_from_db()
        if approval_results:
            self.assertEqual(self.system.source_state, "active")
            self.assertTrue(SystemFinalApproval.objects.filter(system_source=self.system).exists())
            self.assertEqual(FinalProgramRevision.objects.filter(status="effective").count(), 1)
        else:
            self.assertEqual(self.system.source_state, "history")
            self.assertFalse(SystemFinalApproval.objects.filter(system_source=self.system).exists())
            self.assertEqual(FinalProgramRevision.objects.count(), 0)
