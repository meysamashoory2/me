"""Canonical execution-path lifecycle and closeout workflow."""

from dataclasses import asdict, dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import UserProfile
from catalog.models import DeviationReason
from planning.models import PathOperationalApproval, PhysicalMoldReservation
from planning.reservation_integrity import lock_physical_molds, transition_active_reservation

from .models import (
    ProductionDeviation,
    ProductionExecutionEvent,
    ProductionExecutionPath,
    ProductionTargetRevision,
)
from .path_execution import (
    _actor_snapshot,
    _create_execution_event,
    assert_governed_production_lineage,
)
from .target_semantics import resolve_required_good


@dataclass(frozen=True)
class CloseoutSnapshot:
    cumulative_gross: int
    cumulative_good: int
    cumulative_scrap: int
    current_counter_target: int
    counter_remaining: int
    counter_overproduction: int
    required_good: int
    good_shortage: int
    good_overproduction: int


@dataclass(frozen=True)
class FullStopResult:
    path: ProductionExecutionPath
    event: ProductionExecutionEvent
    reservation: PhysicalMoldReservation
    deviations: tuple[ProductionDeviation, ...]
    snapshot: CloseoutSnapshot


def _identity(value, model, label):
    if isinstance(value, model):
        return value.pk
    if isinstance(value, int):
        return value
    raise ValidationError(f"{label} must identify a {model.__name__}.")


def _profile_permission(actor, capability, message, *, using):
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied(message)
    if getattr(actor, "is_superuser", False):
        return
    profile = UserProfile.objects.using(using).filter(user_id=actor.pk).first()
    if profile is None or not getattr(profile, capability, False):
        raise PermissionDenied(message)


def _require_data_entry(actor, *, using):
    _profile_permission(
        actor,
        "can_enter_data",
        "Production data-entry permission is required.",
        using=using,
    )


def _require_manager(actor, *, using):
    _profile_permission(
        actor,
        "can_approve_plans",
        "Manager approval permission is required.",
        using=using,
    )


def _reason(reason_or_id, note, *, using):
    reason_id = _identity(reason_or_id, DeviationReason, "Deviation reason")
    reason = DeviationReason.objects.using(using).get(pk=reason_id)
    note = (note or "").strip()
    if (
        not reason.is_active
        or not reason.code
        or reason.applicability
        not in {
            DeviationReason.Applicability.PRODUCTION,
            DeviationReason.Applicability.BOTH,
        }
    ):
        raise ValidationError("An active structured production reason is required.")
    if (reason.requires_note or reason.code == DeviationReason.Code.OTHER) and not note:
        raise ValidationError("This production reason requires a note.")
    return reason, note


def _reason_snapshot(reason, note):
    return {
        "reason_id": reason.pk,
        "reason_code": reason.code,
        "reason_label": reason.label,
        "note": note,
    }


def _pending_target_requests(path, *, using, lock=False):
    queryset = ProductionTargetRevision.objects.using(using).filter(
        execution_path_id=path.pk,
        state=ProductionTargetRevision.State.PENDING,
        decision__isnull=True,
    )
    if lock:
        # The reverse one-to-one null check uses an outer join on PostgreSQL.
        # Lock only request rows; nullable joined decision rows cannot be locked.
        queryset = queryset.select_for_update(of=("self",))
    return list(queryset.order_by("pk"))


def _closeout_snapshot(path, *, using):
    totals = path.day_entries.using(using).aggregate(
        gross=Sum("gross_pieces"),
        good=Sum("good_pieces"),
        scrap=Sum("scrap_quantity"),
    )
    gross = int(totals["gross"] or 0)
    good = int(totals["good"] or 0)
    scrap = int(totals["scrap"] or 0)
    required_good = int(resolve_required_good(path, using=using))
    target = int(path.current_good_target)
    return CloseoutSnapshot(
        cumulative_gross=gross,
        cumulative_good=good,
        cumulative_scrap=scrap,
        current_counter_target=target,
        counter_remaining=max(target - gross, 0),
        counter_overproduction=max(gross - target, 0),
        required_good=required_good,
        good_shortage=max(required_good - good, 0),
        good_overproduction=max(good - required_good, 0),
    )


def preview_execution_closeout(path_or_id, *, actor, using="default"):
    """Return an advisory closeout snapshot without locking or writing evidence."""
    _require_manager(actor, using=using)
    path_id = _identity(path_or_id, ProductionExecutionPath, "Execution path")
    path = ProductionExecutionPath.objects.using(using).get(pk=path_id)
    if path.status not in {
        ProductionExecutionPath.Status.RUNNING,
        ProductionExecutionPath.Status.TEMPORARY_STOP,
    }:
        raise ValidationError(
            "Closeout preview requires a RUNNING or TEMPORARY_STOP path."
        )
    if path.first_production_at is None or not path.day_entries.using(using).exists():
        raise ValidationError("Closeout preview requires accepted production.")
    assert_governed_production_lineage(path, using=using)
    if _pending_target_requests(path, using=using):
        raise ValidationError("Closeout is blocked by an unresolved target request.")
    return _closeout_snapshot(path, using=using)


def _owned_active_reservation(path, *, using):
    """Lock and verify the one active reservation owned by this path."""
    lock_physical_molds([path.physical_mold_id], using=using)
    reservations = list(
        PhysicalMoldReservation.objects.using(using)
        .select_for_update()
        .filter(execution_path_id=path.pk, status=PhysicalMoldReservation.Status.ACTIVE)
        .order_by("pk")
    )
    if len(reservations) != 1:
        raise ValidationError(
            "Full Stop requires exactly one active reservation owned by the execution path."
        )
    reservation = reservations[0]
    requirement_id = path.requirement_program.requirement_id
    if (
        reservation.physical_mold_id != path.physical_mold_id
        or reservation.machine_id != path.machine_id
        or reservation.requirement_id != requirement_id
        or reservation.source_path_id is None
    ):
        raise ValidationError("The active reservation does not match execution ownership.")
    approvals = list(
        PathOperationalApproval.objects.using(using)
        .filter(execution_path_id=path.pk)
        .order_by("created_at", "pk")
    )
    if not approvals:
        raise ValidationError("Execution ownership has no operational approval evidence.")
    latest = approvals[-1]
    if (
        latest.reservation_id != reservation.pk
        or latest.planned_path_id != reservation.source_path_id
        or latest.execution_path_id != path.pk
    ):
        raise ValidationError("Active reservation ownership is ambiguous or stale.")
    return reservation


def temporary_stop_execution_path(
    path_or_id, *, actor, reason, note="", using="default"
):
    _require_data_entry(actor, using=using)
    path_id = _identity(path_or_id, ProductionExecutionPath, "Execution path")
    reason, note = _reason(reason, note, using=using)
    with transaction.atomic(using=using):
        path = (
            ProductionExecutionPath.objects.using(using)
            .select_for_update()
            .get(pk=path_id)
        )
        if path.status != ProductionExecutionPath.Status.RUNNING:
            raise ValidationError("Only a RUNNING execution path may be temporarily stopped.")
        if path.first_production_at is None or not path.day_entries.using(using).exists():
            raise ValidationError("Temporary Stop requires accepted production.")
        changed_at = timezone.now()
        ProductionExecutionPath.objects.using(using).filter(
            pk=path.pk
        )._update_for_execution_service(
            status=ProductionExecutionPath.Status.TEMPORARY_STOP,
            status_changed_at=changed_at,
            updated_by=actor,
        )
        event = _create_execution_event(
            using=using,
            execution_path=path,
            event_type=ProductionExecutionEvent.EventType.TEMPORARY_STOP,
            occurred_at=changed_at,
            actor=actor,
            actor_identifier_snapshot=_actor_snapshot(actor),
            old_status=ProductionExecutionPath.Status.RUNNING,
            new_status=ProductionExecutionPath.Status.TEMPORARY_STOP,
            metadata=_reason_snapshot(reason, note),
        )
    path.refresh_from_db(using=using)
    return event


def resume_execution_path(path_or_id, *, actor, note="", using="default"):
    _require_data_entry(actor, using=using)
    path_id = _identity(path_or_id, ProductionExecutionPath, "Execution path")
    with transaction.atomic(using=using):
        path = (
            ProductionExecutionPath.objects.using(using)
            .select_for_update()
            .get(pk=path_id)
        )
        if path.status != ProductionExecutionPath.Status.TEMPORARY_STOP:
            raise ValidationError("Only a TEMPORARY_STOP execution path may resume.")
        changed_at = timezone.now()
        ProductionExecutionPath.objects.using(using).filter(
            pk=path.pk
        )._update_for_execution_service(
            status=ProductionExecutionPath.Status.RUNNING,
            status_changed_at=changed_at,
            updated_by=actor,
        )
        event = _create_execution_event(
            using=using,
            execution_path=path,
            event_type=ProductionExecutionEvent.EventType.RESUME,
            occurred_at=changed_at,
            actor=actor,
            actor_identifier_snapshot=_actor_snapshot(actor),
            old_status=ProductionExecutionPath.Status.TEMPORARY_STOP,
            new_status=ProductionExecutionPath.Status.RUNNING,
            metadata={"note": (note or "").strip()},
        )
    path.refresh_from_db(using=using)
    return event


def full_stop_execution_path(
    path_or_id,
    *,
    actor,
    shortfall_reason=None,
    shortfall_note="",
    overproduction_reason=None,
    overproduction_note="",
    using="default",
    require_governed_lineage=False,
):
    _require_manager(actor, using=using)
    path_id = _identity(path_or_id, ProductionExecutionPath, "Execution path")
    with transaction.atomic(using=using):
        path = (
            ProductionExecutionPath.objects.using(using)
            .select_for_update()
            .select_related("requirement_program__requirement")
            .get(pk=path_id)
        )
        if require_governed_lineage:
            assert_governed_production_lineage(path, using=using)
        if path.status not in {
            ProductionExecutionPath.Status.RUNNING,
            ProductionExecutionPath.Status.TEMPORARY_STOP,
        }:
            raise ValidationError(
                "Only a RUNNING or TEMPORARY_STOP execution path may be fully stopped."
            )
        if path.first_production_at is None or not path.day_entries.using(using).exists():
            raise ValidationError("Full Stop requires accepted production.")
        if _pending_target_requests(path, using=using, lock=True):
            raise ValidationError("Full Stop is blocked by an unresolved target request.")
        snapshot = _closeout_snapshot(path, using=using)
        shortfall = snapshot.counter_remaining > 0 or snapshot.good_shortage > 0
        overproduction = (
            snapshot.counter_overproduction > 0 or snapshot.good_overproduction > 0
        )
        shortfall_data = None
        overproduction_data = None
        if shortfall:
            if shortfall_reason is None:
                raise ValidationError("Unresolved shortfall requires a structured reason.")
            shortfall_data = _reason(
                shortfall_reason, shortfall_note, using=using
            )
        if overproduction:
            if overproduction_reason is None:
                raise ValidationError(
                    "Unresolved overproduction requires a structured reason."
                )
            overproduction_data = _reason(
                overproduction_reason, overproduction_note, using=using
            )
        reservation = _owned_active_reservation(path, using=using)
        snapshot_data = asdict(snapshot)
        deviations = []
        if shortfall_data:
            reason, note = shortfall_data
            deviations.append(
                ProductionDeviation.objects.using(using).create(
                    execution_path=path,
                    category=ProductionDeviation.Category.SHORTFALL,
                    reason=reason,
                    explained_quantity=None,
                    note=note,
                    actor=actor,
                    context={"closeout": snapshot_data, "dimensions": ["counter_remaining", "good_shortage"]},
                )
            )
        if overproduction_data:
            reason, note = overproduction_data
            deviations.append(
                ProductionDeviation.objects.using(using).create(
                    execution_path=path,
                    category=ProductionDeviation.Category.OVERPRODUCTION,
                    reason=reason,
                    explained_quantity=None,
                    note=note,
                    actor=actor,
                    context={"closeout": snapshot_data, "dimensions": ["counter_overproduction", "good_overproduction"]},
                )
            )
        changed_at = timezone.now()
        old_status = path.status
        ProductionExecutionPath.objects.using(using).filter(
            pk=path.pk
        )._update_for_execution_service(
            status=ProductionExecutionPath.Status.FULL_STOP,
            status_changed_at=changed_at,
            updated_by=actor,
        )
        transition_active_reservation(
            reservation,
            status=PhysicalMoldReservation.Status.RELEASED,
            using=using,
        )
        event = _create_execution_event(
            using=using,
            execution_path=path,
            event_type=ProductionExecutionEvent.EventType.FULL_STOP,
            occurred_at=changed_at,
            actor=actor,
            actor_identifier_snapshot=_actor_snapshot(actor),
            old_status=old_status,
            new_status=ProductionExecutionPath.Status.FULL_STOP,
            metadata={
                "reconciliation": snapshot_data,
                "reservation_id": reservation.pk,
                "deviation_ids": [item.pk for item in deviations],
            },
        )
    path.refresh_from_db(using=using)
    reservation.refresh_from_db(using=using)
    return FullStopResult(path, event, reservation, tuple(deviations), snapshot)


def complete_execution_statistics(
    path_or_id,
    *,
    actor,
    note="",
    using="default",
    require_governed_lineage=False,
):
    _require_manager(actor, using=using)
    path_id = _identity(path_or_id, ProductionExecutionPath, "Execution path")
    with transaction.atomic(using=using):
        path = (
            ProductionExecutionPath.objects.using(using)
            .select_for_update()
            .get(pk=path_id)
        )
        if require_governed_lineage:
            assert_governed_production_lineage(
                path,
                using=using,
                reservation_status=PhysicalMoldReservation.Status.RELEASED,
            )
        if path.status != ProductionExecutionPath.Status.FULL_STOP:
            raise ValidationError("Statistics may be completed only after Full Stop.")
        if _pending_target_requests(path, using=using, lock=True):
            raise ValidationError(
                "Statistics completion is blocked by an unresolved target request."
            )
        full_stop_events = list(
            path.events.using(using)
            .filter(event_type=ProductionExecutionEvent.EventType.FULL_STOP)
            .order_by("pk")
        )
        if len(full_stop_events) != 1:
            raise ValidationError("Valid immutable Full Stop evidence is required.")
        full_stop_event = full_stop_events[0]
        metadata = full_stop_event.metadata or {}
        if not isinstance(metadata.get("reconciliation"), dict) or not metadata.get(
            "reservation_id"
        ):
            raise ValidationError("Full Stop reconciliation evidence is incomplete.")
        if PhysicalMoldReservation.objects.using(using).filter(
            execution_path_id=path.pk,
            status=PhysicalMoldReservation.Status.ACTIVE,
        ).exists():
            raise ValidationError("Statistics cannot complete while a mold reservation is active.")
        deviation_ids = metadata.get("deviation_ids") or []
        if ProductionDeviation.objects.using(using).filter(
            pk__in=deviation_ids, execution_path_id=path.pk
        ).count() != len(set(deviation_ids)):
            raise ValidationError("Full Stop deviation evidence is incomplete.")
        changed_at = timezone.now()
        ProductionExecutionPath.objects.using(using).filter(
            pk=path.pk
        )._update_for_execution_service(
            status=ProductionExecutionPath.Status.STATISTICS_COMPLETED,
            status_changed_at=changed_at,
            updated_by=actor,
        )
        event = _create_execution_event(
            using=using,
            execution_path=path,
            event_type=ProductionExecutionEvent.EventType.STATISTICS_COMPLETED,
            occurred_at=changed_at,
            actor=actor,
            actor_identifier_snapshot=_actor_snapshot(actor),
            old_status=ProductionExecutionPath.Status.FULL_STOP,
            new_status=ProductionExecutionPath.Status.STATISTICS_COMPLETED,
            metadata={
                "full_stop_event_id": full_stop_event.pk,
                "reconciliation": metadata["reconciliation"],
                "reservation_id": metadata["reservation_id"],
                "deviation_ids": deviation_ids,
                "note": (note or "").strip(),
            },
        )
    path.refresh_from_db(using=using)
    return event
