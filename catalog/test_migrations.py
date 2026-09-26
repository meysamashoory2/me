from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase


CATALOG_0025 = ("catalog", "0025_reactivate_planning_transfer")
CATALOG_PIPE_0026 = ("catalog", "0026_pipe_calc_factory_fields")
CATALOG_MOLD_0026 = (
    "catalog",
    "0026_productmold_moldoption_change_time_hours_and_more",
)
CATALOG_MERGE_0027 = ("catalog", "0027_merge_0026_catalog_feature_lines")


class CatalogMigrationTestCase(TransactionTestCase):
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

    def create_pre_0026_rows(self, apps):
        ProductGroup = apps.get_model("catalog", "ProductGroup")
        ProductSubGroup = apps.get_model("catalog", "ProductSubGroup")
        Product = apps.get_model("catalog", "Product")
        MoldOption = apps.get_model("catalog", "MoldOption")
        PipeProductLine = apps.get_model("catalog", "PipeProductLine")
        PipeSizeProfile = apps.get_model("catalog", "PipeSizeProfile")
        PipeLengthCut = apps.get_model("catalog", "PipeLengthCut")

        group = ProductGroup.objects.create(name="Migration group")
        subgroup = ProductSubGroup.objects.create(
            name="Migration subgroup", group=group
        )
        product = Product.objects.create(
            code="MIGRATION-PRODUCT",
            name="Migration product",
            subgroup=subgroup,
        )
        mold = MoldOption.objects.create(label="Legacy mold")
        pipe_line = PipeProductLine.objects.create(
            code="migration-line", name="Migration line"
        )
        profile = PipeSizeProfile.objects.create(line=pipe_line, size_mm=901)
        cut = PipeLengthCut.objects.create(
            size_profile=profile,
            length_code="L-100",
            label="One metre",
            nominal_cm=100,
            cut_length_mm=1070,
        )
        return {
            "product": product.pk,
            "mold": mold.pk,
            "line": pipe_line.pk,
            "profile": profile.pk,
            "cut": cut.pk,
        }

    def assert_final_rows(self, apps, ids):
        Product = apps.get_model("catalog", "Product")
        MoldOption = apps.get_model("catalog", "MoldOption")
        PipeSizeProfile = apps.get_model("catalog", "PipeSizeProfile")
        PipeLengthCut = apps.get_model("catalog", "PipeLengthCut")

        self.assertTrue(Product.objects.filter(pk=ids["product"]).exists())
        mold = MoldOption.objects.get(pk=ids["mold"])
        self.assertEqual(mold.label, "Legacy mold")
        self.assertEqual(mold.code, "")
        self.assertEqual(mold.copies, 1)
        self.assertEqual(mold.change_time_hours, Decimal("2.00"))

        profile = PipeSizeProfile.objects.get(pk=ids["profile"])
        cut = PipeLengthCut.objects.get(pk=ids["cut"])
        self.assertEqual(profile.line_id, ids["line"])
        self.assertEqual(profile.billing_cavities, Decimal("1.0"))
        self.assertEqual(profile.kg_per_meter, Decimal("0"))
        self.assertEqual(cut.size_profile_id, ids["profile"])
        self.assertEqual(cut.cover_cm, Decimal("0"))
        self.assertEqual(cut.pack_qty, 0)
        self.assertEqual(cut.sku_code, "")


class Catalog0026ReconciliationTests(CatalogMigrationTestCase):
    def test_graph_has_one_catalog_leaf(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)

        self.assertEqual(loader.detect_conflicts(), {})
        self.assertEqual(loader.graph.leaf_nodes("catalog"), [CATALOG_MERGE_0027])
        merge_node = loader.graph.node_map[CATALOG_MERGE_0027]
        self.assertEqual(
            {parent.key for parent in merge_node.parents},
            {CATALOG_PIPE_0026, CATALOG_MOLD_0026},
        )

    def test_fresh_database_reaches_both_final_states(self):
        self.migrate_to([("catalog", None)])
        apps = self.migrate_to([CATALOG_MERGE_0027])

        MoldOption = apps.get_model("catalog", "MoldOption")
        ProductMold = apps.get_model("catalog", "ProductMold")
        PipeSizeProfile = apps.get_model("catalog", "PipeSizeProfile")
        PipeLengthCut = apps.get_model("catalog", "PipeLengthCut")
        self.assertEqual(MoldOption._meta.get_field("code").max_length, 40)
        self.assertFalse(MoldOption._meta.get_field("change_time_hours").null)
        self.assertFalse(ProductMold._meta.get_field("product").null)
        self.assertFalse(ProductMold._meta.get_field("mold").null)
        self.assertIsNotNone(PipeSizeProfile._meta.get_field("kg_per_meter"))
        self.assertIsNotNone(PipeLengthCut._meta.get_field("cover_cm"))

    def test_0025_rows_survive_both_feature_migrations(self):
        apps = self.migrate_to([CATALOG_0025])
        ids = self.create_pre_0026_rows(apps)

        apps = self.migrate_to([CATALOG_MERGE_0027])
        self.assert_final_rows(apps, ids)

        ProductMold = apps.get_model("catalog", "ProductMold")
        link = ProductMold.objects.create(
            product_id=ids["product"], mold_id=ids["mold"], slot=2
        )
        self.assertEqual(link.product_id, ids["product"])
        self.assertEqual(link.mold_id, ids["mold"])

    def test_pipe_0026_applied_then_mold_0026(self):
        apps = self.migrate_to([CATALOG_0025])
        ids = self.create_pre_0026_rows(apps)
        apps = self.migrate_to([CATALOG_PIPE_0026])
        PipeSizeProfile = apps.get_model("catalog", "PipeSizeProfile")
        PipeLengthCut = apps.get_model("catalog", "PipeLengthCut")
        PipeSizeProfile.objects.filter(pk=ids["profile"]).update(
            billing_cavities=Decimal("3.0"), kg_per_meter=Decimal("1.25000")
        )
        PipeLengthCut.objects.filter(pk=ids["cut"]).update(
            sku_code="PIPE-901", cover_cm=Decimal("7.50"), pack_qty=12
        )

        apps = self.migrate_to([CATALOG_MERGE_0027])
        profile = apps.get_model("catalog", "PipeSizeProfile").objects.get(
            pk=ids["profile"]
        )
        cut = apps.get_model("catalog", "PipeLengthCut").objects.get(pk=ids["cut"])
        self.assertEqual(profile.billing_cavities, Decimal("3.0"))
        self.assertEqual(profile.kg_per_meter, Decimal("1.25000"))
        self.assertEqual(cut.sku_code, "PIPE-901")
        self.assertEqual(cut.cover_cm, Decimal("7.50"))
        self.assertEqual(cut.pack_qty, 12)
        self.assertTrue(
            apps.get_model("catalog", "MoldOption").objects.filter(
                pk=ids["mold"]
            ).exists()
        )

    def test_mold_0026_applied_then_pipe_0026(self):
        apps = self.migrate_to([CATALOG_0025])
        ids = self.create_pre_0026_rows(apps)
        apps = self.migrate_to([CATALOG_MOLD_0026])
        MoldOption = apps.get_model("catalog", "MoldOption")
        ProductMold = apps.get_model("catalog", "ProductMold")
        MoldOption.objects.filter(pk=ids["mold"]).update(
            code="MOLD-901", copies=3, change_time_hours=Decimal("4.25")
        )
        link = ProductMold.objects.create(
            product_id=ids["product"], mold_id=ids["mold"], slot=1
        )

        apps = self.migrate_to([CATALOG_MERGE_0027])
        mold = apps.get_model("catalog", "MoldOption").objects.get(pk=ids["mold"])
        saved_link = apps.get_model("catalog", "ProductMold").objects.get(pk=link.pk)
        self.assertEqual(mold.code, "MOLD-901")
        self.assertEqual(mold.copies, 3)
        self.assertEqual(mold.change_time_hours, Decimal("4.25"))
        self.assertEqual(saved_link.product_id, ids["product"])
        self.assertEqual(saved_link.mold_id, ids["mold"])
        self.assertTrue(
            apps.get_model("catalog", "PipeLengthCut").objects.filter(
                pk=ids["cut"]
            ).exists()
        )

    def test_both_0026_migrations_applied_then_merge(self):
        apps = self.migrate_to([CATALOG_0025])
        ids = self.create_pre_0026_rows(apps)
        self.migrate_to([CATALOG_PIPE_0026, CATALOG_MOLD_0026])

        apps = self.migrate_to([CATALOG_MERGE_0027])
        self.assert_final_rows(apps, ids)

    def test_final_constraints_and_cascade_relationships(self):
        apps = self.migrate_to([CATALOG_0025])
        ids = self.create_pre_0026_rows(apps)
        apps = self.migrate_to([CATALOG_MERGE_0027])
        MoldOption = apps.get_model("catalog", "MoldOption")
        ProductMold = apps.get_model("catalog", "ProductMold")
        Product = apps.get_model("catalog", "Product")

        mold = MoldOption.objects.get(pk=ids["mold"])
        mold.code = "MOLD-UNIQUE"
        mold.save(update_fields=["code"])
        MoldOption.objects.create(label="Blank code 1")
        MoldOption.objects.create(label="Blank code 2")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MoldOption.objects.create(code="MOLD-UNIQUE", label="Duplicate code")

        ProductMold.objects.create(
            product_id=ids["product"], mold_id=ids["mold"], slot=1
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductMold.objects.create(
                product_id=ids["product"], mold_id=ids["mold"], slot=2
            )
        Product.objects.get(pk=ids["product"]).delete()
        self.assertFalse(ProductMold.objects.filter(mold_id=ids["mold"]).exists())
