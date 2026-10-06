"""Canonical, transactional Final Program review and merge service."""

from __future__ import annotations

import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import MoldMachineCompatibility, PhysicalMold

from .approval_service import assert_can_approve
from .models import (
    FinalProgramRevision,
    FinalProgramRowProvenance,
    PhysicalMoldReservation,
    PlannedProductionPath,
    ProgramSourceApproval,
    SystemFinalApproval,
    SystemProgramRowDecision,
    WeeklyPlan,
    WeeklyPlanItem,
    WeeklyPlanLine,
    WeeklyProgramCycle,
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _path_snapshot(path):
    return {
        "machine_id": path.machine_id,
        "physical_mold_id": path.physical_mold_id,
        "planned_share_quantity": path.planned_share_quantity,
        "planned_start_date": str(path.planned_start_date),
        "planned_end_date": str(path.planned_end_date),
        "sequence": path.sequence,
        "is_active": path.is_active,
    }


def _item_snapshot(item):
    lines = []
    for line in item.lines.all().order_by("pk"):
        lines.append({
            "line_id": line.pk,
            "production_type_id": line.production_type_id,
            "mold_id": line.mold_id,
            "quantity": line.quantity,
            "cycle": line.cycle,
            "active_cavities": line.active_cavities,
            "paths": [_path_snapshot(path) for path in line.planned_paths.all().order_by("sequence", "pk")],
        })
    return {
        "item_id": item.pk,
        "product_id": item.product_id,
        "subgroup_id": item.subgroup_id,
        "unit_id": item.unit_id,
        "machine_id": item.machine_id,
        "mold_id": item.mold_id,
        "mold_change_weekday": item.mold_change_weekday,
        "mold_change_date": str(item.mold_change_date),
        "active_cavities": item.active_cavities,
        "production_days": item.production_days,
        "sequence": item.sequence,
        "history_alarm": item.history_alarm,
        "lines": lines,
    }


def _source_snapshot(plan, *, using):
    plan = (
        WeeklyPlan.objects.using(using)
        .prefetch_related("items__lines__planned_paths")
        .get(pk=plan.pk)
    )
    return {
        "plan_id": plan.pk,
        "cycle_id": plan.governance_cycle_id,
        "source_state": plan.source_state,
        "items": [_item_snapshot(item) for item in plan.items.all().order_by("sequence", "pk")],
    }


def _latest_decisions(system, *, using):
    latest = {}
    for decision in system.system_row_decisions.using(using).order_by("item_id", "sequence", "pk"):
        latest[decision.item_id] = decision
    return latest


def system_review_fingerprint(system_source_id, *, using="default"):
    system = WeeklyPlan.objects.using(using).get(pk=system_source_id)
    decisions = _latest_decisions(system, using=using)
    review = [
        {
            "item_id": item_id,
            "decision_id": decision.pk,
            "sequence": decision.sequence,
            "decision": decision.decision,
            "snapshot": decision.decision_snapshot,
        }
        for item_id, decision in sorted(decisions.items())
    ]
    return _fingerprint({"source": _source_snapshot(system, using=using), "review": review})


def review_system_row(*, source_plan_id, item_id, decision, actor, modified=None, reason="", using="default"):
    """Append a structured row decision without changing effective Final state."""
    assert_can_approve(actor)
    valid = {choice for choice, _ in SystemProgramRowDecision.Decision.choices}
    if decision not in valid:
        raise ValidationError("Unsupported System row decision.")
    if decision == SystemProgramRowDecision.Decision.MODIFY and not modified:
        raise ValidationError("A modified decision requires the manager's final values.")
    with transaction.atomic(using=using):
        system = WeeklyPlan.objects.using(using).select_for_update().get(pk=source_plan_id)
        if system.planning_mode != WeeklyPlan.PlanningMode.SYSTEMIC or system.source_state != "active":
            raise ValidationError("Only an active governed System source may be reviewed.")
        item = system.items.using(using).get(pk=item_id)
        latest = system.system_row_decisions.using(using).filter(item=item).order_by("-sequence").first()
        evidence = SystemProgramRowDecision(
            source_plan=system,
            item=item,
            sequence=(latest.sequence + 1) if latest else 1,
            decision=decision,
            decision_snapshot={
                "original_system_proposal": _item_snapshot(
                    WeeklyPlanItem.objects.using(using).prefetch_related("lines__planned_paths").get(pk=item.pk)
                ),
                "manager_modified_values": modified or {},
                "reason": reason,
            },
            decided_by=actor,
        )
        evidence._governance_service_create = True
        try:
            evidence.save(using=using, force_insert=True)
        finally:
            del evidence._governance_service_create
        return evidence


def bulk_review_system_rows(*, source_plan_id, decision, actor, modifications=None, reason="", using="default"):
    modifications = modifications or {}
    with transaction.atomic(using=using):
        system = WeeklyPlan.objects.using(using).select_for_update().get(pk=source_plan_id)
        return [
            review_system_row(
                source_plan_id=system.pk,
                item_id=item_id,
                decision=decision,
                actor=actor,
                modified=modifications.get(item_id),
                reason=reason,
                using=using,
            )
            for item_id in system.items.using(using).order_by("pk").values_list("pk", flat=True)
        ]


def _active_mold_ids(item):
    return tuple(sorted({
        path.physical_mold_id
        for line in item.lines.all()
        for path in line.planned_paths.all()
        if path.is_active
    }))


def _snapshot_mold_ids(snapshot):
    return tuple(sorted({
        path["physical_mold_id"]
        for line in snapshot["lines"]
        for path in line["paths"]
        if path.get("is_active", True)
    }))


def _apply_modifications(snapshot, modified):
    result = json.loads(_canonical(snapshot))
    allowed_item = {"machine_id", "mold_id", "mold_change_weekday", "mold_change_date", "active_cavities", "production_days", "sequence"}
    for key in allowed_item:
        if key in modified:
            result[key] = modified[key]
    line_changes = modified.get("lines", {})
    for line in result["lines"]:
        change = line_changes.get(str(line["line_id"]), line_changes.get(line["line_id"], {}))
        for key in ("quantity", "cycle", "active_cavities", "production_type_id", "mold_id"):
            if key in change:
                line[key] = change[key]
        if "paths" in change:
            line["paths"] = change["paths"]
    return result


def _validate_paths(rows, *, using):
    claims = []
    for row in rows:
        for line in row["snapshot"]["lines"]:
            for path in line["paths"]:
                if not path.get("is_active", True):
                    continue
                mold = PhysicalMold.objects.using(using).get(pk=path["physical_mold_id"])
                status = (
                    MoldMachineCompatibility.objects.using(using)
                    .filter(physical_mold_id=mold.pk, machine_id=path["machine_id"])
                    .values_list("status", flat=True)
                    .first()
                )
                if status == MoldMachineCompatibility.Status.INCOMPATIBLE:
                    raise ValidationError("An INCOMPATIBLE mold/machine path cannot be merged.")
                if row["origin"] != FinalProgramRowProvenance.Origin.MANUAL and status != MoldMachineCompatibility.Status.CONFIRMED:
                    raise ValidationError("System-selected paths require CONFIRMED compatibility.")
                start, end = path["planned_start_date"], path["planned_end_date"]
                if start > end:
                    raise ValidationError("A planned path has an invalid date range.")
                for other in claims:
                    if other[0] == mold.pk and other[1] <= end and start <= other[2]:
                        raise ValidationError("A Physical Mold cannot be allocated to overlapping Final paths.")
                if PhysicalMoldReservation.objects.using(using).active_overlaps(
                    physical_mold=mold, start_date=start, end_date=end
                ).exists():
                    raise ValidationError("An active Physical Mold reservation conflicts with the Final result.")
                claims.append((mold.pk, start, end))


def _merge_rows(manual, system, decisions, *, using):
    manual_items = list(manual.items.using(using).prefetch_related("lines__planned_paths").order_by("sequence", "pk"))
    system_items = list(system.items.using(using).prefetch_related("lines__planned_paths").order_by("sequence", "pk")) if system else []
    rows = [{"snapshot": _item_snapshot(item), "manual": item, "system": None, "decision": None, "origin": FinalProgramRowProvenance.Origin.MANUAL} for item in manual_items]
    matched_manual_ids = set()
    accepted_system_molds = set()
    for system_item in system_items:
        decision = decisions.get(system_item.pk)
        if decision is None:
            raise ValidationError("Every System row requires a current manager decision.")
        if decision.decision == SystemProgramRowDecision.Decision.REJECT:
            continue
        original = _item_snapshot(system_item)
        snapshot = original
        if decision.decision == SystemProgramRowDecision.Decision.MODIFY:
            snapshot = _apply_modifications(original, decision.decision_snapshot.get("manager_modified_values") or {})
        system_molds = _snapshot_mold_ids(snapshot)
        if accepted_system_molds.intersection(system_molds):
            raise ValidationError("System proposals contain a duplicate Physical Mold claim.")
        match = next((row for row in rows if row["manual"] and row["manual"].pk not in matched_manual_ids and row["snapshot"]["product_id"] == system_item.product_id and _snapshot_mold_ids(row["snapshot"]) == system_molds and system_molds), None)
        if match:
            match.update(snapshot=snapshot, system=system_item, decision=decision, origin=FinalProgramRowProvenance.Origin.MANUAL_SYSTEM_MODIFIED)
            matched_manual_ids.add(match["manual"].pk)
            accepted_system_molds.update(system_molds)
            continue
        occupied = {mold for row in rows for mold in _snapshot_mold_ids(row["snapshot"])}
        if occupied.intersection(system_molds):
            raise ValidationError("Manual has priority when a System proposal claims the same Physical Mold.")
        rows.append({"snapshot": snapshot, "manual": None, "system": system_item, "decision": decision, "origin": FinalProgramRowProvenance.Origin.SYSTEM})
        accepted_system_molds.update(system_molds)
    _validate_paths(rows, using=using)
    return rows


def _create_final_plan(cycle, rows, *, actor, using):
    final = WeeklyPlan(
        governance_cycle=cycle, program_number=str(cycle.program_number), date=cycle.program_date,
        planning_mode=WeeklyPlan.PlanningMode.FINAL, status=WeeklyPlan.Status.APPROVED,
        created_by=actor, approved_by=actor,
    )
    final._merge_service_create = True
    try:
        final.save(using=using, force_insert=True)
    finally:
        del final._merge_service_create
    for row in rows:
        data = row["snapshot"]
        item = WeeklyPlanItem.objects.using(using).create(
            plan=final, subgroup_id=data["subgroup_id"], unit_id=data["unit_id"], machine_id=data["machine_id"],
            product_id=data["product_id"], mold_id=data["mold_id"], mold_change_weekday=data["mold_change_weekday"],
            mold_change_date=data["mold_change_date"], active_cavities=data["active_cavities"], production_days=data["production_days"],
            sequence=data["sequence"], history_alarm=data["history_alarm"],
        )
        for line_data in data["lines"]:
            line = WeeklyPlanLine.objects.using(using).create(
                item=item, production_type_id=line_data["production_type_id"], mold_id=line_data["mold_id"],
                quantity=line_data["quantity"], cycle=line_data["cycle"], active_cavities=line_data["active_cavities"],
            )
            for path in line_data["paths"]:
                PlannedProductionPath.objects.using(using).create(
                    requirement=line, machine_id=path["machine_id"], physical_mold_id=path["physical_mold_id"],
                    planned_share_quantity=path["planned_share_quantity"], planned_start_date=path["planned_start_date"],
                    planned_end_date=path["planned_end_date"], sequence=path["sequence"], is_active=path.get("is_active", True),
                    created_by=actor, updated_by=actor,
                )
        provenance = FinalProgramRowProvenance(
            final_item=item, manual_source_item=row["manual"], system_source_item=row["system"],
            system_decision=row["decision"], origin=row["origin"],
            provenance_snapshot={"final": data, "decision": row["decision"].decision_snapshot if row["decision"] else None},
        )
        provenance._governance_service_create = True
        try:
            provenance.save(using=using, force_insert=True)
        finally:
            del provenance._governance_service_create
    return final


def _create_revision(*, cycle, manual, system, rows, actor, review_fingerprint, using):
    previous = cycle.final_revisions.using(using).filter(status=FinalProgramRevision.Status.EFFECTIVE).select_for_update().first()
    final = _create_final_plan(cycle, rows, actor=actor, using=using)
    revision = FinalProgramRevision(
        cycle=cycle, revision_number=(previous.revision_number + 1 if previous else 1), final_plan=final,
        manual_source=manual, system_source=system, supersedes=previous, status=FinalProgramRevision.Status.EFFECTIVE,
        source_review_fingerprint=review_fingerprint,
        result_fingerprint=_fingerprint([row["snapshot"] for row in rows]), approved_by=actor,
    )
    revision._merge_service_create = True
    try:
        if previous:
            FinalProgramRevision.objects.using(using).filter(pk=previous.pk)._update_for_merge_service(
                status=FinalProgramRevision.Status.SUPERSEDED,
                superseded_at=timezone.now(),
            )
            WeeklyPlan.objects.using(using).filter(pk=previous.final_plan_id)._update_for_governance_service(source_state="history")
        revision.save(using=using, force_insert=True)
        WeeklyProgramCycle.objects.using(using).filter(pk=cycle.pk).update(final_plan=final)
    finally:
        del revision._merge_service_create
    return revision


def create_manual_final(*, cycle_id, actor, using="default"):
    assert_can_approve(actor)
    with transaction.atomic(using=using):
        cycle = WeeklyProgramCycle.objects.using(using).select_for_update().get(pk=cycle_id)
        manual = cycle.sources.using(using).select_for_update().get(planning_mode=WeeklyPlan.PlanningMode.MANUAL)
        approval = ProgramSourceApproval.objects.using(using).filter(source_plan=manual, approval_type=ProgramSourceApproval.ApprovalType.MANUAL).first()
        if approval is None:
            raise ValidationError("Manual approval is required before Final creation.")
        from .program_governance import _fingerprint as approval_fingerprint
        from .program_governance import _snapshot_plan as approval_snapshot
        if approval.content_fingerprint != approval_fingerprint(
            approval_snapshot(manual, using=using)
        ):
            raise ValidationError("The approved Manual source content no longer matches its approval.")
        existing = cycle.final_revisions.using(using).filter(status=FinalProgramRevision.Status.EFFECTIVE, system_source__isnull=True, source_review_fingerprint=approval.content_fingerprint).first()
        if existing:
            return existing, False
        rows = _merge_rows(manual, None, {}, using=using)
        return _create_revision(cycle=cycle, manual=manual, system=None, rows=rows, actor=actor, review_fingerprint=approval.content_fingerprint, using=using), True


def approve_system_final(*, system_source_id, actor, expected_review_fingerprint, using="default"):
    assert_can_approve(actor)
    try:
        with transaction.atomic(using=using):
            system_identity = WeeklyPlan.objects.using(using).only("governance_cycle_id").get(pk=system_source_id)
            cycle = WeeklyProgramCycle.objects.using(using).select_for_update().get(pk=system_identity.governance_cycle_id)
            system = WeeklyPlan.objects.using(using).select_for_update().get(pk=system_source_id)
            if system.planning_mode != WeeklyPlan.PlanningMode.SYSTEMIC or system.source_state != "active":
                raise ValidationError("Archived or ineligible System sources cannot receive Final Approval.")
            manual = cycle.sources.using(using).select_for_update().get(planning_mode=WeeklyPlan.PlanningMode.MANUAL)
            if not ProgramSourceApproval.objects.using(using).filter(source_plan=manual, approval_type=ProgramSourceApproval.ApprovalType.MANUAL).exists():
                raise ValidationError("Manual approval is required before System Final Approval.")
            current_fingerprint = system_review_fingerprint(system.pk, using=using)
            if current_fingerprint != expected_review_fingerprint:
                raise ValidationError("The System review is stale and must be reviewed again.")
            existing = SystemFinalApproval.objects.using(using).filter(system_source=system).first()
            if existing:
                if existing.review_fingerprint != current_fingerprint:
                    raise ValidationError("This System source already has different Final Approval evidence.")
                return existing.final_revision, False
            decisions = _latest_decisions(system, using=using)
            rows = _merge_rows(manual, system, decisions, using=using)
            revision = _create_revision(cycle=cycle, manual=manual, system=system, rows=rows, actor=actor, review_fingerprint=current_fingerprint, using=using)
            source_approval = ProgramSourceApproval(
                source_plan=system, approval_type=ProgramSourceApproval.ApprovalType.SYSTEM_FINAL,
                content_fingerprint=current_fingerprint, content_snapshot={"review_fingerprint": current_fingerprint}, approved_by=actor,
            )
            source_approval._governance_service_create = True
            final_approval = SystemFinalApproval(
                cycle=cycle, system_source=system, review_fingerprint=current_fingerprint,
                final_revision=revision, approved_by=actor,
            )
            final_approval._governance_service_create = True
            try:
                source_approval.save(using=using, force_insert=True)
                final_approval.save(using=using, force_insert=True)
            finally:
                del source_approval._governance_service_create
                del final_approval._governance_service_create
            return revision, True
    except IntegrityError as error:
        raise ValidationError("Concurrent Final Approval was safely rejected; reload the cycle.") from error
