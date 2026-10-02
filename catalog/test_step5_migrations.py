from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase


CATALOG_BEFORE_STEP5 = ("catalog", "0027_merge_0026_catalog_feature_lines")
CATALOG_STEP5 = (
    "catalog",
    "0028_physicalmold_moldmachineperformance_and_more",
)


class Step5MoldFoundationMigrationTests(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_schema_is_additive_and_does_not_fabricate_physical_molds(self):
        old_apps = self.migrate_to(CATALOG_BEFORE_STEP5)
        MoldOption = old_apps.get_model("catalog", "MoldOption")
        legacy_mold = MoldOption.objects.create(
            code="LEGACY-STEP5",
            label="Verified legacy mold type",
            copies=3,
            change_time_hours="2.75",
        )

        new_apps = self.migrate_to(CATALOG_STEP5)
        MigratedMoldOption = new_apps.get_model("catalog", "MoldOption")
        PhysicalMold = new_apps.get_model("catalog", "PhysicalMold")
        MoldMachinePerformance = new_apps.get_model("catalog", "MoldMachinePerformance")
        preserved = MigratedMoldOption.objects.get(pk=legacy_mold.pk)

        self.assertEqual(preserved.code, "LEGACY-STEP5")
        self.assertEqual(preserved.copies, 3)
        self.assertEqual(str(preserved.change_time_hours), "2.75")
        self.assertEqual(PhysicalMold.objects.count(), 0)
        self.assertEqual(
            MoldMachinePerformance._meta.get_field("effective_from").get_internal_type(),
            "DateTimeField",
        )

    def test_0028_contains_no_data_migration_operations(self):
        loader = MigrationLoader(connection)
        migration = loader.get_migration(*CATALOG_STEP5)
        self.assertFalse(
            any(
                isinstance(operation, (migrations.RunPython, migrations.RunSQL))
                for operation in migration.operations
            )
        )
