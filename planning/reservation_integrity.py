"""Internal reservation integrity primitives; no public workflow integration."""

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import PhysicalMold

from .models import PhysicalMoldReservation
from .postgres_constraints import ACTIVE_MOLD_RESERVATION_CONSTRAINT


EXCLUSION_VIOLATION_SQLSTATE = "23P01"


class PhysicalMoldReservationConflict(Exception):
    """An operational reservation conflicts with physical-mold occupancy."""


@dataclass(frozen=True)
class ReservationOverlap:
    physical_mold_id: int
    physical_mold_code: str
    left_id: int
    right_id: int
    left_start: object
    left_end: object
    right_start: object
    right_end: object
    left_status: str
    right_status: str
    left_revision_id: int
    right_revision_id: int
    left_execution_path_id: int | None
    right_execution_path_id: int | None


def find_active_reservation_overlaps(*, using="default"):
    active = (
        PhysicalMoldReservation.objects.using(using)
        .filter(status=PhysicalMoldReservation.Status.ACTIVE)
        .select_related("physical_mold")
        .order_by("physical_mold_id", "reserved_start_date", "pk")
    )
    conflicts = []
    for left in active.iterator():
        rights = active.filter(
            physical_mold_id=left.physical_mold_id,
            pk__gt=left.pk,
            reserved_start_date__lte=left.reserved_end_date,
            reserved_end_date__gte=left.reserved_start_date,
        )
        for right in rights.iterator():
            conflicts.append(
                ReservationOverlap(
                    physical_mold_id=left.physical_mold_id,
                    physical_mold_code=left.physical_mold.code,
                    left_id=left.pk,
                    right_id=right.pk,
                    left_start=left.reserved_start_date,
                    left_end=left.reserved_end_date,
                    right_start=right.reserved_start_date,
                    right_end=right.reserved_end_date,
                    left_status=left.status,
                    right_status=right.status,
                    left_revision_id=left.approval_revision_id,
                    right_revision_id=right.approval_revision_id,
                    left_execution_path_id=left.execution_path_id,
                    right_execution_path_id=right.execution_path_id,
                )
            )
    return conflicts


def is_reservation_exclusion_violation(error: IntegrityError) -> bool:
    cause = error.__cause__
    diagnostics = getattr(cause, "diag", None)
    return (
        getattr(diagnostics, "constraint_name", None)
        == ACTIVE_MOLD_RESERVATION_CONSTRAINT
        and getattr(cause, "sqlstate", None) == EXCLUSION_VIOLATION_SQLSTATE
    )


def lock_physical_molds(mold_ids, *, using="default"):
    """Lock mold rows in deterministic primary-key order."""
    ordered_ids = sorted({int(pk) for pk in mold_ids})
    return list(
        PhysicalMold.objects.using(using)
        .select_for_update()
        .filter(pk__in=ordered_ids)
        .order_by("pk")
    )


def activate_reservation(reservation, *, using="default"):
    """Internally activate one prepared reservation with atomic rollback."""
    try:
        with transaction.atomic(using=using):
            current = (
                PhysicalMoldReservation.objects.using(using)
                .select_for_update()
                .get(pk=reservation.pk)
            )
            if current.source_path_id is not None:
                from .program_governance import assert_operational_program_is_governed

                assert_operational_program_is_governed(
                    current.source_path.requirement.item.plan, using=using
                )
            if current.status != PhysicalMoldReservation.Status.PREPARING:
                raise ValueError("Only a PREPARING reservation may be activated.")
            lock_physical_molds([current.physical_mold_id], using=using)
            if (
                PhysicalMoldReservation.objects.using(using)
                .active_overlaps(
                    physical_mold=current.physical_mold,
                    start_date=current.reserved_start_date,
                    end_date=current.reserved_end_date,
                )
                .exclude(pk=current.pk)
                .exists()
            ):
                raise PhysicalMoldReservationConflict(
                    "The physical mold already has an overlapping active reservation."
                )
            activated_at = timezone.now()
            PhysicalMoldReservation.objects.using(using).filter(pk=current.pk).update(
                status=PhysicalMoldReservation.Status.ACTIVE,
                activated_at=activated_at,
            )
    except IntegrityError as error:
        if is_reservation_exclusion_violation(error):
            raise PhysicalMoldReservationConflict(
                "The physical mold already has an overlapping active reservation."
            ) from error
        raise
    reservation.refresh_from_db(using=using)
    return reservation


def transition_active_reservation(reservation, *, status, using="default"):
    """Release/cancel/supersede an active reservation without deleting history."""
    allowed = {
        PhysicalMoldReservation.Status.RELEASED,
        PhysicalMoldReservation.Status.CANCELLED,
        PhysicalMoldReservation.Status.SUPERSEDED,
    }
    if status not in allowed:
        raise ValueError("Unsupported reservation terminal status.")
    with transaction.atomic(using=using):
        current = (
            PhysicalMoldReservation.objects.using(using)
            .select_for_update()
            .get(pk=reservation.pk)
        )
        if current.status != PhysicalMoldReservation.Status.ACTIVE:
            raise ValueError("Only an ACTIVE reservation may transition.")
        now = timezone.now()
        updates = {"status": status, "released_at": now}
        if status == PhysicalMoldReservation.Status.SUPERSEDED:
            updates["superseded_at"] = now
        PhysicalMoldReservation.objects.using(using).filter(pk=current.pk).update(**updates)
    reservation.refresh_from_db(using=using)
    return reservation
