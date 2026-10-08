"""Pure Production Counter Target semantics; no persistence or workflow writes."""

from dataclasses import dataclass

from django.db.models import Sum


class RequiredGoodUnavailable(ValueError):
    """The immutable Final-path Good allocation cannot be resolved safely."""


@dataclass(frozen=True)
class CounterTargetSnapshot:
    required_good: int
    current_counter_target: int
    cumulative_total_produced: int
    cumulative_good: int
    counter_remaining: int
    good_shortage: int
    additional_counter_needed: int
    potential_proposed_target: int
    minimum_postproduction_counter_target: int
    existing_overproduction: int
    scrap_compensation_needed: bool
    proposed_target_is_valid: bool


def calculate_counter_target_snapshot(
    *,
    required_good,
    current_counter_target,
    cumulative_total_produced,
    cumulative_good,
):
    """Return deterministic decision support without mutating an effective target."""
    required_good = int(required_good)
    current_counter_target = int(current_counter_target)
    cumulative_total_produced = int(cumulative_total_produced)
    cumulative_good = int(cumulative_good)
    values = {
        "required_good": required_good,
        "current_counter_target": current_counter_target,
        "cumulative_total_produced": cumulative_total_produced,
        "cumulative_good": cumulative_good,
    }
    if any(value < 0 for value in values.values()):
        raise ValueError("Counter Target quantities must not be negative.")
    if cumulative_good > cumulative_total_produced:
        raise ValueError("Cumulative Good cannot exceed cumulative Total Produced.")

    counter_remaining = max(current_counter_target - cumulative_total_produced, 0)
    good_shortage = max(required_good - cumulative_good, 0)
    additional_counter_needed = max(good_shortage - counter_remaining, 0)
    potential_proposed_target = current_counter_target + additional_counter_needed
    minimum_postproduction_counter_target = cumulative_total_produced
    return CounterTargetSnapshot(
        required_good=required_good,
        current_counter_target=current_counter_target,
        cumulative_total_produced=cumulative_total_produced,
        cumulative_good=cumulative_good,
        counter_remaining=counter_remaining,
        good_shortage=good_shortage,
        additional_counter_needed=additional_counter_needed,
        potential_proposed_target=potential_proposed_target,
        minimum_postproduction_counter_target=minimum_postproduction_counter_target,
        existing_overproduction=max(cumulative_total_produced - current_counter_target, 0),
        scrap_compensation_needed=additional_counter_needed > 0,
        proposed_target_is_valid=(
            potential_proposed_target >= minimum_postproduction_counter_target
        ),
    )


def resolve_required_good(execution_path, *, using=None) -> int:
    """Resolve immutable Required Good from Final-path approval evidence.

    Paths with no operational evidence and no Final-program identity are positively
    identified as legacy and retain their immutable initial-target fallback.
    """
    from planning.models import FinalProgramRevision, PathOperationalApproval

    using = using or execution_path._state.db or "default"
    prefetched = getattr(execution_path, "_prefetched_objects_cache", {})
    if (
        "operational_approvals" in prefetched
        and execution_path._state.db == using
    ):
        approvals = sorted(
            prefetched["operational_approvals"],
            key=lambda approval: (
                approval.planned_path_id,
                approval.sequence,
                approval.pk,
            ),
        )
    else:
        approvals = list(
            PathOperationalApproval.objects.using(using)
            .filter(execution_path_id=execution_path.pk)
            .select_related(
                "planned_path__requirement__item__plan",
                "planned_path__requirement__item__plan__final_revision",
            )
            .order_by("planned_path_id", "sequence", "pk")
        )
    if not approvals:
        plan = execution_path.requirement_program.requirement.item.plan
        plan_id = plan.pk
        final_revision_cached = (
            plan._state.db == using
            and "final_revision" in plan._state.fields_cache
        )
        if final_revision_cached:
            try:
                plan.final_revision
            except FinalProgramRevision.DoesNotExist:
                has_final_revision = False
            else:
                has_final_revision = True
        else:
            has_final_revision = FinalProgramRevision.objects.using(using).filter(
                final_plan_id=plan_id
            ).exists()
        if has_final_revision:
            raise RequiredGoodUnavailable(
                "A governed execution path has no immutable path-approval lineage."
            )
        if plan.governance_cycle_id is not None:
            raise RequiredGoodUnavailable(
                "A governed Manual/System source has no authoritative Final-path lineage."
            )
        return int(execution_path.initial_good_target)

    allocations_by_path = {}
    expected_requirement_id = execution_path.requirement_program.requirement_id
    for approval in approvals:
        planned_path = approval.planned_path
        if planned_path.requirement_id != expected_requirement_id:
            raise RequiredGoodUnavailable(
                "Path-approval lineage does not belong to the execution requirement."
            )
        plan = planned_path.requirement.item.plan
        try:
            final_revision = plan.final_revision
        except FinalProgramRevision.DoesNotExist as error:
            raise RequiredGoodUnavailable(
                "Path-approval lineage is not backed by an approved Final revision."
            ) from error
        if final_revision.final_plan_id != plan.pk:
            raise RequiredGoodUnavailable(
                "Path-approval lineage does not belong to its recorded Final program."
            )
        allocation = int(planned_path.planned_share_quantity or 0)
        if allocation <= 0:
            raise RequiredGoodUnavailable(
                "The authoritative Final-path Good allocation is unavailable."
            )
        previous = allocations_by_path.setdefault(planned_path.pk, allocation)
        if previous != allocation:
            raise RequiredGoodUnavailable(
                "Approval evidence for one Final path has conflicting allocations."
            )

    allocations = set(allocations_by_path.values())
    if len(allocations) != 1:
        raise RequiredGoodUnavailable(
            "Different authoritative Final paths have conflicting Good allocations."
        )
    return allocations.pop()


def snapshot_for_execution_path(execution_path):
    """Calculate current decision support for one path; deliberately read-only."""
    totals = execution_path.day_entries.aggregate(
        total_produced=Sum("gross_pieces"),
        total_good=Sum("good_pieces"),
    )
    return calculate_counter_target_snapshot(
        required_good=resolve_required_good(execution_path),
        current_counter_target=execution_path.current_good_target,
        cumulative_total_produced=int(totals["total_produced"] or 0),
        cumulative_good=int(totals["total_good"] or 0),
    )
