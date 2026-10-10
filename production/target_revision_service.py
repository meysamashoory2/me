"""Canonical post-production Counter Target request and decision workflow."""

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import UserProfile
from catalog.models import DeviationReason
from .models import (
    ProductionDeviation,
    ProductionExecutionPath,
    ProductionTargetDecision,
    ProductionTargetRevision,
    _target_revision_creation_scope,
)
from .target_semantics import snapshot_for_execution_path
from .target_semantics import RequiredGoodUnavailable
from .path_execution import assert_governed_production_lineage


class StaleTargetRevision(ValidationError):
    pass


class DuplicatePendingTargetRevision(ValidationError):
    pass


@dataclass(frozen=True)
class TargetDecisionResult:
    request: ProductionTargetRevision
    decision: ProductionTargetDecision
    deviation: ProductionDeviation | None


ELIGIBLE_STATES = {
    ProductionExecutionPath.Status.RUNNING,
    ProductionExecutionPath.Status.TEMPORARY_STOP,
}


def _model_identity(value, model, label):
    if isinstance(value, model):
        return value.pk
    if isinstance(value, int):
        return value
    raise ValidationError(f"{label} must identify a {model.__name__}.")


def _require_request_permission(actor, *, using):
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Production data-entry permission is required.")
    if getattr(actor, "is_superuser", False):
        return
    profile = UserProfile.objects.using(using).filter(user_id=actor.pk).first()
    if profile is None or not profile.can_enter_data:
        raise PermissionDenied("Production data-entry permission is required.")


def _require_decision_permission(actor, *, using):
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Manager approval permission is required.")
    if getattr(actor, "is_superuser", False):
        return
    profile = UserProfile.objects.using(using).filter(user_id=actor.pk).first()
    if profile is None or not profile.can_approve_plans:
        raise PermissionDenied("Manager approval permission is required.")


def _validate_reason(reason, note):
    if reason is None or not reason.is_active:
        raise ValidationError("An active structured production reason is required.")
    if not reason.code or reason.applicability not in {
        DeviationReason.Applicability.PRODUCTION,
        DeviationReason.Applicability.BOTH,
    }:
        raise ValidationError("A structured production-applicable reason is required.")
    if (reason.requires_note or reason.code == DeviationReason.Code.OTHER) and not note.strip():
        raise ValidationError("This target revision reason requires a note.")


def _validate_operational_path(path):
    if path.status not in ELIGIBLE_STATES:
        raise ValidationError("This execution path is not eligible for target revision.")
    if path.first_production_at is None or not path.day_entries.exists():
        raise ValidationError("Post-production target revision requires accepted production.")


def _cumulative_gross(path):
    return int(path.day_entries.aggregate(total=Sum("gross_pieces"))["total"] or 0)


def _create_request_for_locked_path(
    path,
    *,
    requested_target,
    reason,
    actor,
    source,
    note,
    context,
    using,
):
    """Create request evidence after the caller has locked the execution path."""
    _validate_reason(reason, note)
    _validate_operational_path(path)
    gross = _cumulative_gross(path)
    if requested_target < gross:
        raise ValidationError("Requested Counter Target cannot be below cumulative Gross.")
    if requested_target == path.current_good_target:
        raise ValidationError("Requested Counter Target must change the effective target.")
    if path.target_revisions.using(using).filter(
        state=ProductionTargetRevision.State.PENDING,
        decision__isnull=True,
    ).exists():
        raise DuplicatePendingTargetRevision(
            "This execution path already has an undecided target request."
        )
    revision = ProductionTargetRevision(
        execution_path=path,
        original_target=path.initial_good_target,
        previous_effective_target=path.current_good_target,
        requested_target=requested_target,
        delta=requested_target - path.current_good_target,
        source=source,
        requester=actor,
        reason=reason,
        note=note,
        state=ProductionTargetRevision.State.PENDING,
        context={
            **(context or {}),
            "cumulative_gross_at_request": gross,
        },
    )
    with _target_revision_creation_scope():
        revision.save(using=using, force_insert=True)
    return revision


def request_target_revision(
    path_or_id,
    *,
    requested_target,
    reason,
    actor,
    source=ProductionTargetRevision.Source.MANUAL,
    note="",
    context=None,
    using="default",
    require_governed_lineage=False,
):
    _require_request_permission(actor, using=using)
    requested_target = int(requested_target)
    note = (note or "").strip()
    reason_id = _model_identity(reason, DeviationReason, "Deviation reason")
    if source not in {
        ProductionTargetRevision.Source.MANUAL,
        ProductionTargetRevision.Source.PLANNING,
        ProductionTargetRevision.Source.REALLOCATION,
    }:
        raise ValidationError("Unsupported target-revision source.")
    path_id = _model_identity(path_or_id, ProductionExecutionPath, "Execution path")
    with transaction.atomic(using=using):
        path = (
            ProductionExecutionPath.objects.using(using)
            .select_for_update()
            .get(pk=path_id)
        )
        if require_governed_lineage:
            assert_governed_production_lineage(path, using=using)
        reason = DeviationReason.objects.using(using).get(pk=reason_id)
        return _create_request_for_locked_path(
            path,
            requested_target=requested_target,
            reason=reason,
            actor=actor,
            source=source,
            note=note,
            context=context,
            using=using,
        )


def decide_target_revision(
    request_or_id,
    *,
    outcome,
    actor,
    decided_target=None,
    note="",
    using="default",
    require_governed_lineage=False,
):
    _require_decision_permission(actor, using=using)
    if outcome not in ProductionTargetDecision.Outcome.values:
        raise ValidationError("Unsupported target decision outcome.")
    request_id = _model_identity(
        request_or_id, ProductionTargetRevision, "Target request"
    )
    request_identity = ProductionTargetRevision.objects.using(using).only(
        "execution_path_id"
    ).get(pk=request_id)
    with transaction.atomic(using=using):
        path = (
            ProductionExecutionPath.objects.using(using)
            .select_for_update()
            .get(pk=request_identity.execution_path_id)
        )
        if require_governed_lineage:
            assert_governed_production_lineage(path, using=using)
        request = (
            ProductionTargetRevision.objects.using(using)
            .select_for_update()
            .select_related("reason")
            .get(pk=request_id)
        )
        _validate_operational_path(path)
        if request.state != ProductionTargetRevision.State.PENDING:
            raise ValidationError("Only pending C2 requests may be decided.")
        if ProductionTargetDecision.objects.using(using).filter(request=request).exists():
            raise ValidationError("This target request already has a decision.")
        if request.previous_effective_target != path.current_good_target:
            raise StaleTargetRevision(
                "The effective Counter Target changed after this request was created."
            )
        gross = _cumulative_gross(path)
        note = (note or "").strip()
        target = None
        signed_delta = None
        if outcome != ProductionTargetDecision.Outcome.REJECTED:
            if outcome == ProductionTargetDecision.Outcome.MODIFIED and decided_target is None:
                raise ValidationError("A modified decision requires a Counter Target.")
            target = request.requested_target if outcome == ProductionTargetDecision.Outcome.APPROVED else int(decided_target)
            if target == path.current_good_target:
                raise ValidationError("A target decision must change the effective target.")
            if target < gross:
                raise ValidationError("Decided Counter Target cannot be below cumulative Gross.")
            signed_delta = target - path.current_good_target
        decision = ProductionTargetDecision(
            request=request,
            outcome=outcome,
            decided_target=target,
            signed_delta=signed_delta,
            cumulative_gross_at_decision=gross,
            decided_by=actor,
            decided_at=timezone.now(),
            note=note,
            context={
                "previous_target": path.current_good_target,
                "requested_target": request.requested_target,
                "decided_target": target,
                "signed_delta": signed_delta,
                "cumulative_gross": gross,
            },
        )
        decision._target_revision_service_create = True
        try:
            decision.save(using=using, force_insert=True)
        finally:
            del decision._target_revision_service_create
        deviation = None
        if outcome != ProductionTargetDecision.Outcome.REJECTED:
            ProductionExecutionPath.objects.using(using).filter(
                pk=path.pk
            )._update_counter_target_for_revision(
                current_good_target=target,
                updated_by=actor,
            )
            deviation = ProductionDeviation.objects.using(using).create(
                execution_path=path,
                category=ProductionDeviation.Category.TARGET_CHANGE,
                reason=request.reason,
                explained_quantity=abs(signed_delta),
                note=note or request.note,
                actor=actor,
                target_revision=request,
                target_decision=decision,
                context=decision.context,
            )
        return TargetDecisionResult(request, decision, deviation)


def request_scrap_compensation(
    path_or_id,
    *,
    actor,
    note="",
    using="default",
    require_governed_lineage=False,
):
    _require_request_permission(actor, using=using)
    path_id = _model_identity(path_or_id, ProductionExecutionPath, "Execution path")
    with transaction.atomic(using=using):
        path = (
            ProductionExecutionPath.objects.using(using)
            .select_for_update()
            .get(pk=path_id)
        )
        if require_governed_lineage:
            assert_governed_production_lineage(path, using=using)
        if not path.operational_approvals.using(using).filter(
            planned_path__requirement__item__plan__final_revision__isnull=False
        ).exists():
            raise RequiredGoodUnavailable(
                "Scrap Compensation requires authoritative Final-path approval lineage."
            )
        snapshot = snapshot_for_execution_path(path)
        if not snapshot.scrap_compensation_needed:
            raise ValidationError(
                "The current production state needs no Scrap Compensation request."
            )
        if not snapshot.proposed_target_is_valid:
            raise ValidationError("The Scrap Compensation candidate is below the production floor.")
        reason = DeviationReason.objects.using(using).filter(
            code=DeviationReason.Code.SCRAP_COMPENSATION,
            is_active=True,
            applicability__in=[
                DeviationReason.Applicability.PRODUCTION,
                DeviationReason.Applicability.BOTH,
            ],
        ).first()
        if reason is None:
            raise ValidationError("The canonical Scrap Compensation reason is unavailable.")
        return _create_request_for_locked_path(
            path,
            requested_target=snapshot.potential_proposed_target,
            reason=reason,
            actor=actor,
            source=ProductionTargetRevision.Source.SCRAP_COMPENSATION,
            note=note,
            context={
                "counter_snapshot": snapshot.__dict__,
                "proposal_kind": "scrap_compensation",
            },
            using=using,
        )
