"""Canonical path-scoped approval, edit, and cancellation workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from catalog.models import Machine, MoldMachineCompatibility, PhysicalMold
from production.models import ProductionExecutionPath
from production.path_execution import (
    activate_execution_path,
    cancel_unstarted_execution_path,
    edit_unstarted_execution_path,
    get_or_create_unstarted_execution_path,
)

from .approval_service import assert_can_approve
from .models import (
    PathOperationalApproval,
    PhysicalMoldReservation,
    PlannedProductionPath,
)
from .path_foundation import classify_compatibility, classify_mold_status
from .reservation_integrity import (
    PhysicalMoldReservationConflict,
    activate_reservation,
    lock_physical_molds,
    transition_active_reservation,
)


class StalePathApproval(ValidationError):
    pass


class CancelledPathApproval(ValidationError):
    pass


@dataclass(frozen=True)
class PathApprovalPreview:
    planned_path_id: int
    fingerprint: str
    snapshot: dict
    required_confirmations: tuple[str, ...]


@dataclass(frozen=True)
class PathOperationResult:
    approval: PathOperationalApproval
    reservation: PhysicalMoldReservation
    execution_path: ProductionExecutionPath
    created: bool


def _date(value):
    return value.isoformat() if value is not None else None


def _snapshot(path, *, machine=None, physical_mold=None, target=None, start=None, end=None, using="default"):
    machine = machine or path.machine
    physical_mold = physical_mold or path.physical_mold
    start = start or path.planned_start_date
    end = end or path.planned_end_date
    target = int(path.planned_share_quantity if target is None else target)
    compatibility = classify_compatibility(
        physical_mold=physical_mold, machine=machine, using=using
    )
    return {
        "planned_path_id": path.pk,
        "requirement_id": path.requirement_id,
        "requirement_target_quantity": int(path.requirement.quantity or 0),
        "machine_id": machine.pk,
        "physical_mold_id": physical_mold.pk,
        "planned_share_quantity": target,
        "planned_start_date": _date(start),
        "planned_end_date": _date(end),
        "compatibility_status": compatibility.status,
        "physical_mold_status": physical_mold.status,
        "estimated_ready_date": _date(physical_mold.estimated_ready_date),
    }


def _fingerprint(snapshot):
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_snapshot(
    path, snapshot, *, physical_mold, machine, start_date, using, confirmations=()
):
    if not path.is_active:
        raise ValidationError("Inactive planned paths cannot be approved.")
    if snapshot["planned_share_quantity"] <= 0:
        raise ValidationError("A positive GOOD target is required.")
    if snapshot["planned_start_date"] > snapshot["planned_end_date"]:
        raise ValidationError("The reservation start date must not be after its end date.")
    compatibility = classify_compatibility(
        physical_mold=physical_mold, machine=machine, using=using
    )
    if compatibility.status == MoldMachineCompatibility.Status.INCOMPATIBLE:
        raise ValidationError("The selected physical mold and machine are incompatible.")
    required = []
    if compatibility.status in {
        MoldMachineCompatibility.Status.UNKNOWN,
        MoldMachineCompatibility.Status.NEEDS_REVIEW,
    }:
        required.append(compatibility.status)
    mold = classify_mold_status(
        physical_mold=physical_mold,
        planned_start_date=start_date,
    )
    if physical_mold.status == PhysicalMold.Status.UNDER_REPAIR:
        if not mold.future_candidate:
            raise ValidationError("The mold is not ready by the planned start date.")
        required.append("under_repair_future_use")
    elif physical_mold.status in {
        PhysicalMold.Status.UNAVAILABLE,
        PhysicalMold.Status.INACTIVE,
    }:
        raise ValidationError("The physical mold is unavailable.")
    supplied = set(confirmations or ())
    missing = sorted(set(required) - supplied)
    if missing:
        raise ValidationError("Manager confirmation is required: " + ", ".join(missing))
    return tuple(sorted(required))


def preview_path_approval(path_or_id, *, user=None, require_permission=False, using="default"):
    if require_permission:
        assert_can_approve(user)
    path_id = path_or_id.pk if isinstance(path_or_id, PlannedProductionPath) else path_or_id
    path = (
        PlannedProductionPath.objects.using(using)
        .select_related("requirement", "machine", "physical_mold")
        .get(pk=path_id)
    )
    snapshot = _snapshot(path, using=using)
    required = _validate_snapshot(
        path,
        snapshot,
        physical_mold=path.physical_mold,
        machine=path.machine,
        start_date=path.planned_start_date,
        using=using,
        confirmations=(
            MoldMachineCompatibility.Status.UNKNOWN,
            MoldMachineCompatibility.Status.NEEDS_REVIEW,
            "under_repair_future_use",
        ),
    )
    return PathApprovalPreview(path.pk, _fingerprint(snapshot), snapshot, required)


def _assert_fingerprint(path, submitted_fingerprint, *, using):
    snapshot = _snapshot(path, using=using)
    fingerprint = _fingerprint(snapshot)
    if submitted_fingerprint != fingerprint:
        raise StalePathApproval("The path changed after preview; review it again.")
    return snapshot, fingerprint


def _next_sequence(path, *, using):
    latest = path.operational_approvals.using(using).order_by("-sequence").first()
    return (latest.sequence + 1) if latest else 1


def _create_approval_evidence(*, using, **values):
    """Persist evidence only inside the narrow canonical-service scope."""
    evidence = PathOperationalApproval(**values)
    evidence._operational_approval_service_create = True
    try:
        evidence.save(using=using, force_insert=True)
    finally:
        del evidence._operational_approval_service_create
    return evidence


def approve_path(path_id, *, submitted_fingerprint, actor, confirmations=(), reason="", using="default"):
    assert_can_approve(actor)
    with transaction.atomic(using=using):
        path = (
            PlannedProductionPath.objects.using(using)
            .select_for_update()
            .select_related("requirement__item__plan", "machine", "physical_mold")
            .get(pk=path_id)
        )
        from .program_governance import assert_operational_program_is_governed

        assert_operational_program_is_governed(path.requirement.item.plan, using=using)
        snapshot, fingerprint = _assert_fingerprint(path, submitted_fingerprint, using=using)
        latest = path.operational_approvals.using(using).order_by("-sequence").first()
        if latest and latest.action == PathOperationalApproval.Action.CANCELLED:
            raise CancelledPathApproval("A cancelled planned path cannot be reactivated.")
        if latest and latest.path_fingerprint == fingerprint:
            execution = latest.execution_path
            reservation = latest.reservation
            if (
                execution.status in {ProductionExecutionPath.Status.AWAITING, ProductionExecutionPath.Status.RUNNING}
                and reservation.status == PhysicalMoldReservation.Status.ACTIVE
            ):
                return PathOperationResult(latest, reservation, execution, False)
        required = _validate_snapshot(
            path,
            snapshot,
            physical_mold=path.physical_mold,
            machine=path.machine,
            start_date=path.planned_start_date,
            using=using,
            confirmations=confirmations,
        )
        lock_physical_molds([path.physical_mold_id], using=using)
        if PhysicalMoldReservation.objects.using(using).active_overlaps(
            physical_mold=path.physical_mold,
            start_date=path.planned_start_date,
            end_date=path.planned_end_date,
        ).exists():
            raise PhysicalMoldReservationConflict(
                "The physical mold already has an overlapping active reservation."
            )
        execution, _ = get_or_create_unstarted_execution_path(path, actor=actor, using=using)
        if execution.status != ProductionExecutionPath.Status.PREPARING:
            raise ValidationError("Existing operational state does not match this approval.")
        reservation = PhysicalMoldReservation.objects.using(using).create(
            approval_revision=None,
            source_path=path,
            requirement=path.requirement,
            machine=path.machine,
            physical_mold=path.physical_mold,
            execution_path=execution,
            approved_planned_share_quantity=path.planned_share_quantity,
            reserved_start_date=path.planned_start_date,
            reserved_end_date=path.planned_end_date,
            compatibility_status_snapshot=snapshot["compatibility_status"],
            mold_status_snapshot=path.physical_mold.status,
            estimated_ready_date_snapshot=path.physical_mold.estimated_ready_date,
            status=PhysicalMoldReservation.Status.PREPARING,
        )
        activate_reservation(reservation, using=using)
        activate_execution_path(execution, actor=actor, using=using)
        approval = _create_approval_evidence(
            using=using,
            planned_path=path,
            execution_path=execution,
            reservation=reservation,
            sequence=_next_sequence(path, using=using),
            action=PathOperationalApproval.Action.APPROVED,
            path_fingerprint=fingerprint,
            after_snapshot=snapshot,
            manager_confirmations=list(required),
            actor=actor,
            reason=(reason or "").strip(),
        )
        return PathOperationResult(approval, reservation, execution, True)


def cancel_path(path_id, *, actor, reason="", using="default"):
    assert_can_approve(actor)
    with transaction.atomic(using=using):
        path = PlannedProductionPath.objects.using(using).select_for_update().get(pk=path_id)
        latest = path.operational_approvals.using(using).select_related(
            "execution_path", "reservation"
        ).order_by("-sequence").first()
        if latest is None:
            raise ValidationError("This path has no operational approval.")
        if latest.action == PathOperationalApproval.Action.CANCELLED:
            return PathOperationResult(latest, latest.reservation, latest.execution_path, False)
        execution = latest.execution_path
        reservation = latest.reservation
        lock_physical_molds([reservation.physical_mold_id], using=using)
        cancel_unstarted_execution_path(execution, actor=actor, reason=reason, using=using)
        transition_active_reservation(
            reservation, status=PhysicalMoldReservation.Status.CANCELLED, using=using
        )
        evidence = _create_approval_evidence(
            using=using,
            planned_path=path,
            execution_path=execution,
            reservation=reservation,
            sequence=_next_sequence(path, using=using),
            action=PathOperationalApproval.Action.CANCELLED,
            path_fingerprint=latest.path_fingerprint,
            before_snapshot=latest.after_snapshot,
            after_snapshot=latest.after_snapshot,
            actor=actor,
            reason=(reason or "").strip(),
        )
        return PathOperationResult(evidence, reservation, execution, True)


def edit_path(
    path_id,
    *,
    submitted_fingerprint,
    actor,
    machine_id,
    physical_mold_id,
    target,
    start_date,
    end_date,
    confirmations=(),
    reason="",
    using="default",
):
    assert_can_approve(actor)
    with transaction.atomic(using=using):
        path = (
            PlannedProductionPath.objects.using(using)
            .select_for_update()
            .select_related("requirement", "machine", "physical_mold")
            .get(pk=path_id)
        )
        before, _ = _assert_fingerprint(path, submitted_fingerprint, using=using)
        latest = path.operational_approvals.using(using).select_related(
            "execution_path", "reservation"
        ).order_by("-sequence").first()
        if latest is None or latest.action == PathOperationalApproval.Action.CANCELLED:
            raise ValidationError("Only an active approved path may be edited.")
        machine = Machine.objects.using(using).get(pk=machine_id)
        mold = PhysicalMold.objects.using(using).get(pk=physical_mold_id)
        proposed = _snapshot(
            path,
            machine=machine,
            physical_mold=mold,
            target=int(target),
            start=start_date,
            end=end_date,
            using=using,
        )
        required = _validate_snapshot(
            path,
            proposed,
            physical_mold=mold,
            machine=machine,
            start_date=start_date,
            using=using,
            confirmations=confirmations,
        )
        lock_physical_molds(
            [latest.reservation.physical_mold_id, mold.pk], using=using
        )
        execution = latest.execution_path
        if execution.first_production_at is not None:
            raise ValidationError("Operational values cannot change after first production.")
        replacement = PhysicalMoldReservation.objects.using(using).create(
            approval_revision=None,
            source_path=path,
            requirement=path.requirement,
            machine=machine,
            physical_mold=mold,
            execution_path=execution,
            approved_planned_share_quantity=int(target),
            reserved_start_date=start_date,
            reserved_end_date=end_date,
            compatibility_status_snapshot=proposed["compatibility_status"],
            mold_status_snapshot=mold.status,
            estimated_ready_date_snapshot=mold.estimated_ready_date,
            status=PhysicalMoldReservation.Status.PREPARING,
        )
        transition_active_reservation(
            latest.reservation,
            status=PhysicalMoldReservation.Status.SUPERSEDED,
            using=using,
        )
        activate_reservation(replacement, using=using)
        edit_unstarted_execution_path(
            execution,
            machine=machine,
            physical_mold=mold,
            current_good_target=int(target),
            actor=actor,
            before_snapshot=before,
            using=using,
        )
        PlannedProductionPath.objects.using(using).filter(pk=path.pk).update(
            machine=machine,
            physical_mold=mold,
            planned_share_quantity=int(target),
            planned_start_date=start_date,
            planned_end_date=end_date,
            updated_by=actor,
        )
        fingerprint = _fingerprint(proposed)
        evidence = _create_approval_evidence(
            using=using,
            planned_path=path,
            execution_path=execution,
            reservation=replacement,
            sequence=_next_sequence(path, using=using),
            action=PathOperationalApproval.Action.EDITED,
            path_fingerprint=fingerprint,
            before_snapshot=before,
            after_snapshot=proposed,
            manager_confirmations=list(required),
            actor=actor,
            reason=(reason or "").strip(),
        )
        return PathOperationResult(evidence, replacement, execution, True)
