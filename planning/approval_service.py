"""Portable, side-effect-free approval candidate validation for Slice 2B1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch

from accounts.models import UserProfile
from catalog.models import MoldMachineCompatibility, PhysicalMold

from .models import PlannedProductionPath, WeeklyPlan, WeeklyPlanLine
from .path_foundation import classify_compatibility, classify_mold_status


class StaleApprovalCandidate(ValidationError):
    pass


@dataclass(frozen=True)
class RequiredDecision:
    planned_path_id: int
    decision_type: str
    condition_snapshot: dict


@dataclass(frozen=True)
class RequirementCandidate:
    requirement_id: int
    target_quantity: int
    assigned_quantity: int
    unassigned_quantity: int
    overassigned_quantity: int
    active_path_ids: tuple[int, ...]


@dataclass(frozen=True)
class ApprovalCandidate:
    plan_id: int
    fingerprint: str
    requirements: tuple[RequirementCandidate, ...]
    required_decisions: tuple[RequiredDecision, ...]


def assert_can_approve(user) -> None:
    """Enforce the existing manager-only approval capability in the backend."""
    profile = (
        UserProfile.objects.filter(user=user).first()
        if user and getattr(user, "is_authenticated", False)
        else None
    )
    if not (
        getattr(user, "is_superuser", False)
        or (profile and profile.can_approve_plans)
    ):
        raise PermissionDenied("فقط مدیر برنامه‌ریزی می‌تواند برنامه را تأیید کند.")


def _date_value(value):
    return value.isoformat() if value is not None else None


def _fingerprint_payload(plan, requirements, *, using):
    """Return approval-relevant content only; timestamps are intentionally excluded."""
    requirement_rows = []
    for requirement in requirements:
        paths = sorted(
            (path for path in requirement.planned_paths.all() if path.is_active),
            key=lambda path: (path.sequence, path.pk),
        )
        requirement_rows.append(
            {
                "requirement_id": requirement.pk,
                "target_quantity": int(requirement.quantity or 0),
                "paths": [
                    {
                        "path_id": path.pk,
                        "machine_id": path.machine_id,
                        "physical_mold_id": path.physical_mold_id,
                        "compatibility_status": classify_compatibility(
                            physical_mold=path.physical_mold,
                            machine=path.machine,
                            using=using,
                        ).status,
                        "physical_mold_status": path.physical_mold.status,
                        "estimated_ready_date": _date_value(
                            path.physical_mold.estimated_ready_date
                        ),
                        "planned_share_quantity": int(path.planned_share_quantity or 0),
                        "planned_start_date": _date_value(path.planned_start_date),
                        "planned_end_date": _date_value(path.planned_end_date),
                        "sequence": int(path.sequence),
                    }
                    for path in paths
                ],
            }
        )
    return {"plan_id": plan.pk, "requirements": sorted(requirement_rows, key=lambda row: row["requirement_id"])}


def calculate_candidate_fingerprint(plan, *, using=None) -> str:
    using = using or plan._state.db or "default"
    requirements = _load_requirements(plan.pk, using=using)
    encoded = json.dumps(
        _fingerprint_payload(plan, requirements, using=using),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_requirements(plan_id, *, using):
    active_paths = (
        PlannedProductionPath.objects.using(using)
        .filter(is_active=True)
        .select_related("machine", "physical_mold", "physical_mold__mold_type")
        .order_by("sequence", "pk")
    )
    return list(
        WeeklyPlanLine.objects.using(using)
        .filter(item__plan_id=plan_id)
        .select_related("item", "item__plan")
        .prefetch_related(Prefetch("planned_paths", queryset=active_paths))
        .order_by("pk")
    )


def validate_approval_candidate(
    plan_or_id,
    *,
    user=None,
    require_approval_permission=False,
    submitted_fingerprint=None,
    using="default",
) -> ApprovalCandidate:
    """Validate future approval content without creating any operational state."""
    if require_approval_permission:
        assert_can_approve(user)
    plan_id = plan_or_id.pk if isinstance(plan_or_id, WeeklyPlan) else plan_or_id
    plan = WeeklyPlan.objects.using(using).get(pk=plan_id)
    requirements = _load_requirements(plan.pk, using=using)
    errors = []
    requirement_candidates = []
    decisions = []

    for requirement in requirements:
        paths = list(requirement.planned_paths.all())
        if not paths:
            errors.append(f"requirement {requirement.pk} has no active planned path")
            continue
        assigned = sum(int(path.planned_share_quantity or 0) for path in paths)
        target = int(requirement.quantity or 0)
        requirement_candidates.append(
            RequirementCandidate(
                requirement_id=requirement.pk,
                target_quantity=target,
                assigned_quantity=assigned,
                unassigned_quantity=max(target - assigned, 0),
                overassigned_quantity=max(assigned - target, 0),
                active_path_ids=tuple(path.pk for path in paths),
            )
        )
        for path in paths:
            if path.planned_start_date > path.planned_end_date:
                errors.append(f"path {path.pk} has an invalid date range")
                continue
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
                errors.append(f"path {path.pk} uses an incompatible mold and machine")
            elif compatibility.status in {
                MoldMachineCompatibility.Status.UNKNOWN,
                MoldMachineCompatibility.Status.NEEDS_REVIEW,
            }:
                decision_type = (
                    "unknown_compatibility"
                    if compatibility.status == MoldMachineCompatibility.Status.UNKNOWN
                    else "needs_review_compatibility"
                )
                decisions.append(
                    RequiredDecision(
                        planned_path_id=path.pk,
                        decision_type=decision_type,
                        condition_snapshot={
                            "compatibility_status": compatibility.status,
                            "machine_id": path.machine_id,
                            "physical_mold_id": path.physical_mold_id,
                        },
                    )
                )
            if path.physical_mold.status == PhysicalMold.Status.UNDER_REPAIR:
                if not path.physical_mold.estimated_ready_date:
                    errors.append(f"path {path.pk} uses an under-repair mold without a ready date")
                elif not mold_status.future_candidate:
                    errors.append(f"path {path.pk} starts before or on the mold ready date")
                else:
                    decisions.append(
                        RequiredDecision(
                            planned_path_id=path.pk,
                            decision_type="under_repair_future_use",
                            condition_snapshot={
                                "mold_status": path.physical_mold.status,
                                "estimated_ready_date": _date_value(
                                    path.physical_mold.estimated_ready_date
                                ),
                                "planned_start_date": _date_value(path.planned_start_date),
                                "physical_mold_id": path.physical_mold_id,
                            },
                        )
                    )
            elif path.physical_mold.status in {
                PhysicalMold.Status.UNAVAILABLE,
                PhysicalMold.Status.INACTIVE,
            }:
                errors.append(f"path {path.pk} uses an unavailable physical mold")

    fingerprint_payload = _fingerprint_payload(plan, requirements, using=using)
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if submitted_fingerprint is not None and submitted_fingerprint != fingerprint:
        raise StaleApprovalCandidate("Approval candidate content has changed.")
    if errors:
        raise ValidationError(errors)
    return ApprovalCandidate(
        plan_id=plan.pk,
        fingerprint=fingerprint,
        requirements=tuple(requirement_candidates),
        required_decisions=tuple(
            sorted(decisions, key=lambda item: (item.planned_path_id, item.decision_type))
        ),
    )


def validate_manager_confirmations(candidate, confirmations, *, user) -> None:
    """Require an explicit manager confirmation for every candidate exception."""
    assert_can_approve(user)
    supplied = {
        (int(item["planned_path_id"]), str(item["decision_type"]))
        for item in confirmations
        if item.get("confirmed") is True
    }
    required = {
        (item.planned_path_id, item.decision_type)
        for item in candidate.required_decisions
    }
    missing = sorted(required - supplied)
    if missing:
        raise ValidationError(
            [f"Missing explicit manager confirmation for path {path_id}: {kind}" for path_id, kind in missing]
        )
