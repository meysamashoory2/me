import jdatetime

from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase


PLANNING_2A = ("planning", "0014_planapprovalrevision_plannedproductionpath_and_more")
PLANNING_2B1 = (
    "planning",
    "0015_planapprovaldecision_approval_fingerprint_and_more",
)
CATALOG_MOLD_FOUNDATION = (
    "catalog",
    "0028_physicalmold_moldmachineperformance_and_more",
)


class Step5ApprovalFoundationMigrationTests(TransactionTestCase):
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

    def test_fresh_migration_has_no_fabricated_foundation_records(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("planning", None), CATALOG_MOLD_FOUNDATION])
        apps = self.migrate_to(PLANNING_2B1)
        self.assertEqual(apps.get_model("planning", "PlanApprovalDecision").objects.count(), 0)
        self.assertEqual(apps.get_model("planning", "PhysicalMoldReservation").objects.count(), 0)
        self.assertEqual(apps.get_model("catalog", "PhysicalMold").objects.count(), 0)

    def test_populated_0014_upgrade_preserves_all_existing_primary_keys(self):
        apps = self.migrate_to([PLANNING_2A, CATALOG_MOLD_FOUNDATION])
        User = apps.get_model("auth", "User")
        ProductGroup = apps.get_model("catalog", "ProductGroup")
        ProductSubGroup = apps.get_model("catalog", "ProductSubGroup")
        ProductionUnit = apps.get_model("catalog", "ProductionUnit")
        Machine = apps.get_model("catalog", "Machine")
        Product = apps.get_model("catalog", "Product")
        MoldOption = apps.get_model("catalog", "MoldOption")
        PhysicalMold = apps.get_model("catalog", "PhysicalMold")
        WeeklyPlan = apps.get_model("planning", "WeeklyPlan")
        WeeklyPlanItem = apps.get_model("planning", "WeeklyPlanItem")
        WeeklyPlanLine = apps.get_model("planning", "WeeklyPlanLine")
        PlannedProductionPath = apps.get_model("planning", "PlannedProductionPath")
        PlanApprovalRevision = apps.get_model("planning", "PlanApprovalRevision")
        PhysicalMoldReservation = apps.get_model("planning", "PhysicalMoldReservation")

        user = User.objects.create(username="s2b1-migration")
        group = ProductGroup.objects.create(name="S2B1 migration group")
        subgroup = ProductSubGroup.objects.create(name="S2B1 migration subgroup", group=group)
        unit = ProductionUnit.objects.create(number=933, name="S2B1 migration unit")
        machine = Machine.objects.create(number="S2B1-MIG", unit=unit)
        product = Product.objects.create(code="S2B1-MIG", name="S2B1", subgroup=subgroup)
        mold_type = MoldOption.objects.create(code="S2B1-MIG-MOLD", label="S2B1 mold")
        physical_mold = PhysicalMold.objects.create(code="S2B1-PHYSICAL", mold_type=mold_type)
        plan = WeeklyPlan.objects.create(
            program_number="S2B1-MIG-PLAN", date=jdatetime.date(1405, 4, 1), created_by=user
        )
        item = WeeklyPlanItem.objects.create(
            plan=plan,
            subgroup=subgroup,
            unit=unit,
            machine=machine,
            product=product,
            mold=mold_type,
            mold_change_weekday=0,
            mold_change_date=jdatetime.date(1405, 4, 1),
        )
        requirement = WeeklyPlanLine.objects.create(item=item, quantity=10, cycle=2)
        path = PlannedProductionPath.objects.create(
            requirement=requirement,
            machine=machine,
            physical_mold=physical_mold,
            planned_share_quantity=10,
            planned_start_date=jdatetime.date(1405, 4, 1),
            planned_end_date=jdatetime.date(1405, 4, 2),
        )
        revision = PlanApprovalRevision.objects.create(
            plan=plan,
            revision_number=1,
            status="active",
            approved_by=user,
        )
        reservation = PhysicalMoldReservation.objects.create(
            approval_revision=revision,
            source_path=path,
            requirement=requirement,
            machine=machine,
            physical_mold=physical_mold,
            approved_planned_share_quantity=10,
            reserved_start_date=jdatetime.date(1405, 4, 1),
            reserved_end_date=jdatetime.date(1405, 4, 2),
            compatibility_status_snapshot="unknown",
            mold_status_snapshot="ready",
            status="active",
            activated_at="2026-01-01T00:00:00Z",
        )
        ids = (physical_mold.pk, path.pk, revision.pk, reservation.pk)

        new_apps = self.migrate_to(PLANNING_2B1)
        self.assertTrue(new_apps.get_model("catalog", "PhysicalMold").objects.filter(pk=ids[0]).exists())
        self.assertTrue(new_apps.get_model("planning", "PlannedProductionPath").objects.filter(pk=ids[1]).exists())
        self.assertTrue(new_apps.get_model("planning", "PlanApprovalRevision").objects.filter(pk=ids[2]).exists())
        self.assertTrue(new_apps.get_model("planning", "PhysicalMoldReservation").objects.filter(pk=ids[3]).exists())
        self.assertEqual(new_apps.get_model("planning", "PlanApprovalDecision").objects.count(), 0)
        self.assertEqual(new_apps.get_model("planning", "PhysicalMoldReservation").objects.count(), 1)
        self.assertEqual(new_apps.get_model("catalog", "PhysicalMold").objects.count(), 1)

    def test_migration_contains_no_business_data_or_raw_sql_operation(self):
        loader = MigrationLoader(connection)
        migration = loader.get_migration(*PLANNING_2B1)
        self.assertFalse(
            any(
                isinstance(operation, (migrations.RunPython, migrations.RunSQL))
                for operation in migration.operations
            )
        )
