from types import SimpleNamespace
from unittest.mock import Mock

from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase

from .models import PhysicalMoldReservation
from .reservation_integrity import (
    PhysicalMoldReservationConflict,
    activate_reservation,
    find_active_reservation_overlaps,
    is_reservation_exclusion_violation,
    lock_physical_molds,
)
from .tests_path_foundation import PathFoundationTestCase


class ReservationIntegrityPortableTests(PathFoundationTestCase):
    def test_preflight_reports_each_inclusive_active_pair_once(self):
        first = self.create_reservation()
        second = self.create_reservation(
            approval_revision=first.approval_revision,
            reserved_start_date=first.reserved_end_date,
            reserved_end_date=first.reserved_end_date,
        )
        conflicts = find_active_reservation_overlaps()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            {conflicts[0].left_id, conflicts[0].right_id},
            {first.pk, second.pk},
        )

    def test_preflight_ignores_non_active_and_next_day_reservations(self):
        first = self.create_reservation()
        self.create_reservation(
            approval_revision=first.approval_revision,
            status=PhysicalMoldReservation.Status.PREPARING,
            activated_at=None,
            reserved_start_date=first.reserved_start_date,
            reserved_end_date=first.reserved_end_date,
        )
        self.create_reservation(
            approval_revision=first.approval_revision,
            physical_mold=self.mold_b,
            reserved_start_date=first.reserved_start_date,
            reserved_end_date=first.reserved_end_date,
        )
        self.assertEqual(find_active_reservation_overlaps(), [])

    def test_command_is_read_only_and_fails_when_conflicts_exist(self):
        first = self.create_reservation()
        self.create_reservation(
            approval_revision=first.approval_revision,
            reserved_start_date=first.reserved_start_date,
            reserved_end_date=first.reserved_end_date,
        )
        with self.assertRaisesMessage(Exception, "overlapping ACTIVE"):
            call_command("check_mold_reservation_overlaps", verbosity=0)
        self.assertEqual(PhysicalMoldReservation.objects.count(), 2)

    def test_constraint_error_classifier_uses_name_and_sqlstate(self):
        cause = Exception("driver detail intentionally ignored")
        cause.diag = SimpleNamespace(
            constraint_name="exclude_active_mold_reservation_overlap"
        )
        cause.sqlstate = "23P01"
        error = IntegrityError("wrapped")
        error.__cause__ = cause
        self.assertTrue(is_reservation_exclusion_violation(error))
        cause.sqlstate = "23505"
        self.assertFalse(is_reservation_exclusion_violation(error))

    def test_activation_conflict_rolls_back_preparing_reservation(self):
        active = self.create_reservation()
        preparing = self.create_reservation(
            approval_revision=active.approval_revision,
            status=PhysicalMoldReservation.Status.PREPARING,
            activated_at=None,
            reserved_start_date=active.reserved_start_date,
            reserved_end_date=active.reserved_end_date,
        )
        with self.assertRaises(PhysicalMoldReservationConflict):
            activate_reservation(preparing)
        preparing.refresh_from_db()
        active.refresh_from_db()
        self.assertEqual(preparing.status, PhysicalMoldReservation.Status.PREPARING)
        self.assertIsNone(preparing.activated_at)
        self.assertEqual(active.status, PhysicalMoldReservation.Status.ACTIVE)

    def test_lock_helper_sorts_and_deduplicates_ids(self):
        queryset = Mock()
        queryset.filter.return_value.order_by.return_value = []
        manager = Mock()
        manager.using.return_value.select_for_update.return_value = queryset
        with self.settings():
            from catalog.models import PhysicalMold

            original = PhysicalMold.objects
            PhysicalMold.objects = manager
            try:
                lock_physical_molds([self.mold_b.pk, self.mold_a.pk, self.mold_b.pk])
            finally:
                PhysicalMold.objects = original
        queryset.filter.assert_called_once_with(
            pk__in=sorted({self.mold_a.pk, self.mold_b.pk})
        )
        queryset.filter.return_value.order_by.assert_called_once_with("pk")
