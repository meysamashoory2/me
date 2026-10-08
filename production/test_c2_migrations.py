import jdatetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


BEFORE = ("production", "0014_productiontargetrevision_productiondeviation_and_more")
AFTER = ("production", "0015_postproduction_target_revision_workflow")


class ProductionC2MigrationTests(TransactionTestCase):
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

    def make_request(self, apps):
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

        user = User.objects.create(username="c2-migration-user")
        group = Group.objects.create(name="C2 migration group")
        subgroup = Subgroup.objects.create(name="C2 migration subgroup", group=group)
        unit = Unit.objects.create(number=983, name="C2 migration unit")
        machine = Machine.objects.create(unit=unit, number="C2-MIG")
        product = Product.objects.create(code="C2-MIG-P", name="C2 product", subgroup=subgroup)
        mold_type = MoldType.objects.create(code="C2-MIG-T", label="C2 mold")
        mold = Mold.objects.create(code="C2-MIG-M", mold_type=mold_type)
        reason = Reason.objects.create(
            label="C2 reason", code="machine_breakdown", applicability="production"
        )
        plan = Plan.objects.create(
            program_number="C2-MIG-PLAN",
            date=jdatetime.date(1406, 4, 1),
            created_by=user,
        )
        item = Item.objects.create(
            plan=plan,
            subgroup=subgroup,
            unit=unit,
            machine=machine,
            product=product,
            mold=mold_type,
            mold_change_weekday=0,
            mold_change_date=plan.date,
        )
        line = Line.objects.create(item=item, mold=mold_type, quantity=100, cycle=30)
        program = Program.objects.create(requirement=line, created_by=user)
        path = Path.objects.create(
            requirement_program=program,
            machine=machine,
            physical_mold=mold,
            initial_good_target=100,
            current_good_target=100,
        )
        request = Target.objects.create(
            execution_path=path,
            original_target=100,
            previous_effective_target=100,
            requested_target=120,
            delta=20,
            source="manual",
            requester=user,
            reason=reason,
            state="pending",
        )
        return user, request

    def test_forward_preserves_request_and_fabricates_no_decision(self):
        apps = self.migrate_to(BEFORE)
        _user, request = self.make_request(apps)
        request_id = request.pk
        apps = self.migrate_to(AFTER)
        Target = apps.get_model("production", "ProductionTargetRevision")
        Decision = apps.get_model("production", "ProductionTargetDecision")
        preserved = Target.objects.get(pk=request_id)
        self.assertEqual(preserved.requested_target, 120)
        self.assertEqual(preserved.state, "pending")
        self.assertFalse(Decision.objects.exists())
        self.migrate_to(BEFORE)

    def test_reverse_refuses_when_immutable_decision_exists(self):
        apps = self.migrate_to(BEFORE)
        user, request = self.make_request(apps)
        request_id = request.pk
        user_id = user.pk
        apps = self.migrate_to(AFTER)
        Decision = apps.get_model("production", "ProductionTargetDecision")
        Decision.objects.create(
            request_id=request_id,
            outcome="approved",
            decided_target=120,
            signed_delta=20,
            cumulative_gross_at_decision=40,
            decided_by_id=user_id,
        )
        with self.assertRaisesMessage(RuntimeError, "Cannot reverse"):
            self.migrate_to(BEFORE)

    def test_forward_refuses_duplicate_historical_target_change_evidence(self):
        apps = self.migrate_to(BEFORE)
        user, request = self.make_request(apps)
        Deviation = apps.get_model("production", "ProductionDeviation")
        for index in range(2):
            Deviation.objects.create(
                execution_path_id=request.execution_path_id,
                category="target_change",
                reason_id=request.reason_id,
                explained_quantity=20 + index,
                note="historical evidence",
                actor_id=user.pk,
                target_revision_id=request.pk,
            )
        with self.assertRaisesMessage(RuntimeError, "duplicate historical"):
            self.migrate_to(AFTER)
        Deviation.objects.all().delete()

    def test_reverse_refuses_pending_scrap_compensation_request(self):
        apps = self.migrate_to(BEFORE)
        _user, request = self.make_request(apps)
        request_id = request.pk
        apps = self.migrate_to(AFTER)
        Target = apps.get_model("production", "ProductionTargetRevision")
        Target.objects.filter(pk=request_id).update(source="scrap_compensation")
        with self.assertRaisesMessage(RuntimeError, "Scrap Compensation"):
            self.migrate_to(BEFORE)
