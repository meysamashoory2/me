"""Canonical governed pre-production revisions and deterministic impact analysis."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from catalog.models import Machine, MoldMachineCompatibility, PhysicalMold
from production.models import ProductionExecutionPath
from production.path_execution import _edit_unstarted_execution_path

from .approval_service import assert_can_approve
from .models import (
    FinalProgramRevision,
    PathOperationalApproval,
    PhysicalMoldReservation,
    PlannedProductionPath,
    PlanningDeviation,
    PlanningRevision,
)
from .operational_approval import (
    PathOperationResult,
    StalePathApproval,
    _create_approval_evidence,
    _fingerprint,
    _next_sequence,
    _snapshot,
    _validate_snapshot,
)
from .reservation_integrity import (
    PhysicalMoldReservationConflict,
    activate_reservation,
    is_reservation_exclusion_violation,
    lock_physical_molds,
    transition_active_reservation,
)


class ImpactLevel:
    CLEAR = "clear"
    POTENTIAL_IMPACT = "potential_impact"
    ACTUAL_CONFLICT = "actual_conflict"


@dataclass(frozen=True)
class ImpactIdentity:
    kind: str
    cycle_id: int | None = None
    final_revision_id: int | None = None
    planned_path_id: int | None = None
    execution_path_id: int | None = None
    physical_mold_id: int | None = None
    reservation_id: int | None = None


@dataclass(frozen=True)
class RevisionImpact:
    level: str
    affected: tuple[ImpactIdentity, ...]


@dataclass(frozen=True)
class PreProductionRevisionPreview:
    planned_path_id: int
    execution_path_id: int
    fingerprint: str
    before_snapshot: dict
    proposed_snapshot: dict
    required_confirmations: tuple[str, ...]
    impact: RevisionImpact


@dataclass(frozen=True)
class PreProductionRevisionResult:
    operation: PathOperationResult
    revision: PlanningRevision
    impact: RevisionImpact


class RevisionImpactConflict(ValidationError):
    def __init__(self, impact):
        self.impact = impact
        super().__init__("The requested revision conflicts with an approved or active schedule.")


def _latest_operation(path, *, using, lock=False):
    queryset = path.operational_approvals.using(using).select_related(
        "execution_path", "reservation"
    )
    if lock:
        queryset = queryset.select_for_update()
    latest = queryset.order_by("-sequence").first()
    if latest is None or latest.action == PathOperationalApproval.Action.CANCELLED:
        raise ValidationError("Only an active approved path may be revised.")
    if latest.reservation.status != PhysicalMoldReservation.Status.ACTIVE:
        raise ValidationError("The current operational reservation is not active.")
    return latest


def _operational_snapshot(path, latest, *, using):
    execution = latest.execution_path
    reservation = latest.reservation
    return _snapshot(
        path,
        machine=execution.machine,
        physical_mold=execution.physical_mold,
        target=execution.current_good_target,
        start=reservation.reserved_start_date,
        end=reservation.reserved_end_date,
        using=using,
    )


def _impact_snapshot(impact):
    return {
        "level": impact.level,
        "affected": [identity.__dict__ for identity in impact.affected],
    }


def _revision_contract(
    *, path, final_revision, latest, execution, reservation, before, proposed,
    required_confirmations, impact
):
    """Bind approval to exact governance, operational state, proposal, and impact."""
    return {
        "planned_path_id": path.pk,
        "final_revision_id": final_revision.pk,
        "operational_approval_id": latest.pk,
        "operational_approval_sequence": latest.sequence,
        "operational_approval_action": latest.action,
        "execution_path_id": execution.pk,
        "execution_status": execution.status,
        "reservation_id": reservation.pk,
        "reservation_status": reservation.status,
        "before": before,
        "proposed": proposed,
        "required_confirmations": list(required_confirmations),
        "impact": _impact_snapshot(impact),
    }


def analyze_revision_impact(
    *, path, current_reservation, proposed_snapshot, using="default", lock=False
):
    mold_id = proposed_snapshot["physical_mold_id"]
    start = proposed_snapshot["planned_start_date"]
    end = proposed_snapshot["planned_end_date"]
    from .utils import parse_jdate_string

    start_date = parse_jdate_string(start)
    end_date = parse_jdate_string(end)
    actual = []
    owners = ProductionExecutionPath.objects.using(using).filter(
        physical_mold_id=mold_id,
        status__in=[
            ProductionExecutionPath.Status.AWAITING,
            ProductionExecutionPath.Status.RUNNING,
            ProductionExecutionPath.Status.TEMPORARY_STOP,
        ],
    ).exclude(pk=current_reservation.execution_path_id)
    if lock:
        # Lock even unstarted candidates so first production cannot establish
        # mold ownership between this check and the revision commit.
        owners = owners.select_for_update()
    for execution in owners.order_by("pk"):
        if execution.first_production_at is None:
            continue
        actual.append(
            ImpactIdentity(
                kind="active_execution",
                execution_path_id=execution.pk,
                physical_mold_id=mold_id,
            )
        )
    reservations = (
        PhysicalMoldReservation.objects.using(using)
        .active_overlaps(
            physical_mold=mold_id,
            start_date=start_date,
            end_date=end_date,
        )
        .exclude(pk=current_reservation.pk)
    )
    if lock:
        reservation_ids = list(
            reservations.select_for_update().order_by("pk").values_list("pk", flat=True)
        )
        reservations = PhysicalMoldReservation.objects.using(using).filter(
            pk__in=reservation_ids
        )
    for reservation in reservations.select_related(
        "source_path__requirement__item__plan", "execution_path"
    ).order_by("pk"):
        plan = reservation.source_path.requirement.item.plan if reservation.source_path_id else None
        final_revision_id = (
            FinalProgramRevision.objects.using(using)
            .filter(final_plan_id=getattr(plan, "pk", None))
            .values_list("pk", flat=True)
            .first()
        )
        actual.append(
            ImpactIdentity(
                kind="active_reservation",
                cycle_id=getattr(plan, "governance_cycle_id", None),
                final_revision_id=final_revision_id,
                planned_path_id=reservation.source_path_id,
                execution_path_id=reservation.execution_path_id,
                physical_mold_id=mold_id,
                reservation_id=reservation.pk,
            )
        )
    if actual:
        return RevisionImpact(ImpactLevel.ACTUAL_CONFLICT, tuple(actual))

    potential = []
    planned = (
        PlannedProductionPath.objects.using(using)
        .filter(
            physical_mold_id=mold_id,
            planned_start_date__lte=end_date,
            planned_end_date__gte=start_date,
            is_active=True,
            requirement__item__plan__planning_mode="final",
            requirement__item__plan__final_revision__status=FinalProgramRevision.Status.EFFECTIVE,
        )
        .exclude(pk=path.pk)
        .select_related("requirement__item__plan__final_revision")
        .order_by("pk")
    )
    for other in planned:
        plan = other.requirement.item.plan
        potential.append(
            ImpactIdentity(
                kind="effective_final_plan",
                cycle_id=plan.governance_cycle_id,
                final_revision_id=plan.final_revision.pk,
                planned_path_id=other.pk,
                physical_mold_id=mold_id,
            )
        )
    if potential:
        return RevisionImpact(ImpactLevel.POTENTIAL_IMPACT, tuple(potential))
    return RevisionImpact(ImpactLevel.CLEAR, ())


def preview_preproduction_revision(
    path_or_id,
    *,
    machine_id,
    physical_mold_id,
    target,
    start_date,
    end_date,
    confirmations=(),
    using="default",
):
    path_id = path_or_id.pk if isinstance(path_or_id, PlannedProductionPath) else path_or_id
    path = (
        PlannedProductionPath.objects.using(using)
        .select_related("requirement__item__plan", "machine", "physical_mold")
        .get(pk=path_id)
    )
    from .program_governance import assert_operational_program_is_governed

    plan = path.requirement.item.plan
    assert_operational_program_is_governed(plan, using=using)
    final_revision = FinalProgramRevision.objects.using(using).get(
        final_plan=plan, status=FinalProgramRevision.Status.EFFECTIVE
    )
    latest = _latest_operation(path, using=using)
    execution = latest.execution_path
    if execution.first_production_at is not None or execution.day_entries.using(using).exists():
        raise ValidationError("Pre-production revisions are forbidden after first production.")
    machine = Machine.objects.using(using).get(pk=machine_id)
    mold = PhysicalMold.objects.using(using).get(pk=physical_mold_id)
    before = _operational_snapshot(path, latest, using=using)
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
    impact = analyze_revision_impact(
        path=path,
        current_reservation=latest.reservation,
        proposed_snapshot=proposed,
        using=using,
    )
    contract = _revision_contract(
        path=path,
        final_revision=final_revision,
        latest=latest,
        execution=execution,
        reservation=latest.reservation,
        before=before,
        proposed=proposed,
        required_confirmations=required,
        impact=impact,
    )
    return PreProductionRevisionPreview(
        path.pk,
        execution.pk,
        _fingerprint(contract),
        before,
        proposed,
        required,
        impact,
    )


def apply_preproduction_revision(
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
    try:
        with transaction.atomic(using=using):
            path = (
                PlannedProductionPath.objects.using(using)
                .select_for_update()
                .select_related("requirement__item__plan", "machine", "physical_mold")
                .get(pk=path_id)
            )
            plan = path.requirement.item.plan
            from .program_governance import assert_operational_program_is_governed

            assert_operational_program_is_governed(plan, using=using)
            final_revision = FinalProgramRevision.objects.using(using).select_for_update().get(
                final_plan=plan, status=FinalProgramRevision.Status.EFFECTIVE
            )
            latest = _latest_operation(path, using=using, lock=True)
            execution = (
                ProductionExecutionPath.objects.using(using)
                .select_for_update()
                .get(pk=latest.execution_path_id)
            )
            current_reservation = (
                PhysicalMoldReservation.objects.using(using)
                .select_for_update()
                .get(pk=latest.reservation_id)
            )
            if current_reservation.status != PhysicalMoldReservation.Status.ACTIVE:
                raise ValidationError("The current operational reservation is not active.")
            if execution.first_production_at is not None or execution.day_entries.using(using).exists():
                raise ValidationError("Pre-production revisions are forbidden after first production.")
            if execution.status != ProductionExecutionPath.Status.AWAITING:
                raise ValidationError("Only an unstarted active execution path may be revised.")
            latest.execution_path = execution
            latest.reservation = current_reservation
            before = _operational_snapshot(path, latest, using=using)
            lock_physical_molds(
                [current_reservation.physical_mold_id, physical_mold_id], using=using
            )
            machine = Machine.objects.using(using).get(pk=machine_id)
            mold = PhysicalMold.objects.using(using).get(pk=physical_mold_id)
            list(
                MoldMachineCompatibility.objects.using(using)
                .select_for_update()
                .filter(physical_mold=mold, machine=machine)
            )
            proposed = _snapshot(
                path,
                machine=machine,
                physical_mold=mold,
                target=int(target),
                start=start_date,
                end=end_date,
                using=using,
            )
            if proposed == before:
                raise ValidationError("The requested revision contains no operational change.")
            required = _validate_snapshot(
                path,
                proposed,
                physical_mold=mold,
                machine=machine,
                start_date=start_date,
                using=using,
                confirmations=confirmations,
            )
            impact = analyze_revision_impact(
                path=path,
                current_reservation=current_reservation,
                proposed_snapshot=proposed,
                using=using,
                lock=True,
            )
            contract = _revision_contract(
                path=path,
                final_revision=final_revision,
                latest=latest,
                execution=execution,
                reservation=current_reservation,
                before=before,
                proposed=proposed,
                required_confirmations=required,
                impact=impact,
            )
            if _fingerprint(contract) != submitted_fingerprint:
                raise StalePathApproval(
                    "The operational state, proposed revision, or impact changed after preview; review it again."
                )
            if impact.level == ImpactLevel.ACTUAL_CONFLICT:
                raise RevisionImpactConflict(impact)
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
                current_reservation,
                status=PhysicalMoldReservation.Status.SUPERSEDED,
                using=using,
            )
            activate_reservation(replacement, using=using)
            _edit_unstarted_execution_path(
                execution,
                machine=machine,
                physical_mold=mold,
                current_good_target=int(target),
                actor=actor,
                before_snapshot=before,
                using=using,
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
            revision = PlanningRevision.objects.using(using).create(
                cycle=final_revision.cycle,
                final_revision=final_revision,
                final_item=path.requirement.item,
                planned_path=path,
                revision_type=PlanningRevision.RevisionType.PATH,
                before_snapshot=before,
                after_snapshot=proposed,
                context={
                    "operation": "preproduction_revision",
                    "path_operational_approval_id": evidence.pk,
                    "previous_reservation_id": current_reservation.pk,
                    "replacement_reservation_id": replacement.pk,
                    "impact_level": impact.level,
                    "impact": [identity.__dict__ for identity in impact.affected],
                    "planning_deviation_required": False,
                    "manager_confirmations": list(required),
                    "reason": (reason or "").strip(),
                },
                actor=actor,
            )
            if PlanningDeviation.objects.using(using).filter(revision=revision).exists():
                raise RuntimeError("Pre-production revisions must not fabricate deviations.")
            operation = PathOperationResult(evidence, replacement, execution, True)
            return PreProductionRevisionResult(operation, revision, impact)
    except IntegrityError as error:
        if is_reservation_exclusion_violation(error):
            raise PhysicalMoldReservationConflict(
                "The physical mold already has an overlapping active reservation."
            ) from error
        raise
