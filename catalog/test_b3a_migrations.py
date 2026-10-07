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

    def test_forward_preserves_unknown_reason_without_classification(self):
        old = self.migrate_to(BEFORE)
        Reason = old.get_model("catalog", "DeviationReason")
        legacy = Reason.objects.create(label="Unknown historical label")
        apps = self.migrate_to(AFTER)
        reason = apps.get_model("catalog", "DeviationReason").objects.get(pk=legacy.pk)
        self.assertIsNone(reason.code)
        self.assertIsNone(reason.applicability)
        self.assertFalse(reason.requires_note)

    def test_empty_reverse_succeeds_and_classification_refuses_reverse(self):
        self.migrate_to(AFTER)
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
