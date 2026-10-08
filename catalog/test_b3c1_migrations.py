from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


BEFORE = ("catalog", "0029_deviationreason_applicability_deviationreason_code_and_more")
AFTER = ("catalog", "0030_add_scrap_compensation_reason")


class CatalogB3C1MigrationTests(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def remove_canonical_reason_for_forward_setup(self):
        executor = MigrationExecutor(connection)
        apps = executor.loader.project_state([AFTER]).apps
        apps.get_model("catalog", "DeviationReason").objects.filter(
            code="scrap_compensation"
        ).delete()

    def test_forward_adds_choice_and_preserves_existing_reasons(self):
        self.remove_canonical_reason_for_forward_setup()
        old = self.migrate_to(BEFORE)
        Reason = old.get_model("catalog", "DeviationReason")
        existing = Reason.objects.create(
            label="Existing reason",
            code="other",
            applicability="both",
            requires_note=True,
        )
        apps = self.migrate_to(AFTER)
        NewReason = apps.get_model("catalog", "DeviationReason")
        choices = dict(NewReason._meta.get_field("code").choices)
        self.assertEqual(choices["scrap_compensation"], "Scrap compensation")
        canonical = NewReason.objects.get(code="scrap_compensation")
        self.assertEqual(canonical.applicability, "production")
        self.assertTrue(NewReason.objects.filter(pk=existing.pk, code="other").exists())

    def test_forward_adopts_unclassified_matching_label(self):
        self.remove_canonical_reason_for_forward_setup()
        old = self.migrate_to(BEFORE)
        Reason = old.get_model("catalog", "DeviationReason")
        existing = Reason.objects.create(label="Scrap compensation")
        new = self.migrate_to(AFTER).get_model("catalog", "DeviationReason").objects.get(
            pk=existing.pk
        )
        self.assertEqual((new.code, new.applicability), ("scrap_compensation", "production"))

    def test_forward_rejects_conflicting_canonical_code(self):
        self.remove_canonical_reason_for_forward_setup()
        old = self.migrate_to(BEFORE)
        Reason = old.get_model("catalog", "DeviationReason")
        collision = Reason.objects.create(
            label="Wrong scope", code="scrap_compensation", applicability="planning"
        )
        with self.assertRaisesRegex(RuntimeError, "not production-applicable"):
            self.migrate_to(AFTER)
        Reason.objects.filter(pk=collision.pk).delete()

    def test_forward_rejects_matching_label_assigned_to_other_code(self):
        self.remove_canonical_reason_for_forward_setup()
        old = self.migrate_to(BEFORE)
        Reason = old.get_model("catalog", "DeviationReason")
        collision = Reason.objects.create(
            label="Scrap compensation", code="other", applicability="both"
        )
        with self.assertRaisesRegex(RuntimeError, "assigned to another canonical code"):
            self.migrate_to(AFTER)
        Reason.objects.filter(pk=collision.pk).delete()

    def test_reverse_refuses_without_deleting_or_reclassifying_reason(self):
        apps = MigrationExecutor(connection).loader.project_state([AFTER]).apps
        Reason = apps.get_model("catalog", "DeviationReason")
        reason, _ = Reason.objects.get_or_create(
            code="scrap_compensation",
            defaults={"label": "Scrap compensation", "applicability": "production"},
        )
        with self.assertRaisesRegex(RuntimeError, "Cannot reverse"):
            self.migrate_to(BEFORE)
        current = self.migrate_to(AFTER)
        CurrentReason = current.get_model("catalog", "DeviationReason")
        preserved = CurrentReason.objects.get(pk=reason.pk)
        self.assertEqual(preserved.code, "scrap_compensation")
        self.assertEqual(preserved.applicability, "production")
