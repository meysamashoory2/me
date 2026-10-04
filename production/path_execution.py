"""Internal, transaction-safe primitives for path-based production execution."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import UserProfile
from planning.models import PlannedProductionPath

from .models import (
    ProductionDayEntry,
    ProductionExecutionEvent,
    ProductionExecutionPath,
    ProductionRequirementProgram,
)


def _actor_snapshot(user) -> str:
    if user is None:
        return ""
    return str(user.get_username() or user.pk)


def _require_data_entry(user, *, using: str) -> None:
    if user is None or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Production data-entry permission is required.")
    if getattr(user, "is_superuser", False):
        return
    profile = UserProfile.objects.using(using).filter(user_id=user.pk).first()
    if profile is None or not profile.can_enter_data:
        raise PermissionDenied("Production data-entry permission is required.")


def get_or_create_requirement_program(requirement, *, actor=None, using="default"):
    with transaction.atomic(using=using):
        return ProductionRequirementProgram.objects.using(using).get_or_create(
            requirement=requirement,
            defaults={"created_by": actor},
        )


def get_or_create_unstarted_execution_path(
    planned_path: PlannedProductionPath, *, actor=None, using="default"
):
    target = int(planned_path.planned_share_quantity or 0)
    if target <= 0:
        raise ValidationError("A positive planned share is required for execution.")
    with transaction.atomic(using=using):
        program, _ = get_or_create_requirement_program(
            planned_path.requirement, actor=actor, using=using
        )
        existing = (
            ProductionExecutionPath.objects.using(using)
            .select_for_update()
            .filter(
                requirement_program=program,
                machine=planned_path.machine,
                physical_mold=planned_path.physical_mold,
                first_production_at__isnull=True,
                status__in=[
                    ProductionExecutionPath.Status.PREPARING,
                    ProductionExecutionPath.Status.AWAITING,
                ],
            )
            .first()
        )
        if existing:
            return existing, False
        try:
            with transaction.atomic(using=using):
                path = ProductionExecutionPath.objects.using(using).create(
                    requirement_program=program,
                    machine=planned_path.machine,
                    physical_mold=planned_path.physical_mold,
                    initial_good_target=target,
                    current_good_target=target,
                    created_by=actor,
                    updated_by=actor,
                )
        except IntegrityError:
            path = ProductionExecutionPath.objects.using(using).get(
                requirement_program=program,
                machine=planned_path.machine,
                physical_mold=planned_path.physical_mold,
                first_production_at__isnull=True,
                status__in=[
                    ProductionExecutionPath.Status.PREPARING,
                    ProductionExecutionPath.Status.AWAITING,
                ],
            )
            return path, False
        return path, True


def activate_execution_path(path, *, actor=None, using="default"):
    with transaction.atomic(using=using):
        current = ProductionExecutionPath.objects.using(using).select_for_update().get(pk=path.pk)
        if current.status != ProductionExecutionPath.Status.PREPARING:
            raise ValidationError("Only a PREPARING execution path may be activated.")
        changed_at = timezone.now()
        ProductionExecutionPath.objects.using(using).filter(pk=current.pk)._update_for_execution_service(
            status=ProductionExecutionPath.Status.AWAITING,
            status_changed_at=changed_at,
            updated_by=actor,
        )
        event = ProductionExecutionEvent.objects.using(using).create(
            execution_path=current,
            event_type=ProductionExecutionEvent.EventType.ACTIVATED,
            occurred_at=changed_at,
            actor=actor,
            actor_identifier_snapshot=_actor_snapshot(actor),
            old_status=ProductionExecutionPath.Status.PREPARING,
            new_status=ProductionExecutionPath.Status.AWAITING,
        )
    path.refresh_from_db(using=using)
    return event


def cancel_unstarted_execution_path(path, *, actor, reason="", using="default"):
    """Cancel one operational path before its first production record."""
    with transaction.atomic(using=using):
        current = ProductionExecutionPath.objects.using(using).select_for_update().get(pk=path.pk)
        if current.first_production_at is not None or current.day_entries.using(using).exists():
            raise ValidationError("An execution path cannot be cancelled after first production.")
        if current.status == ProductionExecutionPath.Status.CANCELLED:
            return None
        if current.status != ProductionExecutionPath.Status.AWAITING:
            raise ValidationError("Only an unstarted active execution path may be cancelled.")
        changed_at = timezone.now()
        ProductionExecutionPath.objects.using(using).filter(pk=current.pk)._update_for_execution_service(
            status=ProductionExecutionPath.Status.CANCELLED,
            status_changed_at=changed_at,
            updated_by=actor,
        )
        event = ProductionExecutionEvent.objects.using(using).create(
            execution_path=current,
            event_type=ProductionExecutionEvent.EventType.CANCELLED,
            occurred_at=changed_at,
            actor=actor,
            actor_identifier_snapshot=_actor_snapshot(actor),
            old_status=current.status,
            new_status=ProductionExecutionPath.Status.CANCELLED,
            metadata={"reason": (reason or "").strip()},
        )
    path.refresh_from_db(using=using)
    return event


def edit_unstarted_execution_path(
    path, *, machine, physical_mold, current_good_target, actor, before_snapshot, using="default"
):
    """Apply a validated manager edit without changing execution-path identity."""
    with transaction.atomic(using=using):
        current = ProductionExecutionPath.objects.using(using).select_for_update().get(pk=path.pk)
        if current.first_production_at is not None or current.day_entries.using(using).exists():
            raise ValidationError("Machine, mold, and target are immutable after first production.")
        if current.status != ProductionExecutionPath.Status.AWAITING:
            raise ValidationError("Only an unstarted active execution path may be edited.")
        changed_at = timezone.now()
        ProductionExecutionPath.objects.using(using).filter(pk=current.pk)._update_for_execution_service(
            machine=machine,
            physical_mold=physical_mold,
            current_good_target=int(current_good_target),
            updated_by=actor,
        )
        event = ProductionExecutionEvent.objects.using(using).create(
            execution_path=current,
            event_type=ProductionExecutionEvent.EventType.OPERATIONAL_EDIT,
            occurred_at=changed_at,
            actor=actor,
            actor_identifier_snapshot=_actor_snapshot(actor),
            old_status=current.status,
            new_status=current.status,
            metadata={"before": before_snapshot},
        )
    path.refresh_from_db(using=using)
    return event


def register_path_production(
    path,
    *,
    date,
    shots,
    active_cavities,
    scrap_pieces,
    actor,
    description="",
    using="default",
):
    _require_data_entry(actor, using=using)
    shots = int(shots)
    active_cavities = int(active_cavities)
    scrap_pieces = int(scrap_pieces)
    if shots < 0 or active_cavities <= 0 or scrap_pieces < 0:
        raise ValidationError("Shots and scrap must be nonnegative; cavities must be positive.")
    gross = shots * active_cavities
    if scrap_pieces > gross:
        raise ValidationError("Scrap pieces cannot exceed gross pieces.")
    good = gross - scrap_pieces

    with transaction.atomic(using=using):
        current = ProductionExecutionPath.objects.using(using).select_for_update().get(pk=path.pk)
        if current.status not in {
            ProductionExecutionPath.Status.AWAITING,
            ProductionExecutionPath.Status.RUNNING,
        }:
            raise ValidationError("Production is not allowed for the execution path status.")
        if ProductionDayEntry.objects.using(using).filter(execution_path=current, date=date).exists():
            raise ValidationError("A production record already exists for this path and date.")
        accepted_at = timezone.now()
        entry = ProductionDayEntry(
            program=None,
            execution_path=current,
            date=date,
            produced_quantity=shots,
            scrap_quantity=scrap_pieces,
            active_cavities=active_cavities,
            shots=shots,
            gross_pieces=gross,
            good_pieces=good,
            description=description,
            created_by=actor,
        )
        entry._execution_service_create = True
        entry.save(using=using, force_insert=True)
        if current.first_production_at is None:
            old_status = current.status
            ProductionExecutionPath.objects.using(using).filter(pk=current.pk)._update_for_execution_service(
                first_production_at=accepted_at,
                status=ProductionExecutionPath.Status.RUNNING,
                status_changed_at=accepted_at,
                updated_by=actor,
            )
            ProductionExecutionEvent.objects.using(using).create(
                execution_path=current,
                event_type=ProductionExecutionEvent.EventType.FIRST_PRODUCTION,
                occurred_at=accepted_at,
                actor=actor,
                actor_identifier_snapshot=_actor_snapshot(actor),
                old_status=old_status,
                new_status=ProductionExecutionPath.Status.RUNNING,
                metadata={"production_day_entry_id": entry.pk},
            )
        ProductionExecutionEvent.objects.using(using).create(
            execution_path=current,
            event_type=ProductionExecutionEvent.EventType.PRODUCTION_ACCEPTED,
            occurred_at=accepted_at,
            actor=actor,
            actor_identifier_snapshot=_actor_snapshot(actor),
            old_status=ProductionExecutionPath.Status.RUNNING,
            new_status=ProductionExecutionPath.Status.RUNNING,
            metadata={"production_day_entry_id": entry.pk},
        )
    path.refresh_from_db(using=using)
    return entry
