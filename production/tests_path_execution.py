import jdatetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Role, UserProfile
from catalog.models import Machine, MoldOption, PhysicalMold, Product, ProductGroup, ProductSubGroup, ProductionUnit
from planning.models import PlannedProductionPath, Weekday, WeeklyPlan, WeeklyPlanItem, WeeklyPlanLine

from .models import ProductionDayEntry, ProductionExecutionEvent, ProductionExecutionPath
from .path_execution import activate_execution_path, get_or_create_unstarted_execution_path, register_path_production


class PathExecutionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="path-operator")
        profile, _ = UserProfile.objects.get_or_create(user=cls.user)
        profile.role = Role.PLANNING_EXPERT
        profile.save(update_fields=["role"])
        cls.viewer = get_user_model().objects.create_user(username="path-viewer")
        viewer_profile, _ = UserProfile.objects.get_or_create(user=cls.viewer)
        viewer_profile.role = Role.VIEWER
        viewer_profile.save(update_fields=["role"])
        cls.group = ProductGroup.objects.create(name="Execution group")
        cls.subgroup = ProductSubGroup.objects.create(name="Execution subgroup", group=cls.group)
        cls.unit = ProductionUnit.objects.create(number=991, name="Execution unit")
        cls.machine_a = Machine.objects.create(number="EX-A", unit=cls.unit)
        cls.machine_b = Machine.objects.create(number="EX-B", unit=cls.unit)
        cls.product = Product.objects.create(code="EX-P", name="Execution product", subgroup=cls.subgroup)
        cls.mold_type = MoldOption.objects.create(code="EX-MT", label="Execution mold")
        cls.mold_a = PhysicalMold.objects.create(code="EX-M-A", mold_type=cls.mold_type)
        cls.mold_b = PhysicalMold.objects.create(code="EX-M-B", mold_type=cls.mold_type)
        cls.plan = WeeklyPlan.objects.create(program_number="EX-PLAN", date=jdatetime.date(1405, 1, 1), created_by=cls.user)
        cls.item = WeeklyPlanItem.objects.create(
            plan=cls.plan, subgroup=cls.subgroup, unit=cls.unit, machine=cls.machine_a,
            product=cls.product, mold=cls.mold_type, mold_change_weekday=Weekday.SHANBE,
            mold_change_date=jdatetime.date(1405, 1, 1),
        )
        cls.requirement = WeeklyPlanLine.objects.create(item=cls.item, mold=cls.mold_type, quantity=20000, cycle=30)

    def planned_path(self, **overrides):
        data = dict(
            requirement=self.requirement, machine=self.machine_a, physical_mold=self.mold_a,
            planned_share_quantity=20000, planned_start_date=jdatetime.date(1405, 1, 1),
            planned_end_date=jdatetime.date(1405, 1, 3), sequence=self.requirement.planned_paths.count() + 1,
        )
        data.update(overrides)
        return PlannedProductionPath.objects.create(**data)

    def active_path(self, **overrides):
        path, _ = get_or_create_unstarted_execution_path(self.planned_path(**overrides), actor=self.user)
        activate_execution_path(path, actor=self.user)
        return path


class RequirementAndIdentityTests(PathExecutionTestCase):
    def test_requirement_program_is_idempotent_and_number_is_stable(self):
        planned = self.planned_path()
        first, created = get_or_create_unstarted_execution_path(planned, actor=self.user)
        again, second_created = get_or_create_unstarted_execution_path(planned, actor=self.user)
        self.assertTrue(created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, again.pk)
        self.assertRegex(first.requirement_program.program_number, r"^PP-\d{5}$")

    def test_machine_or_mold_change_creates_distinct_path_without_new_mold(self):
        first, _ = get_or_create_unstarted_execution_path(self.planned_path(sequence=1), actor=self.user)
        second, _ = get_or_create_unstarted_execution_path(self.planned_path(machine=self.machine_b, sequence=2), actor=self.user)
        third, _ = get_or_create_unstarted_execution_path(self.planned_path(physical_mold=self.mold_b, sequence=3), actor=self.user)
        self.assertEqual(len({first.pk, second.pk, third.pk}), 3)
        self.assertEqual(PhysicalMold.objects.count(), 2)

    def test_nonpositive_share_cannot_create_execution_path(self):
        for target in (0,):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                get_or_create_unstarted_execution_path(self.planned_path(planned_share_quantity=target), actor=self.user)
        self.assertFalse(ProductionExecutionPath.objects.exists())

    def test_database_rejects_zero_execution_targets(self):
        program, _ = get_or_create_unstarted_execution_path(self.planned_path(), actor=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductionExecutionPath.objects.bulk_create([ProductionExecutionPath(
                requirement_program=program.requirement_program, machine=self.machine_b,
                physical_mold=self.mold_b, initial_good_target=0, current_good_target=1,
            )])


class RegistrationAndQuantityTests(PathExecutionTestCase):
    def test_first_and_later_records_calculate_quantities_and_events(self):
        path = self.active_path()
        first = register_path_production(
            path, date=jdatetime.date(1405, 1, 1), shots=100, active_cavities=4,
            scrap_pieces=15, actor=self.user,
        )
        self.assertEqual((first.gross_pieces, first.good_pieces), (400, 385))
        path.refresh_from_db()
        self.assertEqual(path.status, ProductionExecutionPath.Status.RUNNING)
        self.assertIsNotNone(path.first_production_at)
        self.assertEqual(path.events.filter(event_type=ProductionExecutionEvent.EventType.FIRST_PRODUCTION).count(), 1)
        register_path_production(
            path, date=jdatetime.date(1405, 1, 2), shots=0, active_cavities=4,
            scrap_pieces=0, actor=self.user,
        )
        self.assertEqual(path.events.filter(event_type=ProductionExecutionEvent.EventType.FIRST_PRODUCTION).count(), 1)
        self.assertEqual(path.events.filter(event_type=ProductionExecutionEvent.EventType.PRODUCTION_ACCEPTED).count(), 2)
        self.assertEqual(path.cumulative_good, 385)
        self.assertEqual(path.good_remaining, 19615)

    def test_invalid_quantities_and_duplicate_day_are_rejected(self):
        path = self.active_path()
        for values in [(-1, 1, 0), (1, 0, 0), (1, 1, -1), (1, 1, 2)]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                register_path_production(path, date=jdatetime.date(1405, 1, 1), shots=values[0], active_cavities=values[1], scrap_pieces=values[2], actor=self.user)
        register_path_production(path, date=jdatetime.date(1405, 1, 1), shots=1, active_cavities=1, scrap_pieces=0, actor=self.user)
        with self.assertRaises(ValidationError):
            register_path_production(path, date=jdatetime.date(1405, 1, 1), shots=1, active_cavities=1, scrap_pieces=0, actor=self.user)

    def test_viewer_and_non_operational_statuses_are_rejected(self):
        path = self.active_path()
        with self.assertRaises(PermissionDenied):
            register_path_production(path, date=jdatetime.date(1405, 1, 1), shots=1, active_cavities=1, scrap_pieces=0, actor=self.viewer)
        for status in [ProductionExecutionPath.Status.PREPARING, ProductionExecutionPath.Status.TEMPORARY_STOP, ProductionExecutionPath.Status.FULL_STOP, ProductionExecutionPath.Status.STATISTICS_COMPLETED, ProductionExecutionPath.Status.SUPERSEDED, ProductionExecutionPath.Status.CANCELLED]:
            ProductionExecutionPath.objects.filter(pk=path.pk)._update_for_execution_service(status=status)
            with self.subTest(status=status), self.assertRaises(ValidationError):
                register_path_production(path, date=jdatetime.date(1405, 1, 2), shots=1, active_cavities=1, scrap_pieces=0, actor=self.user)

    def test_event_failure_rolls_back_entry_first_production_and_status(self):
        path = self.active_path()
        original_event_save = ProductionExecutionEvent.save

        def fail_first_production(event, *args, **kwargs):
            if event.event_type == ProductionExecutionEvent.EventType.FIRST_PRODUCTION:
                raise RuntimeError("injected event failure")
            return original_event_save(event, *args, **kwargs)

        with patch.object(ProductionExecutionEvent, "save", fail_first_production):
            with self.assertRaisesRegex(RuntimeError, "injected event failure"):
                register_path_production(
                    path, date=jdatetime.date(1405, 1, 1), shots=10,
                    active_cavities=2, scrap_pieces=0, actor=self.user,
                )
        path.refresh_from_db()
        self.assertEqual(path.status, ProductionExecutionPath.Status.AWAITING)
        self.assertIsNone(path.first_production_at)
        self.assertFalse(path.day_entries.exists())
        self.assertFalse(path.events.filter(event_type=ProductionExecutionEvent.EventType.FIRST_PRODUCTION).exists())


class ImmutabilityTests(PathExecutionTestCase):
    def setUp(self):
        self.path = self.active_path()
        self.entry = register_path_production(
            self.path, date=jdatetime.date(1405, 1, 1), shots=10, active_cavities=2,
            scrap_pieces=1, actor=self.user,
        )

    def test_started_path_save_update_and_bulk_update_guards(self):
        original = self.path.first_production_at
        for field, value in [("machine", self.machine_b), ("physical_mold", self.mold_b), ("current_good_target", 10), ("initial_good_target", 10), ("first_production_at", None)]:
            candidate = ProductionExecutionPath.objects.get(pk=self.path.pk)
            setattr(candidate, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                candidate.save()
        with self.assertRaises(ValueError):
            ProductionExecutionPath.objects.filter(pk=self.path.pk).update(current_good_target=10)
        candidate = ProductionExecutionPath.objects.get(pk=self.path.pk)
        candidate.first_production_at = original
        with self.assertRaises(ValueError):
            ProductionExecutionPath.objects.bulk_update([candidate], ["first_production_at"])

    def test_event_and_path_entry_are_append_only(self):
        event = self.path.events.first()
        event.metadata = {"forged": True}
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            self.path.events.update(metadata={})
        with self.assertRaises(ValueError):
            self.path.events.all().delete()
        self.entry.description = "changed"
        with self.assertRaises(ValueError):
            self.entry.save()
        with self.assertRaises(ValueError):
            ProductionDayEntry.objects.filter(pk=self.entry.pk).delete()
