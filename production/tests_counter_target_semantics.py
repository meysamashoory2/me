import jdatetime

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from catalog.models import (
    DeviationReason,
    Machine,
    MoldOption,
    PhysicalMold,
    Product,
    ProductGroup,
    ProductSubGroup,
    ProductionUnit,
)
from planning.models import WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine

from .models import (
    ProductionDayEntry,
    ProductionDeviation,
    ProductionExecutionPath,
    ProductionRequirementProgram,
    ProductionTargetRevision,
)
from .target_semantics import calculate_counter_target_snapshot, snapshot_for_execution_path


class CounterTargetCalculationTests(SimpleTestCase):
    def assert_snapshot(self, values, expected):
        snapshot = calculate_counter_target_snapshot(**values)
        for field, value in expected.items():
            self.assertEqual(getattr(snapshot, field), value)

    def test_completed_counter_with_scrap_proposes_compensation(self):
        self.assert_snapshot(
            dict(required_good=20_000, current_counter_target=20_000, cumulative_total_produced=20_000, cumulative_good=10_000),
            dict(counter_remaining=0, good_shortage=10_000, additional_counter_needed=10_000, potential_proposed_target=30_000),
        )

    def test_partial_counter_accounts_for_existing_remaining_capacity(self):
        self.assert_snapshot(
            dict(required_good=20_000, current_counter_target=20_000, cumulative_total_produced=12_000, cumulative_good=10_000),
            dict(counter_remaining=8_000, good_shortage=10_000, additional_counter_needed=2_000, potential_proposed_target=22_000),
        )

    def test_no_scrap_needs_no_counter_extension(self):
        self.assert_snapshot(
            dict(required_good=20_000, current_counter_target=20_000, cumulative_total_produced=12_000, cumulative_good=12_000),
            dict(counter_remaining=8_000, good_shortage=8_000, additional_counter_needed=0, potential_proposed_target=20_000),
        )

    def test_invalid_aggregates_are_rejected(self):
        with self.assertRaises(ValueError):
            calculate_counter_target_snapshot(required_good=1, current_counter_target=1, cumulative_total_produced=1, cumulative_good=2)
        with self.assertRaises(ValueError):
            calculate_counter_target_snapshot(required_good=-1, current_counter_target=1, cumulative_total_produced=0, cumulative_good=0)

    def test_previous_extension_is_not_double_counted(self):
        self.assert_snapshot(
            dict(required_good=20_000, current_counter_target=30_000, cumulative_total_produced=20_000, cumulative_good=10_000),
            dict(counter_remaining=10_000, good_shortage=10_000, additional_counter_needed=0, potential_proposed_target=30_000),
        )

    def test_overproduction_exposes_floor_and_invalid_candidate(self):
        self.assert_snapshot(
            dict(required_good=20_000, current_counter_target=20_000, cumulative_total_produced=25_000, cumulative_good=20_000),
            dict(
                existing_overproduction=5_000,
                minimum_postproduction_counter_target=25_000,
                potential_proposed_target=20_000,
                proposed_target_is_valid=False,
                scrap_compensation_needed=False,
            ),
        )

    def test_zero_good_with_scrap_requires_compensation(self):
        self.assert_snapshot(
            dict(required_good=100, current_counter_target=100, cumulative_total_produced=40, cumulative_good=0),
            dict(counter_remaining=60, good_shortage=100, additional_counter_needed=40, potential_proposed_target=140),
        )


class CounterTargetPathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="counter-semantics")
        group = ProductGroup.objects.create(name="Counter group")
        subgroup = ProductSubGroup.objects.create(name="Counter subgroup", group=group)
        unit = ProductionUnit.objects.create(number=987, name="Counter unit")
        machine = Machine.objects.create(number="COUNTER-1", unit=unit)
        product = Product.objects.create(code="COUNTER-P", name="Counter product", subgroup=subgroup)
        mold_type = MoldOption.objects.create(code="COUNTER-T", label="Counter mold type")
        mold = PhysicalMold.objects.create(code="COUNTER-M", mold_type=mold_type)
        plan = WeeklyPlan.objects.create(program_number="COUNTER-PLAN", date=jdatetime.date(1406, 1, 1), created_by=cls.user)
        item = WeeklyPlanItem.objects.create(plan=plan, subgroup=subgroup, unit=unit, machine=machine, product=product, mold=mold_type, mold_change_weekday=0, mold_change_date=plan.date)
        requirement = WeeklyPlanLine.objects.create(item=item, mold=mold_type, quantity=20_000, cycle=30)
        program = ProductionRequirementProgram.objects.create(requirement=requirement, created_by=cls.user)
        cls.machine = machine
        cls.mold_type = mold_type
        cls.program = program
        cls.path = ProductionExecutionPath.objects.create(
            requirement_program=program,
            machine=machine,
            physical_mold=mold,
            initial_good_target=20_000,
            current_good_target=20_000,
            status=ProductionExecutionPath.Status.RUNNING,
            first_production_at=timezone.now(),
            created_by=cls.user,
        )

    def add_entry(self, *, date, gross, scrap):
        entry = ProductionDayEntry(
            execution_path=self.path,
            date=date,
            produced_quantity=gross,
            shots=gross,
            active_cavities=1,
            gross_pieces=gross,
            scrap_quantity=scrap,
            good_pieces=gross - scrap,
            created_by=self.user,
        )
        entry._execution_service_create = True
        entry.save(force_insert=True)
        return entry

    def test_path_properties_distinguish_counter_remaining_and_good_shortage(self):
        self.add_entry(date=jdatetime.date(1406, 1, 1), gross=12_000, scrap=2_000)
        self.assertEqual(self.path.cumulative_total_produced, 12_000)
        self.assertEqual(self.path.cumulative_good, 10_000)
        self.assertEqual(self.path.counter_remaining, 8_000)
        self.assertEqual(self.path.good_remaining, 8_000)
        self.assertEqual(self.path.required_good_quantity, 20_000)
        self.assertEqual(self.path.good_shortage, 10_000)
        self.assertEqual(self.path.minimum_postproduction_counter_target, 12_000)

    def test_calculation_is_read_only_and_creates_no_evidence(self):
        self.add_entry(date=jdatetime.date(1406, 1, 1), gross=20_000, scrap=10_000)
        before_target = self.path.current_good_target
        snapshot = snapshot_for_execution_path(self.path)
        self.path.refresh_from_db()
        self.assertEqual(snapshot.potential_proposed_target, 30_000)
        self.assertEqual(self.path.current_good_target, before_target)
        self.assertFalse(ProductionTargetRevision.objects.exists())
        self.assertFalse(ProductionDeviation.objects.exists())

    def test_snapshot_uses_one_aggregate_query_for_multiple_entries(self):
        self.add_entry(date=jdatetime.date(1406, 1, 1), gross=10_000, scrap=2_000)
        self.add_entry(date=jdatetime.date(1406, 1, 2), gross=5_000, scrap=1_000)
        # One approval lookup, one legacy-classification lookup, and one combined aggregate.
        with self.assertNumQueries(3):
            snapshot = snapshot_for_execution_path(self.path)
        self.assertEqual(snapshot.cumulative_total_produced, 15_000)
        self.assertEqual(snapshot.cumulative_good, 12_000)

    def test_legacy_path_is_positively_identified_for_initial_target_fallback(self):
        self.assertFalse(self.path.operational_approvals.exists())
        self.assertEqual(self.path.required_good_quantity, 20_000)

    def test_parallel_paths_keep_counter_aggregates_isolated(self):
        other_mold = PhysicalMold.objects.create(code="COUNTER-M-2", mold_type=self.mold_type)
        other = ProductionExecutionPath.objects.create(
            requirement_program=self.program,
            machine=self.machine,
            physical_mold=other_mold,
            initial_good_target=5_000,
            current_good_target=5_000,
            status=ProductionExecutionPath.Status.RUNNING,
            first_production_at=timezone.now(),
            created_by=self.user,
        )
        self.add_entry(date=jdatetime.date(1406, 1, 1), gross=1_000, scrap=100)
        entry = ProductionDayEntry(
            execution_path=other,
            date=jdatetime.date(1406, 1, 1),
            produced_quantity=500,
            shots=500,
            active_cavities=1,
            gross_pieces=500,
            scrap_quantity=50,
            good_pieces=450,
            created_by=self.user,
        )
        entry._execution_service_create = True
        entry.save(force_insert=True)
        self.assertEqual(snapshot_for_execution_path(self.path).cumulative_total_produced, 1_000)
        self.assertEqual(snapshot_for_execution_path(other).cumulative_total_produced, 500)

    def test_scrap_compensation_is_a_distinct_production_reason(self):
        self.assertIn(DeviationReason.Code.SCRAP_COMPENSATION, DeviationReason.Code.values)
        reason = DeviationReason.objects.get(code=DeviationReason.Code.SCRAP_COMPENSATION)
        self.assertEqual(reason.code, "scrap_compensation")
        self.assertEqual(reason.applicability, DeviationReason.Applicability.PRODUCTION)
