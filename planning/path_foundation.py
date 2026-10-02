"""Side-effect-free planning-path validation for Step 5 Slice 2A."""

from __future__ import annotations

from dataclasses import dataclass

from catalog.models import MoldMachineCompatibility, PhysicalMold

from .models import PhysicalMoldReservation, PlannedProductionPath


@dataclass(frozen=True)
class CompatibilityClassification:
    status: str
    operationally_allowed: bool
    requires_manager_decision: bool
    smart_planner_candidate: bool


@dataclass(frozen=True)
class MoldStatusClassification:
    status: str
    operationally_ready: bool
    future_candidate: bool
    requires_approval_revalidation: bool


def classify_compatibility(*, physical_mold, machine, using: str = "default"):
    status = (
        MoldMachineCompatibility.objects.using(using)
        .filter(physical_mold=physical_mold, machine=machine)
        .values_list("status", flat=True)
        .first()
        or MoldMachineCompatibility.Status.UNKNOWN
    )
    return CompatibilityClassification(
        status=status,
        operationally_allowed=status == MoldMachineCompatibility.Status.CONFIRMED,
        requires_manager_decision=status in {
            MoldMachineCompatibility.Status.NEEDS_REVIEW,
            MoldMachineCompatibility.Status.UNKNOWN,
        },
        smart_planner_candidate=status == MoldMachineCompatibility.Status.CONFIRMED,
    )


def classify_mold_status(*, physical_mold, planned_start_date=None):
    ready = physical_mold.status == PhysicalMold.Status.READY
    future = bool(
        physical_mold.status == PhysicalMold.Status.UNDER_REPAIR
        and physical_mold.estimated_ready_date
        and planned_start_date
        and planned_start_date > physical_mold.estimated_ready_date
    )
    return MoldStatusClassification(
        status=physical_mold.status,
        operationally_ready=ready,
        future_candidate=future,
        requires_approval_revalidation=not ready,
    )


def validate_proposed_path(path: PlannedProductionPath, *, using: str = "default") -> dict:
    """Classify a draft path without saving or creating operational state."""
    errors = []
    if path.planned_start_date > path.planned_end_date:
        errors.append("planned_start_date must be on or before planned_end_date")
    compatibility = classify_compatibility(
        physical_mold=path.physical_mold,
        machine=path.machine,
        using=using,
    )
    mold_status = classify_mold_status(
        physical_mold=path.physical_mold,
        planned_start_date=path.planned_start_date,
    )
    if compatibility.status == MoldMachineCompatibility.Status.INCOMPATIBLE:
        errors.append("physical mold and machine are incompatible")
    if physical_mold_unavailable := path.physical_mold.status in {
        PhysicalMold.Status.UNAVAILABLE,
        PhysicalMold.Status.INACTIVE,
    }:
        errors.append("physical mold is unavailable for planning")
    return {
        "valid": not errors,
        "errors": tuple(errors),
        "compatibility": compatibility,
        "mold_status": mold_status,
        "requires_manager_decision": compatibility.requires_manager_decision,
        "physical_mold_unavailable": physical_mold_unavailable,
    }


def active_reservation_overlaps(
    *, physical_mold, start_date, end_date, using: str = "default", exclude_pk=None
):
    queryset = PhysicalMoldReservation.objects.using(using).active_overlaps(
        physical_mold=physical_mold,
        start_date=start_date,
        end_date=end_date,
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset
