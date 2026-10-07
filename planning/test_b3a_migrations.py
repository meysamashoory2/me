import jdatetime
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


BEFORE = ("planning", "0022_system_final_merge_foundation")
AFTER = ("planning", "0023_planningrevision_planningdeviation_and_more")


class PlanningB3MigrationTests(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_preserves_data_and_fabricates_no_evidence(self):
        old = self.migrate_to(BEFORE)
        Plan = old.get_model("planning", "WeeklyPlan")
        row = Plan.objects.create(program_number="B3A-LEGACY", date=jdatetime.date(1405, 12, 10))
        apps = self.migrate_to(AFTER)
        self.assertTrue(apps.get_model("planning", "WeeklyPlan").objects.filter(pk=row.pk).exists())
        self.assertFalse(apps.get_model("planning", "PlanningRevision").objects.exists())
        self.assertFalse(apps.get_model("planning", "PlanningDeviation").objects.exists())

    def test_empty_reverse_succeeds_and_evidence_refuses_reverse(self):
        self.migrate_to(AFTER)
        self.migrate_to(BEFORE)
        apps = self.migrate_to(AFTER)
        User = apps.get_model("auth", "User")
        Cycle = apps.get_model("planning", "WeeklyProgramCycle")
        Plan = apps.get_model("planning", "WeeklyPlan")
        Final = apps.get_model("planning", "FinalProgramRevision")
        Revision = apps.get_model("planning", "PlanningRevision")
        user = User.objects.create(username="b3a-migration-planner")
        cycle = Cycle.objects.create(program_number=1101, program_date=jdatetime.date(1405, 12, 11), created_by=user)
        manual = Plan.objects.create(program_number="1101-M", date=cycle.program_date, planning_mode="manual", governance_cycle=cycle, created_by=user)
        final_plan = Plan.objects.create(program_number="1101-F", date=cycle.program_date, planning_mode="final", governance_cycle=cycle, created_by=user)
        final = Final.objects.create(cycle=cycle, revision_number=1, final_plan=final_plan, manual_source=manual, status="effective", source_review_fingerprint="a" * 64, result_fingerprint="b" * 64, approved_by=user)
        Revision.objects.create(cycle=cycle, final_revision=final, revision_type="plan", actor=user)
        with self.assertRaisesMessage(RuntimeError, "Cannot reverse"):
            self.migrate_to(BEFORE)
