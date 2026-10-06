import threading
from unittest import skipUnless

import jdatetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

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
from planning.models import (
    PhysicalMoldReservation,
    PlanApprovalRevision,
    PlannedProductionPath,
    Weekday,
    WeeklyPlan,
    WeeklyPlanItem,
    WeeklyPlanLine,
)
from planning.postgres_constraints import ACTIVE_MOLD_RESERVATION_CONSTRAINT
from planning.operational_approval import approve_path, preview_path_approval
from planning.reservation_integrity import (
    PhysicalMoldReservationConflict,
    activate_reservation,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql", "PostgreSQL integration test"
)


class PostgresReservationFixture(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="pg-planner")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.role = Role.PLANNING_MANAGER
        profile.save(update_fields=["role"])
        group = ProductGroup.objects.create(name="PG reservation group")
        subgroup = ProductSubGroup.objects.create(name="PG reservation subgroup", group=group)
        unit = ProductionUnit.objects.create(number=980, name="PG reservation unit")
        self.machine = Machine.objects.create(number="PG-R-A", unit=unit)
        product = Product.objects.create(code="PG-R-P", name="PG product", subgroup=subgroup)
        mold_type = MoldOption.objects.create(code="PG-R-MT", label="PG mold type")
        self.mold_a = PhysicalMold.objects.create(code="PG-R-M-A", mold_type=mold_type)
        self.mold_b = PhysicalMold.objects.create(code="PG-R-M-B", mold_type=mold_type)
        plan = WeeklyPlan.objects.create(
            program_number="PG-R-PLAN",
            date=jdatetime.date(1405, 7, 1),
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
            mold_change_date=jdatetime.date(1405, 7, 1),
        )
        self.requirement = WeeklyPlanLine.objects.create(
            item=item, mold=mold_type, quantity=1000, cycle=20
        )
        self.plan = plan

    def create_path(self, *, mold=None, sequence=None):
        return PlannedProductionPath.objects.create(
            requirement=self.requirement,
            machine=self.machine,
            physical_mold=mold or self.mold_a,
            planned_share_quantity=500,
            planned_start_date=jdatetime.date(1405, 7, 1),
            planned_end_date=jdatetime.date(1405, 7, 5),
            sequence=sequence or self.requirement.planned_paths.count() + 1,
            created_by=self.user,
            updated_by=self.user,
        )

    def create_revision(self):
        return PlanApprovalRevision.objects.create(
            plan=self.plan,
            revision_number=self.plan.approval_revisions.count() + 1,
            status=PlanApprovalRevision.Status.PREPARING,
        )

    def reservation_values(self, *, mold=None, status="active", start=None, end=None):
        path = self.create_path(mold=mold)
        return {
            "approval_revision": self.create_revision(),
            "source_path": path,
            "requirement": self.requirement,
            "machine": self.machine,
            "physical_mold": mold or self.mold_a,
            "approved_planned_share_quantity": 500,
            "reserved_start_date": start or jdatetime.date(1405, 7, 1),
            "reserved_end_date": end or jdatetime.date(1405, 7, 5),
            "compatibility_status_snapshot": "unknown",
            "mold_status_snapshot": PhysicalMold.Status.READY,
            "status": status,
            "activated_at": None if status == "preparing" else timezone.now(),
        }


@POSTGRESQL_ONLY
class PostgreSQLReservationConstraintTests(PostgresReservationFixture):
    def test_required_extension_and_constraint_definition_are_present(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'btree_gist'")
            self.assertIsNotNone(cursor.fetchone())
            cursor.execute(
                """
                SELECT c.contype, pg_get_constraintdef(c.oid)
                  FROM pg_constraint c
                  JOIN pg_class t ON t.oid = c.conrelid
                 WHERE t.relname = 'planning_physicalmoldreservation'
                   AND c.conname = %s
                """,
                [ACTIVE_MOLD_RESERVATION_CONSTRAINT],
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "x")
        definition = row[1].lower()
        self.assertIn("exclude using gist", definition)
        self.assertIn("daterange", definition)
        self.assertIn("[]", definition)
        self.assertIn("status", definition)
        self.assertIn("active", definition)

    def test_inclusive_overlap_and_same_day_boundary_are_rejected(self):
        PhysicalMoldReservation.objects.create(**self.reservation_values())
        for start, end in (
            (jdatetime.date(1405, 7, 3), jdatetime.date(1405, 7, 7)),
            (jdatetime.date(1405, 7, 5), jdatetime.date(1405, 7, 5)),
        ):
            with self.subTest(start=start), self.assertRaises(IntegrityError), transaction.atomic():
                PhysicalMoldReservation.objects.create(
                    **self.reservation_values(start=start, end=end)
                )

    def test_next_day_other_mold_and_non_active_statuses_are_allowed(self):
        PhysicalMoldReservation.objects.create(**self.reservation_values())
        PhysicalMoldReservation.objects.create(
            **self.reservation_values(
                start=jdatetime.date(1405, 7, 6), end=jdatetime.date(1405, 7, 6)
            )
        )
        PhysicalMoldReservation.objects.create(
            **self.reservation_values(mold=self.mold_b)
        )
        for status in ("preparing", "superseded", "released", "cancelled"):
            with self.subTest(status=status):
                PhysicalMoldReservation.objects.create(
                    **self.reservation_values(status=status)
                )

    def test_service_translates_conflict_and_preserves_preparing_state(self):
        PhysicalMoldReservation.objects.create(**self.reservation_values())
        preparing = PhysicalMoldReservation.objects.create(
            **self.reservation_values(status="preparing")
        )
        with self.assertRaises(PhysicalMoldReservationConflict):
            activate_reservation(preparing)
        preparing.refresh_from_db()
        self.assertEqual(preparing.status, "preparing")
        self.assertIsNone(preparing.activated_at)


@POSTGRESQL_ONLY
class PostgreSQLReservationConcurrencyTests(PostgresReservationFixture):
    def _run_parallel_activations(self, reservations):
        barrier = threading.Barrier(len(reservations))
        results = []
        lock = threading.Lock()

        def worker(pk):
            close_old_connections()
            try:
                candidate = PhysicalMoldReservation.objects.get(pk=pk)
                barrier.wait(timeout=10)
                activate_reservation(candidate)
                outcome = "active"
            except PhysicalMoldReservationConflict:
                outcome = "conflict"
            except Exception as error:  # surfaced with its type for an actionable failure
                outcome = f"error:{type(error).__name__}:{error}"
            finally:
                close_old_connections()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker, args=(row.pk,)) for row in reservations]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        return results

    def test_overlapping_concurrent_activation_allows_exactly_one(self):
        rows = [
            PhysicalMoldReservation.objects.create(
                **self.reservation_values(status="preparing")
            )
            for _ in range(2)
        ]
        self.assertCountEqual(self._run_parallel_activations(rows), ["active", "conflict"])
        self.assertEqual(
            PhysicalMoldReservation.objects.filter(status="active").count(), 1
        )
        self.assertEqual(
            PhysicalMoldReservation.objects.filter(status="preparing").count(), 1
        )

    def test_non_overlapping_concurrent_activation_allows_both(self):
        rows = [
            PhysicalMoldReservation.objects.create(
                **self.reservation_values(
                    status="preparing",
                    start=jdatetime.date(1405, 7, 1),
                    end=jdatetime.date(1405, 7, 2),
                )
            ),
            PhysicalMoldReservation.objects.create(
                **self.reservation_values(
                    status="preparing",
                    start=jdatetime.date(1405, 7, 3),
                    end=jdatetime.date(1405, 7, 4),
                )
            ),
        ]
        self.assertCountEqual(self._run_parallel_activations(rows), ["active", "active"])


@POSTGRESQL_ONLY
class PostgreSQLPathApprovalConcurrencyTests(PostgresReservationFixture):
    def test_concurrent_double_submission_creates_one_operational_identity(self):
        path = self.create_path()
        preview = preview_path_approval(path)
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                actor = get_user_model().objects.get(pk=self.user.pk)
                result = approve_path(
                    path.pk,
                    submitted_fingerprint=preview.fingerprint,
                    actor=actor,
                    confirmations=["unknown"],
                )
                outcome = (result.execution_path.pk, result.reservation.pk, result.created)
            except Exception as error:
                outcome = f"error:{type(error).__name__}:{error}"
            finally:
                close_old_connections()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(any(isinstance(item, str) for item in results), results)
        self.assertEqual(len({item[:2] for item in results}), 1)
        self.assertEqual(sum(1 for item in results if item[2]), 1)
        self.assertEqual(path.operational_approvals.count(), 1)


@POSTGRESQL_ONLY
class PostgreSQLReservationMigrationTests(PostgresReservationFixture):
    migrate_from = [("planning", "0016_physicalmoldreservation_execution_path")]
    migrate_to = [("planning", "0017_active_mold_reservation_exclusion")]
    restore_to = [("planning", "0022_system_final_merge_foundation")]

    def _executor(self):
        return MigrationExecutor(connection)

    def test_populated_forward_and_reverse_preserve_rows_and_extension(self):
        existing = PhysicalMoldReservation.objects.create(**self.reservation_values())
        executor = self._executor()
        executor.migrate(self.migrate_from)
        executor = self._executor()
        executor.migrate(self.migrate_to)
        self.assertTrue(PhysicalMoldReservation.objects.filter(pk=existing.pk).exists())
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = %s",
                [ACTIVE_MOLD_RESERVATION_CONSTRAINT],
            )
            self.assertIsNotNone(cursor.fetchone())
        self._executor().migrate(self.migrate_from)
        self.assertTrue(PhysicalMoldReservation.objects.filter(pk=existing.pk).exists())
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = %s",
                [ACTIVE_MOLD_RESERVATION_CONSTRAINT],
            )
            self.assertIsNone(cursor.fetchone())
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
            self.assertIsNotNone(cursor.fetchone())
        self._executor().migrate(self.restore_to)

    def test_preflight_blocks_conflicting_populated_migration(self):
        existing = PhysicalMoldReservation.objects.create(**self.reservation_values())
        executor = self._executor()
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldReservation = old_apps.get_model("planning", "PhysicalMoldReservation")
        first = OldReservation.objects.get(pk=existing.pk)
        duplicate_values = {
            field.attname: getattr(first, field.attname)
            for field in first._meta.concrete_fields
            if not field.primary_key
        }
        duplicate = OldReservation.objects.create(**duplicate_values)
        try:
            with self.assertRaisesMessage(RuntimeError, "Overlapping ACTIVE"):
                self._executor().migrate(self.migrate_to)
        finally:
            OldReservation.objects.filter(pk=duplicate.pk).delete()
            self._executor().migrate(self.restore_to)
