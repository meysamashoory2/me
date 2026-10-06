import jdatetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


BEFORE = ("planning", "0021_final_program_row_provenance")
AFTER = ("planning", "0022_system_final_merge_foundation")


class FinalMergeMigrationTests(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_preserves_legacy_plan_and_creates_no_final_evidence(self):
        old_apps = self.migrate_to(BEFORE)
        Plan = old_apps.get_model("planning", "WeeklyPlan")
        legacy = Plan.objects.create(program_number="B2-LEGACY", date=jdatetime.date(1405, 10, 1))
        apps = self.migrate_to(AFTER)
        self.assertTrue(apps.get_model("planning", "WeeklyPlan").objects.filter(pk=legacy.pk).exists())
        self.assertEqual(apps.get_model("planning", "FinalProgramRevision").objects.count(), 0)
        self.assertEqual(apps.get_model("planning", "SystemFinalApproval").objects.count(), 0)

    def test_empty_migration_reverses_and_evidence_refuses_reverse(self):
        self.migrate_to(AFTER)
        self.migrate_to(BEFORE)
        apps = self.migrate_to(AFTER)
        User = apps.get_model("auth", "User")
        Plan = apps.get_model("planning", "WeeklyPlan")
        Cycle = apps.get_model("planning", "WeeklyProgramCycle")
        Revision = apps.get_model("planning", "FinalProgramRevision")
        user = User.objects.create(username="b2-migration-user")
        cycle = Cycle.objects.create(program_number=901, program_date=jdatetime.date(1405, 10, 8), created_by=user)
        manual = Plan.objects.create(program_number="901", date=cycle.program_date, planning_mode="manual", governance_cycle=cycle, created_by=user)
        final = Plan.objects.create(program_number="901", date=cycle.program_date, planning_mode="final", governance_cycle=cycle, created_by=user)
        Revision.objects.create(cycle=cycle, revision_number=1, final_plan=final, manual_source=manual, status="effective", source_review_fingerprint="a" * 64, result_fingerprint="b" * 64, approved_by=user)
        with self.assertRaisesMessage(RuntimeError, "Cannot reverse"):
            self.migrate_to(BEFORE)
