from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


BEFORE = ("catalog", "0028_physicalmold_moldmachineperformance_and_more")
AFTER = ("catalog", "0029_deviationreason_applicability_deviationreason_code_and_more")


class CatalogB3MigrationTests(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def remove_b3c1_reason_for_older_migration_test(self):
        executor = MigrationExecutor(connection)
        apps = executor.loader.project_state(
            [("catalog", "0030_add_scrap_compensation_reason")]
        ).apps
        apps.get_model("catalog", "DeviationReason").objects.filter(
            code="scrap_compensation"
        ).delete()

    def test_forward_preserves_unknown_reason_without_classification(self):
        self.remove_b3c1_reason_for_older_migration_test()
        old = self.migrate_to(BEFORE)
        Reason = old.get_model("catalog", "DeviationReason")
        legacy = Reason.objects.create(label="Unknown historical label")
        apps = self.migrate_to(AFTER)
        reason = apps.get_model("catalog", "DeviationReason").objects.get(pk=legacy.pk)
        self.assertIsNone(reason.code)
        self.assertIsNone(reason.applicability)
        self.assertFalse(reason.requires_note)

    def test_empty_reverse_succeeds_and_classification_refuses_reverse(self):
        self.remove_b3c1_reason_for_older_migration_test()
        apps = self.migrate_to(AFTER)
        # B3-C1 adds one canonical classified reason at the Catalog leaf.
        # Remove it only in this disposable migration test to retain coverage
        # of the original 0029 empty-database reverse path.
        apps.get_model("catalog", "DeviationReason").objects.filter(
            code="scrap_compensation"
        ).delete()
        self.migrate_to(BEFORE)
        apps = self.migrate_to(AFTER)
        apps.get_model("catalog", "DeviationReason").objects.create(
            label="Canonical other",
            code="other",
            applicability="both",
            requires_note=True,
        )
        with self.assertRaisesMessage(RuntimeError, "Cannot reverse"):
            self.migrate_to(BEFORE)
