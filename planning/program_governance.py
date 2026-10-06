"""Transaction-safe weekly program governance foundation."""

from __future__ import annotations

import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from accounts.models import UserProfile

from .approval_service import assert_can_approve
from .models import (
    PlanningGovernanceAlarm,
    PlanningGovernanceAlarmView,
    ProgramSourceApproval,
    SystemProgramRowDecision,
    WeeklyPlan,
    WeeklyProgramCycle,
    WeeklyProgramSequence,
)


def _create_evidence(model, *, using, **values):
    evidence = model(**values)
    evidence._governance_service_create = True
    try:
        evidence.save(using=using, force_insert=True)
    finally:
        del evidence._governance_service_create
    return evidence


def _snapshot_plan(plan, *, using):
    items = []
    for item in (
        plan.items.using(using)
        .prefetch_related("lines")
        .order_by("sequence", "pk")
    ):
        items.append(
            {
                "item_id": item.pk,
                "product_id": item.product_id,
                "machine_id": item.machine_id,
                "mold_id": item.mold_id,
                "lines": [
                    {
                        "line_id": line.pk,
                        "quantity": int(line.quantity or 0),
                        "mold_id": line.mold_id,
                        "active_cavities": int(line.active_cavities or 0),
                    }
                    for line in item.lines.all()
                ],
            }
        )
    return {
        "plan_id": plan.pk,
        "cycle_id": plan.governance_cycle_id,
        "program_number": plan.program_number,
        "program_date": plan.date.isoformat(),
        "planning_mode": plan.planning_mode,
        "items": items,
    }


def _fingerprint(snapshot):
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def initialize_program_numbering(*, starting_program_number, actor, using="default"):
    """One-time go-live boundary; no historical plans are inferred or paired."""
    assert_can_approve(actor)
    starting_program_number = int(starting_program_number)
    if starting_program_number <= 0:
        raise ValidationError("The starting program number must be positive.")
    try:
        with transaction.atomic(using=using):
            sequence = WeeklyProgramSequence(
                singleton=1,
                last_allocated_number=starting_program_number - 1,
                initialized_by=actor,
            )
            sequence._governance_service_create = True
            try:
                sequence.save(using=using, force_insert=True)
            finally:
                del sequence._governance_service_create
            return sequence
    except IntegrityError as error:
        raise ValidationError("Program numbering has already been initialized.") from error


def _manual_is_approved(cycle, *, using):
    manual = cycle.sources.using(using).filter(
        planning_mode=WeeklyPlan.PlanningMode.MANUAL
    ).first()
    if manual is None:
        return True
    return ProgramSourceApproval.objects.using(using).filter(
        source_plan=manual,
        approval_type=ProgramSourceApproval.ApprovalType.MANUAL,
    ).exists()


def _archive_unapproved_system_source(cycle, *, using):
    system = cycle.sources.using(using).filter(
        planning_mode=WeeklyPlan.PlanningMode.SYSTEMIC
    ).first()
    if system is None:
        return
    final_approved = ProgramSourceApproval.objects.using(using).filter(
        source_plan=system,
        approval_type=ProgramSourceApproval.ApprovalType.SYSTEM_FINAL,
    ).exists()
    if not final_approved:
        WeeklyPlan.objects.using(using).filter(pk=system.pk)._update_for_governance_service(
            source_state="history"
        )


def create_program_source(
    *, origin, program_date, actor, requested_program_number=None, using="default"
):
    """Create one Manual/System source or attach the missing peer to a cycle."""
    profile = UserProfile.objects.using(using).filter(user_id=getattr(actor, "pk", None)).first()
    if not getattr(actor, "is_superuser", False) and not (
        profile and profile.can_create_plans
    ):
        raise PermissionDenied("Only authorized Planning users may create Programs.")
    if origin not in {WeeklyPlan.PlanningMode.MANUAL, WeeklyPlan.PlanningMode.SYSTEMIC}:
        raise ValidationError("Unsupported program source origin.")
    if origin == WeeklyPlan.PlanningMode.MANUAL and requested_program_number is None:
        raise ValidationError("Manual program creation requires the expected program number.")
    try:
        requested = int(requested_program_number) if requested_program_number is not None else None
    except (TypeError, ValueError) as error:
        raise ValidationError("Program Number must be a positive integer.") from error
    if requested is not None and requested <= 0:
        raise ValidationError("Program Number must be a positive integer.")

    with transaction.atomic(using=using):
        try:
            sequence = WeeklyProgramSequence.objects.using(using).select_for_update().get(
                singleton=1
            )
        except WeeklyProgramSequence.DoesNotExist as error:
            if origin != WeeklyPlan.PlanningMode.SYSTEMIC or requested is None:
                raise ValidationError(
                    "Program numbering must be initialized before Manual creation."
                ) from error
            sequence = initialize_program_numbering(
                starting_program_number=requested, actor=actor, using=using
            )
            sequence = WeeklyProgramSequence.objects.using(using).select_for_update().get(
                singleton=1
            )

        current = (
            WeeklyProgramCycle.objects.using(using)
            .select_for_update()
            .filter(program_number=sequence.last_allocated_number)
            .first()
        )
        if current and requested == current.program_number:
            if current.program_date != program_date:
                raise ValidationError("Both sources of a program must use the same Program Date.")
            if current.sources.using(using).filter(planning_mode=origin).exists():
                raise ValidationError("This program source already exists.")
            cycle = current
        else:
            expected = sequence.last_allocated_number + 1
            candidate = expected if requested is None else requested
            if candidate != expected:
                raise ValidationError(f"The next valid Program Number is {expected}.")
            if current and not _manual_is_approved(current, using=using):
                raise ValidationError(
                    "The next Program cannot be created before Manual approval."
                )
            if current:
                _archive_unapproved_system_source(current, using=using)
            cycle = WeeklyProgramCycle.objects.using(using).create(
                program_number=expected,
                program_date=program_date,
                created_by=actor,
            )
            WeeklyProgramSequence.objects.using(using).filter(
                pk=sequence.pk
            )._update_for_governance_service(
                last_allocated_number=expected
            )

        plan = WeeklyPlan.objects.using(using).create(
            governance_cycle=cycle,
            program_number=str(cycle.program_number),
            date=cycle.program_date,
            planning_mode=origin,
            status=WeeklyPlan.Status.DRAFT,
            created_by=actor,
        )
        if origin == WeeklyPlan.PlanningMode.MANUAL and not cycle.sources.using(using).filter(
            planning_mode=WeeklyPlan.PlanningMode.SYSTEMIC
        ).exists():
            _create_evidence(
                PlanningGovernanceAlarm,
                using=using,
                cycle=cycle,
                source_plan=plan,
                kind=PlanningGovernanceAlarm.Kind.MANUAL_WITHOUT_SYSTEM,
                title="Manual Program created without a System proposal",
                message="System Planning is optional; this informational alarm does not block work.",
                details={"program_number": cycle.program_number},
            )
        return plan


def approve_manual_source(plan_id, *, actor, using="default"):
    assert_can_approve(actor)
    with transaction.atomic(using=using):
        plan = WeeklyPlan.objects.using(using).select_for_update().get(pk=plan_id)
        if (
            plan.governance_cycle_id is None
            or plan.planning_mode != WeeklyPlan.PlanningMode.MANUAL
            or plan.source_state != "active"
        ):
            raise ValidationError("Only a governed Manual source can receive Manual approval.")
        existing = ProgramSourceApproval.objects.using(using).filter(source_plan=plan).first()
        if existing:
            return existing, False
        snapshot = _snapshot_plan(plan, using=using)
        evidence = _create_evidence(
            ProgramSourceApproval,
            using=using,
            source_plan=plan,
            approval_type=ProgramSourceApproval.ApprovalType.MANUAL,
            content_fingerprint=_fingerprint(snapshot),
            content_snapshot=snapshot,
            approved_by=actor,
        )
        WeeklyPlan.objects.using(using).filter(pk=plan.pk)._update_for_governance_service(
            status=WeeklyPlan.Status.APPROVED,
            approved_by=actor,
            approved_at=evidence.approved_at,
        )
        return evidence, True


def record_system_row_decision(
    *, source_plan_id, item_id, decision, actor, snapshot=None, using="default"
):
    assert_can_approve(actor)
    with transaction.atomic(using=using):
        plan = WeeklyPlan.objects.using(using).select_for_update().get(pk=source_plan_id)
        if (
            plan.planning_mode != WeeklyPlan.PlanningMode.SYSTEMIC
            or plan.governance_cycle_id is None
            or plan.source_state != "active"
        ):
            raise ValidationError("Row decisions belong to a governed System source.")
        item = plan.items.using(using).get(pk=item_id)
        latest = plan.system_row_decisions.using(using).filter(item=item).order_by("-sequence").first()
        evidence = _create_evidence(
            SystemProgramRowDecision,
            using=using,
            source_plan=plan,
            item=item,
            sequence=(latest.sequence + 1) if latest else 1,
            decision=decision,
            decision_snapshot=snapshot or {},
            decided_by=actor,
        )
        return evidence


def mark_governance_alarm_viewed(alarm_id, *, user, using="default"):
    return PlanningGovernanceAlarmView.objects.using(using).get_or_create(
        alarm_id=alarm_id, viewed_by=user
    )


def assert_operational_program_is_governed(plan, *, using="default"):
    """Legacy plans pass unchanged; governed production requires Manual approval + Final."""
    if plan.governance_cycle_id is None:
        return
    cycle = WeeklyProgramCycle.objects.using(using).get(pk=plan.governance_cycle_id)
    if cycle.final_plan_id != plan.pk:
        raise ValidationError("Only the Merged/Final Program may be operational.")
    if not _manual_is_approved(cycle, using=using):
        raise ValidationError("Manual approval is required before production may start.")
