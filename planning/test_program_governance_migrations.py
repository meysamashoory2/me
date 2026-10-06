import jdatetime

from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase


BEFORE_GOVERNANCE = ("planning", "0019_reservation_provenance_constraints")
GOVERNANCE_FOUNDATION = ("planning", "0020_weekly_program_governance_foundation")
GOVERNANCE_PROVENANCE = ("planning", "0021_final_program_row_provenance")


class ProgramGovernanceMigrationTests(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_populated_forward_migration_preserves_legacy_rows_without_pairing(self):
        old_apps = self.migrate_to(BEFORE_GOVERNANCE)
        User = old_apps.get_model("auth", "User")
        WeeklyPlan = old_apps.get_model("planning", "WeeklyPlan")
        user = User.objects.create(username="governance-migration-user")
        first = WeeklyPlan.objects.create(
            program_number="LEGACY-MANUAL-901",
            date=jdatetime.date(1405, 9, 1),
            planning_mode="manual",
            created_by=user,
        )
        second = WeeklyPlan.objects.create(
            program_number="LEGACY-SYSTEM-902",
            date=jdatetime.date(1405, 9, 8),
            planning_mode="systemic",
            created_by=user,
        )

        new_apps = self.migrate_to(GOVERNANCE_FOUNDATION)
        NewPlan = new_apps.get_model("planning", "WeeklyPlan")
        rows = list(NewPlan.objects.filter(pk__in=[first.pk, second.pk]).order_by("pk"))
        self.assertEqual([row.pk for row in rows], [first.pk, second.pk])
        self.assertTrue(all(row.governance_cycle_id is None for row in rows))
        self.assertTrue(all(row.source_state == "active" for row in rows))
        self.assertEqual(new_apps.get_model("planning", "WeeklyProgramCycle").objects.count(), 0)
        self.assertEqual(new_apps.get_model("planning", "WeeklyProgramSequence").objects.count(), 0)
        self.assertEqual(new_apps.get_model("planning", "ProgramSourceApproval").objects.count(), 0)

    def test_empty_foundation_reverses_without_touching_legacy_data(self):
        old_apps = self.migrate_to(BEFORE_GOVERNANCE)
        WeeklyPlan = old_apps.get_model("planning", "WeeklyPlan")
        plan = WeeklyPlan.objects.create(
            program_number="LEGACY-REVERSIBLE-903",
            date=jdatetime.date(1405, 9, 15),
        )
        self.migrate_to(GOVERNANCE_FOUNDATION)
        reversed_apps = self.migrate_to(BEFORE_GOVERNANCE)
        preserved = reversed_apps.get_model("planning", "WeeklyPlan").objects.get(pk=plan.pk)
        self.assertEqual(preserved.program_number, "LEGACY-REVERSIBLE-903")

    def test_migration_contains_no_data_rewrite_or_raw_sql(self):
        loader = MigrationLoader(connection)
        for target in (GOVERNANCE_FOUNDATION, GOVERNANCE_PROVENANCE):
            migration = loader.get_migration(*target)
            self.assertFalse(
                any(
                    isinstance(operation, migrations.RunSQL)
                    for operation in migration.operations
                )
            )
        for target in (GOVERNANCE_FOUNDATION, GOVERNANCE_PROVENANCE):
            migration = loader.get_migration(*target)
            self.assertEqual(
                sum(
                    isinstance(operation, migrations.RunPython)
                    for operation in migration.operations
                ),
                1,
            )

    def test_reverse_refuses_to_drop_governance_with_governed_rows(self):
        apps = self.migrate_to(GOVERNANCE_FOUNDATION)
        User = apps.get_model("auth", "User")
        WeeklyPlan = apps.get_model("planning", "WeeklyPlan")
        WeeklyProgramCycle = apps.get_model("planning", "WeeklyProgramCycle")
        user = User.objects.create(username="governance-reverse-user")
        cycle = WeeklyProgramCycle.objects.create(
            program_number=1200,
            program_date=jdatetime.date(1405, 9, 22),
            created_by=user,
        )
        plan = WeeklyPlan.objects.create(
            program_number="1200",
            date=jdatetime.date(1405, 9, 22),
            governance_cycle=cycle,
            created_by=user,
        )
        with self.assertRaisesMessage(RuntimeError, "governed Programs exist"):
            self.migrate_to(BEFORE_GOVERNANCE)
        WeeklyPlan.objects.filter(pk=plan.pk).delete()
        WeeklyProgramCycle.objects.filter(pk=cycle.pk).delete()
        self.migrate_to(BEFORE_GOVERNANCE)
