import jdatetime

from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase


PLANNING_BEFORE_PATH_FOUNDATION = (
    "planning",
    "0013_alter_weeklyplanitem_uid_alter_weeklyplanline_uid",
)
PLANNING_PATH_FOUNDATION = (
    "planning",
    "0014_planapprovalrevision_plannedproductionpath_and_more",
)
CATALOG_MOLD_FOUNDATION = (
    "catalog",
    "0028_physicalmold_moldmachineperformance_and_more",
)


class Step5PathFoundationMigrationTests(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, target):
        targets = target if isinstance(target, list) else [target]
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_fresh_path_foundation_has_empty_new_tables(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("planning", None), CATALOG_MOLD_FOUNDATION])
        apps = self.migrate_to(PLANNING_PATH_FOUNDATION)
        self.assertEqual(apps.get_model("planning", "PlannedProductionPath").objects.count(), 0)
        self.assertEqual(apps.get_model("planning", "PlanApprovalRevision").objects.count(), 0)
        self.assertEqual(apps.get_model("planning", "PhysicalMoldReservation").objects.count(), 0)

    def test_populated_upgrade_preserves_legacy_primary_keys_and_fabricates_nothing(self):
        old_apps = self.migrate_to(
            [PLANNING_BEFORE_PATH_FOUNDATION, CATALOG_MOLD_FOUNDATION]
        )
        ProductGroup = old_apps.get_model("catalog", "ProductGroup")
        ProductSubGroup = old_apps.get_model("catalog", "ProductSubGroup")
        ProductionUnit = old_apps.get_model("catalog", "ProductionUnit")
        Machine = old_apps.get_model("catalog", "Machine")
        Product = old_apps.get_model("catalog", "Product")
        MoldOption = old_apps.get_model("catalog", "MoldOption")
        PhysicalMold = old_apps.get_model("catalog", "PhysicalMold")
        WeeklyPlan = old_apps.get_model("planning", "WeeklyPlan")
        WeeklyPlanItem = old_apps.get_model("planning", "WeeklyPlanItem")
        WeeklyPlanLine = old_apps.get_model("planning", "WeeklyPlanLine")

        group = ProductGroup.objects.create(name="Slice 2A migration group")
        subgroup = ProductSubGroup.objects.create(name="Slice 2A migration subgroup", group=group)
        unit = ProductionUnit.objects.create(number=921, name="Slice 2A migration unit")
        machine = Machine.objects.create(number="S2A-MIG", unit=unit)
        product = Product.objects.create(code="S2A-MIG", name="Slice 2A migration", subgroup=subgroup)
        mold_type = MoldOption.objects.create(
            code="S2A-MIG-MOLD", label="Slice 2A migration mold", copies=3,
            change_time_hours="2.50",
        )
        physical_mold = PhysicalMold.objects.create(
            code="S2A-VERIFIED-PHYSICAL", mold_type=mold_type
        )
        plan = WeeklyPlan.objects.create(
            program_number="S2A-MIG-PLAN", date=jdatetime.date(1405, 3, 1)
        )
        item = WeeklyPlanItem.objects.create(
            plan=plan, subgroup=subgroup, unit=unit, machine=machine, product=product,
            mold=mold_type, mold_change_weekday=0,
            mold_change_date=jdatetime.date(1405, 3, 1),
        )
        line = WeeklyPlanLine.objects.create(
            item=item, mold=mold_type, quantity=40000, cycle=30
        )
        ids = (plan.pk, item.pk, line.pk, physical_mold.pk)

        new_apps = self.migrate_to(PLANNING_PATH_FOUNDATION)
        self.assertTrue(new_apps.get_model("planning", "WeeklyPlan").objects.filter(pk=ids[0]).exists())
        self.assertTrue(new_apps.get_model("planning", "WeeklyPlanItem").objects.filter(pk=ids[1]).exists())
        self.assertTrue(new_apps.get_model("planning", "WeeklyPlanLine").objects.filter(pk=ids[2]).exists())
        self.assertTrue(new_apps.get_model("catalog", "PhysicalMold").objects.filter(pk=ids[3]).exists())
        preserved_mold = new_apps.get_model("catalog", "MoldOption").objects.get(pk=mold_type.pk)
        self.assertEqual(preserved_mold.copies, 3)
        self.assertEqual(str(preserved_mold.change_time_hours), "2.50")
        self.assertEqual(new_apps.get_model("planning", "PlannedProductionPath").objects.count(), 0)
        self.assertEqual(new_apps.get_model("planning", "PlanApprovalRevision").objects.count(), 0)
        self.assertEqual(new_apps.get_model("planning", "PhysicalMoldReservation").objects.count(), 0)

    def test_migration_is_additive_and_contains_no_data_or_sql_operations(self):
        loader = MigrationLoader(connection)
        migration = loader.get_migration(*PLANNING_PATH_FOUNDATION)
        self.assertFalse(
            any(
                isinstance(operation, (migrations.RunPython, migrations.RunSQL))
                for operation in migration.operations
            )
        )
