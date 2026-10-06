import jdatetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Role, UserProfile
from catalog.models import (
    Machine, MoldMachineCompatibility, MoldOption, PhysicalMold, Product,
    ProductGroup, ProductSubGroup, ProductionUnit,
)
from planning.final_merge import (
    approve_system_final, bulk_review_system_rows, create_manual_final,
    review_system_row, system_review_fingerprint,
)
from planning.models import (
    FinalProgramRevision, FinalProgramRowProvenance, PlannedProductionPath,
    SystemFinalApproval, SystemProgramRowDecision, WeeklyPlan, WeeklyPlanItem,
    WeeklyPlanLine,
)
from planning.program_governance import (
    approve_manual_source, assert_operational_program_is_governed,
    create_program_source, initialize_program_numbering,
)


class FinalMergeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = get_user_model().objects.create_user(username="b2-manager")
        profile, _ = UserProfile.objects.get_or_create(user=cls.manager)
        profile.role = Role.PLANNING_MANAGER
        profile.save(update_fields=["role"])
        cls.ordinary = get_user_model().objects.create_user(username="b2-ordinary")
        cls.group = ProductGroup.objects.create(name="B2 group")
        cls.subgroup = ProductSubGroup.objects.create(name="B2 subgroup", group=cls.group)
        cls.unit = ProductionUnit.objects.create(number=994, name="B2 unit")
        cls.machine = Machine.objects.create(number="B2-M1", unit=cls.unit)
        cls.product_a = Product.objects.create(code="B2-A", name="B2 A", subgroup=cls.subgroup)
        cls.product_b = Product.objects.create(code="B2-B", name="B2 B", subgroup=cls.subgroup)
        cls.mold_type = MoldOption.objects.create(code="B2-T", label="B2 mold")
        cls.mold_a = PhysicalMold.objects.create(code="B2-PM-A", mold_type=cls.mold_type)
        cls.mold_b = PhysicalMold.objects.create(code="B2-PM-B", mold_type=cls.mold_type)
        for mold in (cls.mold_a, cls.mold_b):
            MoldMachineCompatibility.objects.create_with_status(
                physical_mold=mold, machine=cls.machine,
                status=MoldMachineCompatibility.Status.CONFIRMED,
                changed_by=cls.manager, reason="B2 test master data",
            )

    def setUp(self):
        initialize_program_numbering(starting_program_number=801, actor=self.manager)
        self.manual = create_program_source(
            origin=WeeklyPlan.PlanningMode.MANUAL,
            program_date=jdatetime.date(1405, 10, 1), actor=self.manager,
            requested_program_number=801,
        )
        self.system = create_program_source(
            origin=WeeklyPlan.PlanningMode.SYSTEMIC,
            program_date=self.manual.date, actor=self.manager,
            requested_program_number=801,
        )

    def add_item(self, plan, *, product=None, mold=None, quantity=100, sequence=1, start=None, end=None):
        mold = mold or self.mold_a
        item = WeeklyPlanItem.objects.create(
            plan=plan, subgroup=self.subgroup, unit=self.unit, machine=self.machine,
            product=product or self.product_a, mold=self.mold_type,
            mold_change_weekday=0, mold_change_date=plan.date, sequence=sequence,
        )
        line = WeeklyPlanLine.objects.create(item=item, mold=self.mold_type, quantity=quantity, cycle=30)
        PlannedProductionPath.objects.create(
            requirement=line, machine=self.machine, physical_mold=mold,
            planned_share_quantity=quantity, planned_start_date=start or plan.date,
            planned_end_date=end or plan.date, sequence=1,
            created_by=self.manager, updated_by=self.manager,
        )
        return item

    def approve_manual(self):
        approve_manual_source(self.manual.pk, actor=self.manager)

    def decide(self, item, decision=SystemProgramRowDecision.Decision.APPROVE, **kwargs):
        return review_system_row(
            source_plan_id=self.system.pk, item_id=item.pk, decision=decision,
            actor=self.manager, **kwargs,
        )

    def final_approve(self):
        return approve_system_final(
            system_source_id=self.system.pk, actor=self.manager,
            expected_review_fingerprint=system_review_fingerprint(self.system.pk),
        )

    def test_approved_manual_creates_manual_only_final_with_provenance(self):
        source = self.add_item(self.manual)
        self.approve_manual()
        revision, created = create_manual_final(cycle_id=self.manual.governance_cycle_id, actor=self.manager)
        self.assertTrue(created)
        provenance = revision.final_plan.items.get().final_provenance
        self.assertEqual(provenance.manual_source_item, source)
        self.assertEqual(provenance.origin, FinalProgramRowProvenance.Origin.MANUAL)
        self.assertEqual(
            revision.source_review_fingerprint,
            self.manual.source_approval.content_fingerprint,
        )
        assert_operational_program_is_governed(revision.final_plan)
        with self.assertRaises(ValidationError):
            assert_operational_program_is_governed(self.manual)

    def test_row_decisions_are_staged_and_bulk_is_per_row(self):
        first = self.add_item(self.system, product=self.product_a, mold=self.mold_a)
        second = self.add_item(self.system, product=self.product_b, mold=self.mold_b, sequence=2)
        before = self.manual.governance_cycle.final_plan_id
        rows = bulk_review_system_rows(
            source_plan_id=self.system.pk,
            decision=SystemProgramRowDecision.Decision.APPROVE,
            actor=self.manager,
        )
        self.assertEqual({row.item_id for row in rows}, {first.pk, second.pk})
        self.assertEqual(len(rows), 2)
        self.manual.governance_cycle.refresh_from_db()
        self.assertEqual(self.manual.governance_cycle.final_plan_id, before)

    def test_modify_preserves_original_and_manager_values_without_merging(self):
        item = self.add_item(self.system, quantity=100)
        decision = self.decide(
            item, SystemProgramRowDecision.Decision.MODIFY,
            modified={"lines": {str(item.lines.get().pk): {"quantity": 125}}},
            reason="manager correction",
        )
        self.assertEqual(decision.decision_snapshot["original_system_proposal"]["lines"][0]["quantity"], 100)
        self.assertEqual(decision.decision_snapshot["manager_modified_values"]["lines"][str(item.lines.get().pk)]["quantity"], 125)
        self.assertIsNone(self.manual.governance_cycle.final_plan_id)

    def test_system_final_replaces_matching_manual_and_preserves_modified_provenance(self):
        manual_item = self.add_item(self.manual, quantity=100)
        system_item = self.add_item(self.system, quantity=120)
        self.approve_manual()
        create_manual_final(cycle_id=self.manual.governance_cycle_id, actor=self.manager)
        self.decide(
            system_item, SystemProgramRowDecision.Decision.MODIFY,
            modified={"lines": {str(system_item.lines.get().pk): {"quantity": 125}}},
        )
        revision, created = self.final_approve()
        self.assertTrue(created)
        self.assertEqual(revision.revision_number, 2)
        final_item = revision.final_plan.items.get()
        self.assertEqual(final_item.lines.get().quantity, 125)
        self.assertEqual(final_item.final_provenance.manual_source_item, manual_item)
        self.assertEqual(final_item.final_provenance.system_source_item, system_item)
        self.assertEqual(final_item.final_provenance.origin, FinalProgramRowProvenance.Origin.MANUAL_SYSTEM_MODIFIED)
        self.assertEqual(FinalProgramRevision.objects.filter(cycle=revision.cycle, status="effective").count(), 1)

    def test_system_addition_and_rejection(self):
        self.add_item(self.manual, product=self.product_a, mold=self.mold_a)
        added = self.add_item(self.system, product=self.product_b, mold=self.mold_b)
        self.approve_manual()
        self.decide(added)
        revision, _ = self.final_approve()
        self.assertEqual(revision.final_plan.items.count(), 2)
        self.assertEqual(revision.final_plan.items.get(product=self.product_b).final_provenance.origin, "system")

    def test_rejected_system_proposal_is_excluded(self):
        self.add_item(self.manual, product=self.product_a, mold=self.mold_a)
        rejected = self.add_item(self.system, product=self.product_b, mold=self.mold_b)
        self.approve_manual()
        self.decide(rejected, SystemProgramRowDecision.Decision.REJECT)
        revision, _ = self.final_approve()
        self.assertFalse(revision.final_plan.items.filter(product=self.product_b).exists())

    def test_manual_priority_blocks_different_product_claiming_same_mold(self):
        self.add_item(self.manual, product=self.product_a, mold=self.mold_a)
        proposed = self.add_item(self.system, product=self.product_b, mold=self.mold_a)
        self.approve_manual()
        self.decide(proposed)
        with self.assertRaisesMessage(ValidationError, "Manual has priority"):
            self.final_approve()
        self.assertFalse(SystemFinalApproval.objects.exists())

    def test_same_product_with_different_molds_is_allowed(self):
        self.add_item(self.manual, product=self.product_a, mold=self.mold_a)
        proposed = self.add_item(self.system, product=self.product_a, mold=self.mold_b)
        self.approve_manual()
        self.decide(proposed)
        revision, _ = self.final_approve()
        self.assertEqual(revision.final_plan.items.count(), 2)

    def test_unknown_system_compatibility_is_not_automatically_selected(self):
        unknown_mold = PhysicalMold.objects.create(code="B2-PM-UNKNOWN", mold_type=self.mold_type)
        proposed = self.add_item(self.system, product=self.product_b, mold=unknown_mold)
        self.approve_manual()
        self.decide(proposed)
        with self.assertRaisesMessage(ValidationError, "CONFIRMED"):
            self.final_approve()
        self.assertFalse(FinalProgramRevision.objects.exists())

    def test_failed_system_merge_leaves_previous_manual_final_effective(self):
        self.add_item(self.manual, product=self.product_a, mold=self.mold_a)
        conflicting = self.add_item(self.system, product=self.product_b, mold=self.mold_a)
        self.approve_manual()
        previous, _ = create_manual_final(
            cycle_id=self.manual.governance_cycle_id, actor=self.manager
        )
        self.decide(conflicting)
        with self.assertRaises(ValidationError):
            self.final_approve()
        previous.refresh_from_db()
        previous.cycle.refresh_from_db()
        self.assertEqual(previous.status, FinalProgramRevision.Status.EFFECTIVE)
        self.assertEqual(previous.cycle.final_plan_id, previous.final_plan_id)
        self.assertEqual(FinalProgramRevision.objects.count(), 1)

    def test_failure_after_final_rows_are_created_rolls_back_everything(self):
        self.add_item(self.manual, product=self.product_a, mold=self.mold_a)
        proposed = self.add_item(self.system, product=self.product_b, mold=self.mold_b)
        self.approve_manual()
        previous, _ = create_manual_final(
            cycle_id=self.manual.governance_cycle_id, actor=self.manager
        )
        self.decide(proposed)
        with patch.object(SystemFinalApproval, "save", side_effect=RuntimeError("late persistence failure")):
            with self.assertRaisesMessage(RuntimeError, "late persistence failure"):
                self.final_approve()
        previous.refresh_from_db()
        previous.cycle.refresh_from_db()
        self.assertEqual(previous.status, FinalProgramRevision.Status.EFFECTIVE)
        self.assertIsNone(previous.superseded_at)
        self.assertEqual(previous.cycle.final_plan_id, previous.final_plan_id)
        self.assertEqual(FinalProgramRevision.objects.count(), 1)
        self.assertEqual(WeeklyPlan.objects.filter(planning_mode=WeeklyPlan.PlanningMode.FINAL).count(), 1)
        self.assertFalse(SystemFinalApproval.objects.exists())

    def test_stale_and_archived_system_are_blocked_without_partial_merge(self):
        proposed = self.add_item(self.system, product=self.product_b, mold=self.mold_b)
        self.approve_manual()
        self.decide(proposed)
        stale = system_review_fingerprint(self.system.pk)
        self.decide(proposed, reason="second review")
        with self.assertRaisesMessage(ValidationError, "stale"):
            approve_system_final(system_source_id=self.system.pk, actor=self.manager, expected_review_fingerprint=stale)
        self.assertFalse(FinalProgramRevision.objects.exists())
        WeeklyPlan.objects.filter(pk=self.system.pk)._update_for_governance_service(source_state="history")
        with self.assertRaisesMessage(ValidationError, "Archived"):
            self.final_approve()

    def test_duplicate_final_approval_is_idempotent(self):
        proposed = self.add_item(self.system, product=self.product_b, mold=self.mold_b)
        self.approve_manual()
        self.decide(proposed)
        fingerprint = system_review_fingerprint(self.system.pk)
        first, created = approve_system_final(system_source_id=self.system.pk, actor=self.manager, expected_review_fingerprint=fingerprint)
        second, created_again = approve_system_final(system_source_id=self.system.pk, actor=self.manager, expected_review_fingerprint=fingerprint)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)

    def test_manager_permission_is_backend_enforced(self):
        proposed = self.add_item(self.system)
        with self.assertRaises(Exception):
            review_system_row(source_plan_id=self.system.pk, item_id=proposed.pk, decision="approve", actor=self.ordinary)

    def test_final_evidence_and_content_are_immutable(self):
        source = self.add_item(self.manual)
        self.approve_manual()
        revision, _ = create_manual_final(cycle_id=self.manual.governance_cycle_id, actor=self.manager)
        with self.assertRaises(ValueError):
            FinalProgramRevision.objects.filter(pk=revision.pk).update(status="superseded")
        final_item = revision.final_plan.items.get()
        final_item.sequence = 9
        with self.assertRaises(ValueError):
            final_item.save()
        provenance = final_item.final_provenance
        provenance.manual_source_item = source
        with self.assertRaises(ValueError):
            provenance.save()

    def test_approved_source_and_final_paths_are_immutable(self):
        source = self.add_item(self.manual)
        source_path = source.lines.get().planned_paths.get()
        self.approve_manual()
        source_path.planned_share_quantity = 99
        guarded = (
            lambda: source_path.save(),
            lambda: PlannedProductionPath.objects.filter(pk=source_path.pk).update(planned_share_quantity=99),
            lambda: PlannedProductionPath.objects.bulk_update([source_path], ["planned_share_quantity"]),
            lambda: PlannedProductionPath.objects.filter(pk=source_path.pk).delete(),
            lambda: source_path.delete(),
        )
        for operation in guarded:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
        revision, _ = create_manual_final(
            cycle_id=self.manual.governance_cycle_id, actor=self.manager
        )
        final_path = revision.final_plan.items.get().lines.get().planned_paths.get()
        final_path.planned_share_quantity = 98
        with self.assertRaises(ValueError):
            final_path.save()

    def test_cross_cycle_provenance_validation_rejects(self):
        source = self.add_item(self.manual)
        self.approve_manual()
        revision, _ = create_manual_final(cycle_id=self.manual.governance_cycle_id, actor=self.manager)
        other = WeeklyPlan.objects.create(program_number="LEGACY-B2", date=jdatetime.date(1405, 11, 1), created_by=self.manager)
        other_item = self.add_item(other, product=self.product_b, mold=self.mold_b)
        provenance = revision.final_plan.items.get().final_provenance
        provenance.manual_source_item = other_item
        with self.assertRaisesMessage(ValueError, "cross Program cycles"):
            provenance.clean()

    def test_database_prevents_two_effective_revisions(self):
        self.add_item(self.manual)
        self.approve_manual()
        revision, _ = create_manual_final(cycle_id=self.manual.governance_cycle_id, actor=self.manager)
        other_plan = WeeklyPlan(
            governance_cycle=revision.cycle, program_number="801", date=self.manual.date,
            planning_mode=WeeklyPlan.PlanningMode.FINAL, created_by=self.manager,
        )
        other_plan._merge_service_create = True
        other_plan.save()
        duplicate = FinalProgramRevision(
            cycle=revision.cycle, revision_number=2, final_plan=other_plan,
            manual_source=self.manual, status="effective", source_review_fingerprint="a" * 64,
            result_fingerprint="b" * 64, approved_by=self.manager,
        )
        duplicate._merge_service_create = True
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_previous_final_is_timestamped_and_cannot_be_operational(self):
        self.add_item(self.manual, product=self.product_a, mold=self.mold_a)
        proposed = self.add_item(self.system, product=self.product_b, mold=self.mold_b)
        self.approve_manual()
        previous, _ = create_manual_final(
            cycle_id=self.manual.governance_cycle_id, actor=self.manager
        )
        self.decide(proposed)
        current, _ = self.final_approve()
        previous.refresh_from_db()
        self.assertEqual(previous.status, FinalProgramRevision.Status.SUPERSEDED)
        self.assertIsNotNone(previous.superseded_at)
        self.assertEqual(current.supersedes_id, previous.pk)
        type(previous.cycle).objects.filter(pk=previous.cycle_id).update(
            final_plan=previous.final_plan
        )
        with self.assertRaisesMessage(ValidationError, "currently effective"):
            assert_operational_program_is_governed(previous.final_plan)

    def test_cross_cycle_revision_creation_is_rejected_by_canonical_model_guard(self):
        self.add_item(self.manual)
        self.approve_manual()
        revision, _ = create_manual_final(
            cycle_id=self.manual.governance_cycle_id, actor=self.manager
        )
        other_cycle = type(revision.cycle).objects.create(
            program_number=999,
            program_date=jdatetime.date(1405, 12, 1),
            created_by=self.manager,
        )
        invalid = FinalProgramRevision(
            cycle=other_cycle,
            revision_number=1,
            final_plan=revision.final_plan,
            manual_source=self.manual,
            status=FinalProgramRevision.Status.EFFECTIVE,
            source_review_fingerprint="a" * 64,
            result_fingerprint="b" * 64,
            approved_by=self.manager,
        )
        invalid._merge_service_create = True
        with self.assertRaisesMessage(ValueError, "Program cycle"):
            invalid.save()
