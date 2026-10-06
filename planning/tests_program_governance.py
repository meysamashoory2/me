import jdatetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Role, UserProfile
from catalog.models import Machine, MoldOption, Product, ProductGroup, ProductSubGroup, ProductionUnit
from planning.models import (
    PlanningGovernanceAlarm,
    ProgramSourceApproval,
    SystemProgramRowDecision,
    WeeklyPlan,
    WeeklyPlanItem,
    WeeklyPlanLine,
    WeeklyProgramCycle,
    WeeklyProgramSequence,
)
from planning.program_governance import (
    approve_manual_source,
    assert_operational_program_is_governed,
    create_program_source,
    initialize_program_numbering,
    record_system_row_decision,
)


class WeeklyProgramGovernanceTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(username="governance-manager")
        profile, _ = UserProfile.objects.get_or_create(user=self.manager)
        profile.role = Role.PLANNING_MANAGER
        profile.save(update_fields=["role"])
        self.day1 = jdatetime.date(1405, 8, 1)
        self.day2 = jdatetime.date(1405, 8, 8)

    def initialize(self, number=100):
        return initialize_program_numbering(
            starting_program_number=number, actor=self.manager
        )

    def create_item(self, plan):
        group = ProductGroup.objects.create(name=f"Governance group {plan.pk}")
        subgroup = ProductSubGroup.objects.create(
            name=f"Governance subgroup {plan.pk}", group=group
        )
        unit = ProductionUnit.objects.create(
            number=700 + plan.pk, name=f"Governance unit {plan.pk}"
        )
        machine = Machine.objects.create(number=f"GOV-{plan.pk}", unit=unit)
        product = Product.objects.create(
            code=f"GOV-P-{plan.pk}", name="Governance product", subgroup=subgroup
        )
        mold = MoldOption.objects.create(code=f"GOV-M-{plan.pk}", label="Governance mold")
        return WeeklyPlanItem.objects.create(
            plan=plan,
            subgroup=subgroup,
            unit=unit,
            machine=machine,
            product=product,
            mold=mold,
            mold_change_weekday=0,
            mold_change_date=plan.date,
        )

    def test_manual_first_and_system_second_share_one_cycle_and_number(self):
        self.initialize()
        manual = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=100,
        )
        system = create_program_source(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=100,
        )
        self.assertEqual(manual.governance_cycle_id, system.governance_cycle_id)
        self.assertEqual(manual.program_number, system.program_number)
        self.assertEqual(manual.program_number, "100")
        self.assertEqual(manual.governance_cycle.sources.count(), 2)

    def test_system_first_can_initialize_once_and_manual_joins(self):
        system = create_program_source(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=250,
        )
        manual = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=250,
        )
        self.assertEqual(system.governance_cycle_id, manual.governance_cycle_id)
        self.assertEqual(WeeklyProgramSequence.objects.get().last_allocated_number, 250)
        with self.assertRaises(ValidationError):
            initialize_program_numbering(starting_program_number=900, actor=self.manager)

    def test_duplicate_gap_and_backwards_numbers_are_rejected(self):
        self.initialize(10)
        first = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=10,
        )
        approve_manual_source(first.pk, actor=self.manager)
        for invalid in (9, 10, 12):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                create_program_source(
                    origin=WeeklyPlan.PlanningMode.MANUAL,
                    program_date=self.day2,
                    actor=self.manager,
                    requested_program_number=invalid,
                )
        second = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=self.day2,
            actor=self.manager,
            requested_program_number=11,
        )
        self.assertEqual(second.governance_cycle.program_number, 11)

    def test_unapproved_manual_blocks_next_cycle(self):
        self.initialize(30)
        create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=30,
        )
        with self.assertRaisesMessage(ValidationError, "Manual approval"):
            create_program_source(
                origin=WeeklyPlan.PlanningMode.SYSTEMIC,
                program_date=self.day2,
                actor=self.manager,
                requested_program_number=31,
            )

    def test_unapproved_system_does_not_block_and_is_archived_with_decisions(self):
        self.initialize(40)
        system = create_program_source(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=40,
        )
        item = self.create_item(system)
        decision = record_system_row_decision(
            source_plan_id=system.pk,
            item_id=item.pk,
            decision=SystemProgramRowDecision.Decision.MODIFY,
            actor=self.manager,
            snapshot={"quantity": 120},
        )
        next_system = create_program_source(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            program_date=self.day2,
            actor=self.manager,
            requested_program_number=41,
        )
        system.refresh_from_db()
        self.assertEqual(system.source_state, "history")
        self.assertTrue(WeeklyPlanItem.objects.filter(pk=item.pk).exists())
        self.assertTrue(SystemProgramRowDecision.objects.filter(pk=decision.pk).exists())
        self.assertEqual(next_system.governance_cycle.program_number, 41)

    def test_row_decision_is_staged_evidence_and_does_not_make_plan_final(self):
        system = create_program_source(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=50,
        )
        item = self.create_item(system)
        record_system_row_decision(
            source_plan_id=system.pk,
            item_id=item.pk,
            decision=SystemProgramRowDecision.Decision.APPROVE,
            actor=self.manager,
        )
        system.governance_cycle.refresh_from_db()
        self.assertIsNone(system.governance_cycle.final_plan_id)
        self.assertFalse(ProgramSourceApproval.objects.filter(source_plan=system).exists())

    def test_governed_source_is_not_operational_without_final_and_manual_approval(self):
        self.initialize(60)
        manual = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=60,
        )
        with self.assertRaisesMessage(ValidationError, "Merged/Final"):
            assert_operational_program_is_governed(manual)
        approve_manual_source(manual.pk, actor=self.manager)
        with self.assertRaisesMessage(ValidationError, "Merged/Final"):
            assert_operational_program_is_governed(manual)

    def test_legacy_plan_remains_operationally_eligible_and_unique(self):
        legacy = WeeklyPlan.objects.create(
            program_number="LEGACY-GOV-1", date=self.day1, created_by=self.manager
        )
        assert_operational_program_is_governed(legacy)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WeeklyPlan.objects.create(
                program_number="LEGACY-GOV-1", date=self.day2, created_by=self.manager
            )

    def test_governance_alarm_and_approval_evidence_are_immutable(self):
        self.initialize(70)
        manual = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=70,
        )
        alarm = PlanningGovernanceAlarm.objects.get(source_plan=manual)
        with self.assertRaises(ValueError):
            alarm.delete()
        approval, created = approve_manual_source(manual.pk, actor=self.manager)
        self.assertTrue(created)
        with self.assertRaises(ValueError):
            ProgramSourceApproval.objects.filter(pk=approval.pk).update(
                content_fingerprint="changed"
            )
        with self.assertRaises(ValueError):
            manual.delete()

    def test_database_allows_only_one_source_of_each_origin_per_cycle(self):
        self.initialize(80)
        manual = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=80,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            WeeklyPlan.objects.create(
                governance_cycle=manual.governance_cycle,
                program_number="80",
                date=self.day1,
                planning_mode=WeeklyPlan.PlanningMode.MANUAL,
                created_by=self.manager,
            )

    def test_approved_manual_content_is_immutable_through_normal_orm_paths(self):
        self.initialize(90)
        manual = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=90,
        )
        item = self.create_item(manual)
        line = WeeklyPlanLine.objects.create(item=item, quantity=100, cycle=20)
        approve_manual_source(manual.pk, actor=self.manager)

        manual.alarms = "forged"
        item.sequence = 2
        line.quantity = 200
        guarded_calls = (
            lambda: manual.save(),
            lambda: WeeklyPlan.objects.filter(pk=manual.pk).update(alarms="forged"),
            lambda: item.save(),
            lambda: WeeklyPlanItem.objects.filter(pk=item.pk).update(sequence=2),
            lambda: WeeklyPlanItem.objects.bulk_update([item], ["sequence"]),
            lambda: line.save(),
            lambda: WeeklyPlanLine.objects.filter(pk=line.pk).update(quantity=200),
            lambda: WeeklyPlanLine.objects.bulk_update([line], ["quantity"]),
            lambda: line.delete(),
            lambda: item.delete(),
        )
        for guarded_call in guarded_calls:
            with self.subTest(call=guarded_call), self.assertRaises(ValueError):
                guarded_call()

    def test_first_system_row_decision_freezes_source_content(self):
        system = create_program_source(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=95,
        )
        item = self.create_item(system)
        line = WeeklyPlanLine.objects.create(item=item, quantity=100, cycle=20)
        record_system_row_decision(
            source_plan_id=system.pk,
            item_id=item.pk,
            decision=SystemProgramRowDecision.Decision.APPROVE,
            actor=self.manager,
        )
        line.quantity = 200
        with self.assertRaises(ValueError):
            line.save()
        with self.assertRaises(ValueError):
            WeeklyPlanItem.objects.filter(pk=item.pk).delete()

    def test_active_governed_draft_without_evidence_remains_editable(self):
        system = create_program_source(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            program_date=self.day1,
            actor=self.manager,
            requested_program_number=96,
        )
        item = self.create_item(system)
        item.sequence = 2
        item.save(update_fields=["sequence"])
        self.assertEqual(WeeklyPlanItem.objects.get(pk=item.pk).sequence, 2)
