import jdatetime
from importlib import import_module
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


PLANNING_0002 = (
    "planning",
    "0002_alter_weeklyplanitem_options_alter_weeklyplan_status_and_more",
)
PLANNING_UID_FIX = (
    "planning",
    "0004_replace_0003_weeklyplanitem_uid",
)
PRODUCTION_0005 = (
    "production",
    "0005_productionprogram_productiondayentry",
)
uid_migration = import_module(
    "planning.migrations.0004_replace_0003_weeklyplanitem_uid"
)


class MigrationTestCase(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        if any(name is None for _, name in targets):
            return None
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()


class WeeklyPlanItemUidMigrationTests(MigrationTestCase):
    def create_legacy_rows(self, apps):
        ProductGroup = apps.get_model("catalog", "ProductGroup")
        ProductSubGroup = apps.get_model("catalog", "ProductSubGroup")
        ProductionTypeOption = apps.get_model("catalog", "ProductionTypeOption")
        ProductionUnit = apps.get_model("catalog", "ProductionUnit")
        Machine = apps.get_model("catalog", "Machine")
        Product = apps.get_model("catalog", "Product")
        WeeklyPlan = apps.get_model("planning", "WeeklyPlan")
        WeeklyPlanItem = apps.get_model("planning", "WeeklyPlanItem")
        WeeklyPlanLine = apps.get_model("planning", "WeeklyPlanLine")

        group = ProductGroup.objects.create(name="Migration group")
        subgroup = ProductSubGroup.objects.create(name="Migration subgroup", group=group)
        unit = ProductionUnit.objects.create(number=901, name="Migration unit")
        machine = Machine.objects.create(number="M-901", unit=unit)
        product = Product.objects.create(
            code="MIGRATION-PRODUCT", name="Migration product", subgroup=subgroup
        )
        production_type = ProductionTypeOption.objects.create(label="Migration type")
        plan = WeeklyPlan.objects.create(
            program_number="MIGRATION-PLAN",
            date=jdatetime.date(1405, 1, 1),
        )
        item_ids = []
        line_ids = []
        for weekday in (0, 1, 2):
            item = WeeklyPlanItem.objects.create(
                plan=plan,
                subgroup=subgroup,
                unit=unit,
                machine=machine,
                product=product,
                mold_change_weekday=weekday,
                mold_change_date=jdatetime.date(1405, 1, weekday + 1),
            )
            line = WeeklyPlanLine.objects.create(
                item=item,
                production_type=production_type,
                quantity=weekday + 1,
                cycle=10,
            )
            item_ids.append(item.pk)
            line_ids.append(line.pk)
        return plan.pk, item_ids, line_ids

    def test_populated_0002_upgrade_preserves_rows_relationships_and_constraints(self):
        old_apps = self.migrate_to([PLANNING_0002])
        plan_id, item_ids, line_ids = self.create_legacy_rows(old_apps)

        new_apps = self.migrate_to([PLANNING_UID_FIX])
        WeeklyPlan = new_apps.get_model("planning", "WeeklyPlan")
        WeeklyPlanItem = new_apps.get_model("planning", "WeeklyPlanItem")
        WeeklyPlanLine = new_apps.get_model("planning", "WeeklyPlanLine")

        items = list(WeeklyPlanItem.objects.order_by("pk"))
        self.assertEqual([item.pk for item in items], item_ids)
        self.assertTrue(WeeklyPlan.objects.filter(pk=plan_id).exists())
        self.assertEqual(
            list(WeeklyPlanLine.objects.order_by("pk").values_list("pk", flat=True)),
            line_ids,
        )
        self.assertEqual(
            list(WeeklyPlanLine.objects.order_by("pk").values_list("item_id", flat=True)),
            item_ids,
        )
        self.assertTrue(all(item.sequence == 1 for item in items))
        self.assertTrue(all(item.uid for item in items))
        self.assertEqual(len({item.uid for item in items}), len(items))

        constraints = connection.introspection.get_constraints(
            connection.cursor(), WeeklyPlanItem._meta.db_table
        )
        self.assertTrue(
            any(
                constraint["unique"] and constraint["columns"] == ["uid"]
                for constraint in constraints.values()
            )
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            WeeklyPlanItem.objects.filter(pk=item_ids[1]).update(uid=items[0].uid)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WeeklyPlanItem.objects.filter(pk=item_ids[1]).update(uid=None)

    def test_backfill_preserves_existing_valid_uids(self):
        old_apps = self.migrate_to([PLANNING_0002])
        _, item_ids, _ = self.create_legacy_rows(old_apps)
        apps = self.migrate_to([PLANNING_UID_FIX])
        WeeklyPlanItem = apps.get_model("planning", "WeeklyPlanItem")
        expected_uids = {
            item.pk: item.uid for item in WeeklyPlanItem.objects.order_by("pk")
        }

        with connection.schema_editor() as schema_editor:
            uid_migration.backfill_weekly_plan_item_uids(apps, schema_editor)

        self.assertEqual(
            {
                item.pk: item.uid
                for item in WeeklyPlanItem.objects.filter(pk__in=item_ids).order_by("pk")
            },
            expected_uids,
        )

    def test_uid_generation_fails_after_bounded_collision_retries(self):
        with patch.object(
            uid_migration.planning.models,
            "generate_program_uid",
            return_value="EXISTING",
        ) as generate_uid:
            with self.assertRaisesRegex(
                RuntimeError,
                rf"after {uid_migration.MAX_UID_GENERATION_ATTEMPTS} attempts",
            ):
                uid_migration.generate_unique_uid({"EXISTING"})

        self.assertEqual(
            generate_uid.call_count, uid_migration.MAX_UID_GENERATION_ATTEMPTS
        )

    def test_duplicate_existing_uids_abort_backfill(self):
        with self.assertRaisesRegex(
            RuntimeError, "Duplicate existing WeeklyPlanItem UID 'DUPLICATE'"
        ):
            uid_migration.collect_existing_uids(
                [(1, "DUPLICATE"), (2, None), (3, "DUPLICATE")]
            )

    def test_fresh_migration_path(self):
        self.migrate_to([("planning", None)])
        apps = self.migrate_to([PLANNING_UID_FIX])
        WeeklyPlanItem = apps.get_model("planning", "WeeklyPlanItem")

        field = WeeklyPlanItem._meta.get_field("uid")
        self.assertFalse(field.null)
        self.assertTrue(field.unique)
        self.assertEqual(field.max_length, 16)

    def test_downstream_production_dependency_migrates(self):
        old_apps = self.migrate_to([PLANNING_0002])
        _, item_ids, _ = self.create_legacy_rows(old_apps)

        apps = self.migrate_to([PRODUCTION_0005])
        WeeklyPlanItem = apps.get_model("planning", "WeeklyPlanItem")
        ProductionProgram = apps.get_model("production", "ProductionProgram")
        item = WeeklyPlanItem.objects.get(pk=item_ids[0])
        program = ProductionProgram.objects.create(item=item)

        self.assertEqual(program.item_id, item_ids[0])
        self.assertTrue(item.uid)
