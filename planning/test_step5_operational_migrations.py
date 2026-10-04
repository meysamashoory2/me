from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class OperationalApprovalMigrationTests(TransactionTestCase):
    migrate_from = [("planning", "0017_active_mold_reservation_exclusion")]
    migrate_to = [("planning", "0018_pathoperationalapproval")]

    def test_additive_path_evidence_and_nullable_revision_state(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        Approval = apps.get_model("planning", "PathOperationalApproval")
        Reservation = apps.get_model("planning", "PhysicalMoldReservation")
        self.assertEqual(Approval.objects.count(), 0)
        self.assertTrue(Reservation._meta.get_field("approval_revision").null)
