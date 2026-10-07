import jdatetime
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


BEFORE = ("production", "0013_operational_edit_event")
AFTER = ("production", "0014_productiontargetrevision_productiondeviation_and_more")


class ProductionB3MigrationTests(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        targets = [("planning", "0023_planningrevision_planningdeviation_and_more"), target]
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_fabricates_no_evidence_and_empty_reverse_succeeds(self):
        apps = self.migrate_to(AFTER)
        self.assertFalse(apps.get_model("production", "ProductionTargetRevision").objects.exists())
        self.assertFalse(apps.get_model("production", "ProductionDeviation").objects.exists())
        self.migrate_to(BEFORE)

    def test_target_revision_evidence_refuses_reverse(self):
        apps = self.migrate_to(AFTER)
        User = apps.get_model("auth", "User")
        Group = apps.get_model("catalog", "ProductGroup")
        Subgroup = apps.get_model("catalog", "ProductSubGroup")
        Unit = apps.get_model("catalog", "ProductionUnit")
        Machine = apps.get_model("catalog", "Machine")
        Product = apps.get_model("catalog", "Product")
        MoldType = apps.get_model("catalog", "MoldOption")
        Mold = apps.get_model("catalog", "PhysicalMold")
        Reason = apps.get_model("catalog", "DeviationReason")
        Plan = apps.get_model("planning", "WeeklyPlan")
        Item = apps.get_model("planning", "WeeklyPlanItem")
        Line = apps.get_model("planning", "WeeklyPlanLine")
        Program = apps.get_model("production", "ProductionRequirementProgram")
        Path = apps.get_model("production", "ProductionExecutionPath")
        Target = apps.get_model("production", "ProductionTargetRevision")
        user = User.objects.create(username="b3a-migration-production")
        group = Group.objects.create(name="B3A migration group")
        subgroup = Subgroup.objects.create(name="B3A migration subgroup", group=group)
        unit = Unit.objects.create(number=985, name="B3A migration unit")
        machine = Machine.objects.create(unit=unit, number="B3A-MIG")
        product = Product.objects.create(code="B3A-MIG-P", name="B3A migration product", subgroup=subgroup)
        mold_type = MoldType.objects.create(code="B3A-MIG-T", label="B3A migration mold")
        mold = Mold.objects.create(code="B3A-MIG-M", mold_type=mold_type)
        reason = Reason.objects.create(label="B3A migration reason", code="machine_breakdown", applicability="production")
        plan = Plan.objects.create(program_number="B3A-MIG-PLAN", date=jdatetime.date(1405, 12, 12), created_by=user)
        item = Item.objects.create(plan=plan, subgroup=subgroup, unit=unit, machine=machine, product=product, mold=mold_type, mold_change_weekday=0, mold_change_date=plan.date)
        line = Line.objects.create(item=item, mold=mold_type, quantity=100, cycle=30)
        program = Program.objects.create(requirement=line, created_by=user)
        path = Path.objects.create(requirement_program=program, machine=machine, physical_mold=mold, initial_good_target=100, current_good_target=100)
        Target.objects.create(execution_path=path, original_target=100, previous_effective_target=100, requested_target=120, delta=20, source="manual", requester=user, reason=reason, state="pending")
        with self.assertRaisesMessage(RuntimeError, "Cannot reverse"):
            self.migrate_to(BEFORE)
