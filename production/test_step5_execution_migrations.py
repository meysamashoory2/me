from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ExecutionFoundationMigrationTests(TransactionTestCase):
    migrate_from = [
        ("planning", "0015_planapprovaldecision_approval_fingerprint_and_more"),
        ("production", "0010_pipe_socket_length_200_2s"),
    ]
    migrate_to = [
        ("planning", "0016_physicalmoldreservation_execution_path"),
        ("production", "0012_path_day_entries"),
    ]

    @property
    def executor(self):
        return MigrationExecutor(connection)

    def setUp(self):
        super().setUp()
        executor = self.executor
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._create_legacy_row(old_apps)
        self.executor.migrate(self.migrate_to)

    def tearDown(self):
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _create_legacy_row(self, apps):
        Unit = apps.get_model("catalog", "ProductionUnit")
        Machine = apps.get_model("catalog", "Machine")
        Group = apps.get_model("catalog", "ProductGroup")
        Subgroup = apps.get_model("catalog", "ProductSubGroup")
        Product = apps.get_model("catalog", "Product")
        Plan = apps.get_model("planning", "WeeklyPlan")
        Item = apps.get_model("planning", "WeeklyPlanItem")
        Program = apps.get_model("production", "ProductionProgram")
        Entry = apps.get_model("production", "ProductionDayEntry")
        unit = Unit.objects.create(number=997, name="Migration unit")
        machine = Machine.objects.create(number="MIG-X", unit=unit)
        group = Group.objects.create(name="Migration group")
        subgroup = Subgroup.objects.create(name="Migration subgroup", group=group)
        product = Product.objects.create(code="MIG-P", name="Migration product", subgroup=subgroup)
        plan = Plan.objects.create(program_number="MIG-PLAN", date="1405-01-01", planning_mode="manual", status="draft", alarms="")
        item = Item.objects.create(plan=plan, subgroup=subgroup, unit=unit, machine=machine, product=product, mold_change_weekday=0, mold_change_date="1405-01-01")
        program = Program.objects.create(item=item)
        entry = Entry.objects.create(program=program, date="1405-01-01", produced_quantity=7)
        self.program_pk = program.pk
        self.entry_pk = entry.pk

    def test_populated_legacy_rows_are_preserved_without_fabrication(self):
        apps = self.executor.loader.project_state(self.migrate_to).apps
        Program = apps.get_model("production", "ProductionProgram")
        Entry = apps.get_model("production", "ProductionDayEntry")
        Path = apps.get_model("production", "ProductionExecutionPath")
        PhysicalMold = apps.get_model("catalog", "PhysicalMold")
        entry = Entry.objects.get(pk=self.entry_pk)
        self.assertEqual(entry.program_id, self.program_pk)
        self.assertIsNone(entry.execution_path_id)
        self.assertEqual(entry.produced_quantity, 7)
        self.assertTrue(Program.objects.filter(pk=self.program_pk).exists())
        self.assertFalse(Path.objects.exists())
        self.assertFalse(PhysicalMold.objects.exists())
