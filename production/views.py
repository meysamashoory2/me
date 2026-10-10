import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.db.models import Prefetch, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import get_profile

from .forms import (
    DayEntryForm,
    PipeProductionForm,
    ProgramStartForm,
    ProgramStatusForm,
    StoppageFormSetPipe,
    TargetRevisionDecisionForm,
    TargetRevisionRequestForm,
    FullStopForm,
    GovernedProductionEntryForm,
    ResumeExecutionForm,
    ScrapCompensationRequestForm,
    StatisticsCompletedForm,
    TemporaryStopForm,
    pipe_field_map,
)
from .execution_lifecycle import (
    complete_execution_statistics,
    full_stop_execution_path,
    preview_execution_closeout,
    resume_execution_path,
    temporary_stop_execution_path,
)
from .target_revision_service import (
    decide_target_revision,
    request_scrap_compensation,
    request_target_revision,
)
from .target_semantics import (
    RequiredGoodUnavailable,
    calculate_counter_target_snapshot,
    resolve_required_good,
)
from .models import ProductionExecutionPath, ProductionTargetRevision
from .path_execution import register_path_production


def _governed_path_queryset():
    from planning.models import PathOperationalApproval, PhysicalMoldReservation

    approvals = PathOperationalApproval.objects.select_related(
        "planned_path__requirement__item__plan",
        "planned_path__requirement__item__plan__final_revision",
        "actor",
    ).order_by("planned_path_id", "sequence", "pk")
    reservations = PhysicalMoldReservation.objects.select_related(
        "physical_mold", "machine"
    ).order_by("-activated_at", "-pk")
    return (
        ProductionExecutionPath.objects.select_related(
            "requirement_program__requirement__item__plan__final_revision",
            "requirement_program__requirement__item__product",
            "machine__unit",
            "physical_mold__mold_type",
        )
        .prefetch_related(
            Prefetch("operational_approvals", queryset=approvals),
            Prefetch("reservation_snapshots", queryset=reservations),
        )
        .annotate(
            ui_gross=Coalesce(Sum("day_entries__gross_pieces"), 0),
            ui_good=Coalesce(Sum("day_entries__good_pieces"), 0),
            ui_scrap=Coalesce(Sum("day_entries__scrap_quantity"), 0),
        )
    )


def _path_read_model(path):
    gross = int(path.ui_gross or 0)
    good = int(path.ui_good or 0)
    scrap = int(path.ui_scrap or 0)
    try:
        required_good = resolve_required_good(path)
        metrics = calculate_counter_target_snapshot(
            required_good=required_good,
            current_counter_target=path.current_good_target,
            cumulative_total_produced=gross,
            cumulative_good=good,
        )
        lineage_error = ""
    except (RequiredGoodUnavailable, ValueError) as error:
        metrics = None
        lineage_error = str(error)
    reservations = list(path.reservation_snapshots.all())
    approvals = list(path.operational_approvals.all())
    active_reservations = [
        item for item in reservations if item.status == item.Status.ACTIVE
    ]
    if len(active_reservations) == 1:
        reservation = active_reservations[0]
        reservation_state = "single"
    elif len(active_reservations) > 1:
        reservation = None
        reservation_state = "multiple"
    else:
        reservation = None
        reservation_state = "none"
    if lineage_error:
        lineage_classification = "invalid"
    elif approvals:
        lineage_classification = "governed_final"
    else:
        lineage_classification = "legacy_compatible"
    return {
        "path": path,
        "metrics": metrics,
        "gross": gross,
        "good": good,
        "scrap": scrap,
        "lineage_error": lineage_error,
        "reservation": reservation,
        "active_reservations": active_reservations,
        "reservation_state": reservation_state,
        "approvals": approvals,
        "lineage_classification": lineage_classification,
    }


@login_required
def execution_path_list(request):
    queryset = _governed_path_queryset().order_by("status", "machine_id", "pk")
    selected_status = (request.GET.get("status") or "").strip()
    selected_machine = (request.GET.get("machine") or "").strip()
    valid_statuses = {value for value, _label in ProductionExecutionPath.Status.choices}
    if selected_status in valid_statuses:
        queryset = queryset.filter(status=selected_status)
    else:
        selected_status = ""
    if selected_machine.isdigit():
        queryset = queryset.filter(machine_id=int(selected_machine))
    else:
        selected_machine = ""
    from django.core.paginator import Paginator

    page_obj = Paginator(queryset, 50).get_page(request.GET.get("page"))
    rows = [_path_read_model(path) for path in page_obj.object_list]
    from catalog.models import Machine

    return render(
        request,
        "production/execution_path_list.html",
        {
            "rows": rows,
            "page_obj": page_obj,
            "status_choices": ProductionExecutionPath.Status.choices,
            "machines": Machine.objects.filter(
                production_execution_paths__isnull=False
            ).distinct().select_related("unit").order_by("unit_id", "number"),
            "selected_status": selected_status,
            "selected_machine": selected_machine,
        },
    )


@login_required
def execution_path_detail(request, path_id):
    path = get_object_or_404(_governed_path_queryset(), pk=path_id)
    row = _path_read_model(path)
    profile = get_profile(request.user)
    has_operation_permission = bool(profile and profile.can_enter_data)
    has_manager_permission = bool(profile and profile.can_approve_plans)
    governed_final = row["lineage_classification"] == "governed_final"
    closeout_preview = None
    closeout_preview_error = ""
    if (
        has_manager_permission
        and governed_final
        and path.status
        in {
            ProductionExecutionPath.Status.RUNNING,
            ProductionExecutionPath.Status.TEMPORARY_STOP,
        }
    ):
        try:
            closeout_preview = preview_execution_closeout(
                path, actor=request.user
            )
        except (ValidationError, RequiredGoodUnavailable) as error:
            closeout_preview_error = str(error)
    row.update(
        {
            "entries": path.day_entries.select_related("created_by").order_by("date", "pk"),
            "events": path.events.select_related("actor").order_by("occurred_at", "pk"),
            "target_revisions": path.target_revisions.select_related(
                "requester", "approver", "reason", "decision", "decision__decided_by"
            ).order_by("requested_at", "pk"),
            "deviations": path.deviations.select_related(
                "reason", "actor", "target_revision", "target_decision", "day_entry"
            ).order_by("created_at", "pk"),
            "reservations": path.reservation_snapshots.select_related(
                "physical_mold", "machine"
            ).order_by("-activated_at", "-pk"),
            "has_operation_permission": has_operation_permission,
            "can_operate_path": (
                has_operation_permission
                and governed_final
            ),
            "can_manage_path": has_manager_permission and governed_final,
            "can_request_target": (
                has_operation_permission
                and governed_final
                and path.status
                in {
                    ProductionExecutionPath.Status.RUNNING,
                    ProductionExecutionPath.Status.TEMPORARY_STOP,
                }
            ),
            "closeout_preview": closeout_preview,
            "closeout_preview_error": closeout_preview_error,
            "production_entry_form": GovernedProductionEntryForm(),
            "temporary_stop_form": TemporaryStopForm(),
            "resume_form": ResumeExecutionForm(),
            "target_request_form": TargetRevisionRequestForm(),
            "scrap_compensation_form": ScrapCompensationRequestForm(),
            "target_decision_form": TargetRevisionDecisionForm(),
            "full_stop_form": FullStopForm(),
            "statistics_completed_form": StatisticsCompletedForm(),
        }
    )
    return render(request, "production/execution_path_detail.html", row)


def _detail_ui_requested(request):
    return request.POST.get("ui_context") == "execution_path_detail"


def _form_error_messages(form):
    return [
        f"{form.fields.get(field).label if field in form.fields else 'فرم'}: {message}"
        for field, errors in form.errors.items()
        for message in errors
    ]


def _redirect_with_errors(request, path_id, errors):
    for error in errors:
        messages.error(request, error)
    return redirect("execution_path_detail", path_id=path_id)


def _validation_messages(error):
    return error.messages if isinstance(error, ValidationError) else [str(error)]


@login_required
@require_POST
def governed_target_revision_request(request, path_id):
    form = TargetRevisionRequestForm(request.POST)
    if not form.is_valid():
        return _redirect_with_errors(request, path_id, _form_error_messages(form))
    try:
        request_target_revision(
            path_id,
            requested_target=form.cleaned_data["requested_target"],
            reason=form.cleaned_data["reason"],
            source=ProductionTargetRevision.Source.MANUAL,
            note=form.cleaned_data["note"],
            actor=request.user,
            require_governed_lineage=True,
        )
    except (ValidationError, RequiredGoodUnavailable) as error:
        return _redirect_with_errors(request, path_id, _validation_messages(error))
    messages.success(request, "درخواست تغییر هدف شمارنده برای تصمیم مدیر ثبت شد.")
    return redirect("execution_path_detail", path_id=path_id)


@login_required
@require_POST
def governed_scrap_compensation_request(request, path_id):
    form = ScrapCompensationRequestForm(request.POST)
    if not form.is_valid():
        return _redirect_with_errors(request, path_id, _form_error_messages(form))
    try:
        request_scrap_compensation(
            path_id,
            actor=request.user,
            note=form.cleaned_data["note"],
            require_governed_lineage=True,
        )
    except (ValidationError, RequiredGoodUnavailable) as error:
        return _redirect_with_errors(request, path_id, _validation_messages(error))
    messages.success(
        request,
        "درخواست جبران ضایعات برای تصمیم مدیر ثبت شد؛ هدف هنوز تغییر نکرده است.",
    )
    return redirect("execution_path_detail", path_id=path_id)


@login_required
@require_POST
def governed_target_revision_decide(request, request_id):
    revision = get_object_or_404(
        ProductionTargetRevision.objects.only("execution_path_id"), pk=request_id
    )
    path_id = revision.execution_path_id
    form = TargetRevisionDecisionForm(request.POST)
    if not form.is_valid():
        return _redirect_with_errors(request, path_id, _form_error_messages(form))
    try:
        decide_target_revision(
            request_id,
            outcome=form.cleaned_data["outcome"],
            decided_target=form.cleaned_data.get("decided_target"),
            note=form.cleaned_data["note"],
            actor=request.user,
            require_governed_lineage=True,
        )
    except (ValidationError, RequiredGoodUnavailable) as error:
        return _redirect_with_errors(request, path_id, _validation_messages(error))
    messages.success(request, "تصمیم مدیر با حفظ سند درخواست ثبت شد.")
    return redirect("execution_path_detail", path_id=path_id)


@login_required
@require_POST
def governed_execution_path_full_stop(request, path_id):
    form = FullStopForm(request.POST)
    if not form.is_valid():
        return _redirect_with_errors(request, path_id, _form_error_messages(form))
    try:
        full_stop_execution_path(
            path_id,
            actor=request.user,
            shortfall_reason=form.cleaned_data.get("shortfall_reason"),
            shortfall_note=form.cleaned_data["shortfall_note"],
            overproduction_reason=form.cleaned_data.get("overproduction_reason"),
            overproduction_note=form.cleaned_data["overproduction_note"],
            require_governed_lineage=True,
        )
    except (ValidationError, RequiredGoodUnavailable) as error:
        return _redirect_with_errors(request, path_id, _validation_messages(error))
    messages.success(request, "توقف کامل ثبت و رزرو قالب متعلق به این مسیر آزاد شد.")
    return redirect("execution_path_detail", path_id=path_id)


@login_required
@require_POST
def governed_execution_path_statistics_complete(request, path_id):
    form = StatisticsCompletedForm(request.POST)
    if not form.is_valid():
        return _redirect_with_errors(request, path_id, _form_error_messages(form))
    try:
        complete_execution_statistics(
            path_id,
            actor=request.user,
            note=form.cleaned_data["note"],
            require_governed_lineage=True,
        )
    except (ValidationError, RequiredGoodUnavailable) as error:
        return _redirect_with_errors(request, path_id, _validation_messages(error))
    messages.success(request, "تکمیل نهایی آمار تولید با موفقیت ثبت شد.")
    return redirect("execution_path_detail", path_id=path_id)


@login_required
@require_POST
def execution_path_entry_create(request, path_id):
    path = get_object_or_404(ProductionExecutionPath, pk=path_id)
    form = GovernedProductionEntryForm(request.POST)
    if not form.is_valid():
        return _redirect_with_errors(request, path_id, _form_error_messages(form))
    try:
        entry = register_path_production(
            path,
            date=form.cleaned_data["date"],
            shots=form.cleaned_data["shots"],
            active_cavities=form.cleaned_data["active_cavities"],
            scrap_pieces=form.cleaned_data["scrap_pieces"],
            description=form.cleaned_data["description"],
            actor=request.user,
            require_governed_lineage=True,
        )
    except ValidationError as error:
        return _redirect_with_errors(request, path_id, error.messages)
    messages.success(
        request,
        f"آمار تولید ثبت شد: ناخالص {entry.gross_pieces}، خوب {entry.good_pieces}، ضایعات {entry.scrap_quantity}.",
    )
    return redirect("execution_path_detail", path_id=path_id)


def _form_error_response(form):
    return JsonResponse({"errors": form.errors.get_json_data()}, status=400)


def _validation_error_response(error):
    messages = error.messages if isinstance(error, ValidationError) else [str(error)]
    return JsonResponse({"errors": messages}, status=400)


@login_required
@require_POST
def target_revision_request(request, path_id):
    form = TargetRevisionRequestForm(request.POST)
    if not form.is_valid():
        return _form_error_response(form)
    try:
        revision = request_target_revision(
            path_id,
            requested_target=form.cleaned_data["requested_target"],
            reason=form.cleaned_data["reason"],
            source=ProductionTargetRevision.Source.MANUAL,
            note=form.cleaned_data["note"],
            actor=request.user,
        )
    except ValidationError as error:
        return _validation_error_response(error)
    return JsonResponse({"request_id": revision.pk, "state": "pending"}, status=201)


@login_required
@require_POST
def target_revision_decide(request, request_id):
    form = TargetRevisionDecisionForm(request.POST)
    if not form.is_valid():
        return _form_error_response(form)
    try:
        result = decide_target_revision(
            request_id,
            outcome=form.cleaned_data["outcome"],
            decided_target=form.cleaned_data.get("decided_target"),
            note=form.cleaned_data["note"],
            actor=request.user,
        )
    except ValidationError as error:
        return _validation_error_response(error)
    return JsonResponse(
        {
            "request_id": result.request.pk,
            "decision_id": result.decision.pk,
            "outcome": result.decision.outcome,
        }
    )


@login_required
@require_POST
def target_revision_scrap_request(request, path_id):
    form = ScrapCompensationRequestForm(request.POST)
    if not form.is_valid():
        return _form_error_response(form)
    try:
        revision = request_scrap_compensation(
            path_id,
            actor=request.user,
            note=form.cleaned_data["note"],
        )
    except (ValidationError, RequiredGoodUnavailable) as error:
        return _validation_error_response(error)
    return JsonResponse({"request_id": revision.pk, "state": "pending"}, status=201)


@login_required
@require_POST
def execution_path_temporary_stop(request, path_id):
    form = TemporaryStopForm(request.POST)
    if not form.is_valid():
        if _detail_ui_requested(request):
            return _redirect_with_errors(request, path_id, _form_error_messages(form))
        return _form_error_response(form)
    try:
        event = temporary_stop_execution_path(
            path_id,
            actor=request.user,
            reason=form.cleaned_data["reason"],
            note=form.cleaned_data["note"],
        )
    except ValidationError as error:
        if _detail_ui_requested(request):
            return _redirect_with_errors(request, path_id, error.messages)
        return _validation_error_response(error)
    if _detail_ui_requested(request):
        messages.success(request, "مسیر تولید با موفقیت به‌صورت موقت متوقف شد.")
        return redirect("execution_path_detail", path_id=path_id)
    return JsonResponse({"event_id": event.pk, "status": event.new_status})


@login_required
@require_POST
def execution_path_resume(request, path_id):
    form = ResumeExecutionForm(request.POST)
    if not form.is_valid():
        if _detail_ui_requested(request):
            return _redirect_with_errors(request, path_id, _form_error_messages(form))
        return _form_error_response(form)
    try:
        event = resume_execution_path(
            path_id, actor=request.user, note=form.cleaned_data["note"]
        )
    except ValidationError as error:
        if _detail_ui_requested(request):
            return _redirect_with_errors(request, path_id, error.messages)
        return _validation_error_response(error)
    if _detail_ui_requested(request):
        messages.success(request, "تولید این مسیر با موفقیت از سر گرفته شد.")
        return redirect("execution_path_detail", path_id=path_id)
    return JsonResponse({"event_id": event.pk, "status": event.new_status})


@login_required
@require_POST
def execution_path_full_stop(request, path_id):
    form = FullStopForm(request.POST)
    if not form.is_valid():
        return _form_error_response(form)
    try:
        result = full_stop_execution_path(
            path_id,
            actor=request.user,
            shortfall_reason=form.cleaned_data.get("shortfall_reason"),
            shortfall_note=form.cleaned_data["shortfall_note"],
            overproduction_reason=form.cleaned_data.get("overproduction_reason"),
            overproduction_note=form.cleaned_data["overproduction_note"],
        )
    except (ValidationError, RequiredGoodUnavailable) as error:
        return _validation_error_response(error)
    return JsonResponse(
        {
            "event_id": result.event.pk,
            "status": result.path.status,
            "reservation_id": result.reservation.pk,
        }
    )


@login_required
@require_POST
def execution_path_statistics_complete(request, path_id):
    form = StatisticsCompletedForm(request.POST)
    if not form.is_valid():
        return _form_error_response(form)
    try:
        event = complete_execution_statistics(
            path_id, actor=request.user, note=form.cleaned_data["note"]
        )
    except ValidationError as error:
        return _validation_error_response(error)
    return JsonResponse({"event_id": event.pk, "status": event.new_status})
from .models import (
    PipeProduction,
    ProductionDayEntry,
    ProductionHistoryRecord,
    ProductionProgram,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile(request):
    return get_profile(request.user)


def _require_data_entry(request):
    profile = _profile(request)
    if not profile or not profile.can_enter_data:
        raise PermissionDenied("شما اجازه ثبت داده ندارید.")
    return profile


def program_totals(program):
    agg = program.entries.aggregate(
        produced=Sum("produced_quantity"),
        planned=Sum("planned_quantity"),
        seconds=Sum("active_seconds"),
    )
    produced = agg["produced"] or 0
    planned = agg["planned"] or 0
    seconds = agg["seconds"] or 0
    return {
        "produced": produced,
        "planned": planned,
        "deviation": planned - produced,
        "hours": round(seconds / 3600, 1),
    }


# ---------------------------------------------------------------------------
# ثبت تولید روزانه (plan-driven for fittings) + pipes
# ---------------------------------------------------------------------------

@login_required
def production_list(request):
    # Daily production is merged into the برنامه‌های تولید hub.
    return redirect("program_list")


@login_required
def entry_create(request, program_pk):
    _require_data_entry(request)
    program = get_object_or_404(
        ProductionProgram.objects.select_related("item__product", "item__machine__unit"),
        pk=program_pk,
    )
    if program.status == ProductionProgram.Status.TEMP_STOP:
        messages.error(request, "برنامه در حالت «توقف موقت» است؛ برای ثبت آمار ابتدا آن را از سر بگیرید.")
        return redirect("production_list")
    if program.status != ProductionProgram.Status.RUNNING:
        messages.error(request, "این برنامه در حال تولید نیست.")
        return redirect("production_list")

    if request.method == "POST":
        form = DayEntryForm(request.POST, program=program)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.program = program
            entry.created_by = request.user
            entry.save()
            messages.success(request, "آمار تولید ثبت شد.")
            return redirect("entry_create", program_pk=program.pk)
    else:
        form = DayEntryForm(program=program)

    return render(request, "production/entry_form.html", {
        "form": form, "program": program, "totals": program_totals(program),
        "entries": program.entries.select_related("deviation_reason").all(),
        "mode": "create",
    })


@login_required
def entry_edit(request, pk):
    entry = get_object_or_404(
        ProductionDayEntry.objects.select_related("program__item").filter(program__isnull=False),
        pk=pk,
    )
    profile = _profile(request)
    if not profile or not (profile.is_manager or entry.created_by_id == request.user.id or profile.can_edit_others):
        raise PermissionDenied("فقط مدیر یا ثبت‌کننده می‌تواند ویرایش کند.")
    program = entry.program
    if request.method == "POST":
        form = DayEntryForm(request.POST, instance=entry, program=program)
        if form.is_valid():
            form.save()
            messages.success(request, "آمار ویرایش شد.")
            return redirect("entry_create", program_pk=program.pk)
    else:
        form = DayEntryForm(instance=entry, program=program)
    return render(request, "production/entry_form.html", {
        "form": form, "program": program, "totals": program_totals(program),
        "entries": program.entries.select_related("deviation_reason").all(),
        "mode": "edit",
    })


# ---------------------------------------------------------------------------
# برنامه‌های تولید + تعیین وضعیت (state machine)
# ---------------------------------------------------------------------------

@login_required
def program_list(request):
    """Hub with two tabs: دستگاه تزریق (fitting programs) and خط لوله (pipes).

    Injection tab shows awaiting (to start), running, and temporarily stopped
    programs. Finished programs appear only under «سوابق تولید».

    Light backfill: push active Excel archive rows that are still missing a live
    ProductionProgram so ثبت و کنترل تولید stays in sync after imports.
    """
    import logging

    from production.sync import ensure_running_history_in_production

    try:
        ensure_running_history_in_production(user=request.user)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "ensure_running_history_in_production failed on hub load"
        )

    profile = _profile(request)
    programs = list(
        ProductionProgram.objects.select_related(
            "item__product", "item__machine__unit", "item__plan"
        )
        .prefetch_related("item__lines")
        .filter(
            status__in=[
                ProductionProgram.Status.AWAITING,
                ProductionProgram.Status.RUNNING,
                ProductionProgram.Status.TEMP_STOP,
            ]
        )
        .annotate(
            agg_produced=Coalesce(Sum("entries__produced_quantity"), 0),
            agg_planned=Coalesce(Sum("entries__planned_quantity"), 0),
            agg_seconds=Coalesce(Sum("entries__active_seconds"), 0),
        )
    )
    from core.natsort import natural_key

    programs.sort(
        key=lambda p: (
            -(p.item.plan.date.toordinal() if p.item.plan_id and p.item.plan.date else 0),
            p.item.machine.unit.number if p.item.machine_id else 0,
            natural_key(p.item.machine.number if p.item.machine_id else ""),
            p.item.sequence or 0,
        )
    )

    def _totals_from_prog(p):
        produced = int(getattr(p, "agg_produced", 0) or 0)
        planned = int(getattr(p, "agg_planned", 0) or 0)
        seconds = int(getattr(p, "agg_seconds", 0) or 0)
        return {
            "produced": produced,
            "planned": planned,
            "deviation": planned - produced,
            "hours": round(seconds / 3600, 1),
        }

    rows = [{"program": p, "totals": _totals_from_prog(p)} for p in programs]
    from .conflicts import conflict_counts

    counts = conflict_counts()

    pipes = PipeProduction.objects.select_related("unit", "line", "product", "created_by")[:50]
    for rec in pipes:
        rec.can_edit = bool(profile and profile.can_edit_record(rec))

    active_tab = request.GET.get("tab", "injection")
    from reports.form_purposes import PURPOSE_PRODUCTION, forms_for_purpose
    import json as _json
    forms_production = [
        {"id": f.pk, "number": f.number, "title": f.title}
        for f in forms_for_purpose(request.user, PURPOSE_PRODUCTION)
    ]
    return render(request, "production/hub.html",
                  {
                      "rows": rows,
                      "pipes": pipes,
                      "profile": profile,
                      "active_tab": active_tab,
                      "forms_production": forms_production,
                      "forms_production_json": _json.dumps(forms_production, ensure_ascii=False),
                      "conflict_count": counts.get("total", 0),
                      "duplicate_count": counts.get("duplicate", 0),
                      "precedence_count": counts.get("precedence", 0),
                  })

@login_required
def production_history(request):
    """All planning/production programs + Excel archives, sorted naturally.

    Sync/conflict checks run during Excel transfer/update — not on every page load.
    Conflict details open on a dedicated page so large lists do not bury the table.
    """
    from .conflicts import collect_in_production_conflicts, history_conflict_uids
    from .history import build_history_rows

    profile = _profile(request)
    rows = build_history_rows()
    conflict_uids = history_conflict_uids()
    for row in rows:
        uid = str(row.get("change_uid") or row.get("unique_code") or "").strip()
        if uid in ("—", "-"):
            uid = ""
        row["has_conflict"] = bool(uid and uid in conflict_uids)
    conflicts = collect_in_production_conflicts()
    from .conflicts import conflict_counts

    counts = conflict_counts()
    return render(
        request,
        "production/history.html",
        {
            "rows": rows,
            "profile": profile,
            "production_conflicts": conflicts,
            "conflict_count": counts.get("total", len(conflicts)),
            "ok_count": sum(1 for r in rows if not r.get("has_conflict")),
            "conflict_row_count": sum(1 for r in rows if r.get("has_conflict")),
        },
    )


@login_required
def production_conflicts(request):
    """Conflict review hub — دو دسته قالب تکراری و تقدم/تاخر."""
    profile = _profile(request)
    if not profile or not profile.can_view_conflicts:
        raise PermissionDenied("اجازه مشاهده تداخل‌ها را ندارید.")
    from .conflicts import KIND_DUPLICATE, KIND_PRECEDENCE, collect_all_conflicts, kind_label

    groups = collect_all_conflicts()
    return render(
        request,
        "production/conflicts.html",
        {
            "profile": profile,
            "can_resolve": profile.can_resolve_conflicts,
            "duplicate_groups": groups[KIND_DUPLICATE],
            "precedence_groups": groups[KIND_PRECEDENCE],
            "duplicate_count": len(groups[KIND_DUPLICATE]),
            "precedence_count": len(groups[KIND_PRECEDENCE]),
            "kind_duplicate": KIND_DUPLICATE,
            "kind_precedence": KIND_PRECEDENCE,
            "kind_label": kind_label,
        },
    )


@login_required
def production_conflicts_kind(request, kind: str):
    """Detail list for one conflict category with in-page fix dialogs."""
    profile = _profile(request)
    if not profile or not profile.can_view_conflicts:
        raise PermissionDenied("اجازه مشاهده تداخل‌ها را ندارید.")
    from catalog.models import MoldOption
    from .conflicts import (
        KIND_DUPLICATE,
        KIND_PRECEDENCE,
        collect_all_conflicts,
        kind_label,
    )

    if kind not in (KIND_DUPLICATE, KIND_PRECEDENCE):
        return redirect("production_conflicts")
    groups = [g.as_dict() for g in collect_all_conflicts()[kind]]
    return render(
        request,
        "production/conflicts_kind.html",
        {
            "profile": profile,
            "can_resolve": profile.can_resolve_conflicts,
            "kind": kind,
            "kind_label": kind_label(kind),
            "groups": groups,
            "group_count": len(groups),
            "mold_options": MoldOption.objects.filter(is_active=True).order_by("order", "label"),
        },
    )


@login_required
def production_conflict_resolve(request):
    """Apply manager-only conflict fixes to live + archive + planning."""
    profile = _profile(request)
    if not profile or not profile.can_resolve_conflicts:
        raise PermissionDenied("فقط مدیر برنامه‌ریزی می‌تواند تداخل را رفع کند.")
    if request.method != "POST":
        return redirect("production_conflicts")

    from .conflict_resolve import apply_conflict_fix

    result = apply_conflict_fix(request.POST, user=request.user)
    if result.get("ok"):
        messages.success(request, result.get("message") or "تغییرات اعمال شد.")
    else:
        messages.error(request, result.get("message") or "اعمال تغییرات ممکن نشد.")
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/"):
        return redirect(next_url)
    kind = request.POST.get("conflict_kind") or ""
    if kind:
        return redirect("production_conflicts_kind", kind=kind)
    return redirect("production_conflicts")


@login_required
def production_history_detail(request, pk):
    """Detail of one live production program with per-entry documents."""
    from .history import entry_detail_rows, history_row_from_program, program_summary_text

    profile = _profile(request)
    program = get_object_or_404(
        ProductionProgram.objects.select_related(
            "item__product", "item__machine__unit", "item__plan", "mold"
        ).prefetch_related("item__lines", "entries__deviation_reason"),
        pk=pk,
    )
    row = history_row_from_program(program)
    return render(
        request,
        "production/history_detail.html",
        {
            "profile": profile,
            "row": row,
            "program": program,
            "summary": program_summary_text(program),
            "entries": entry_detail_rows(program),
            "totals": program_totals(program),
        },
    )


@login_required
def production_history_archive_detail(request, pk):
    """Detail for an Excel-imported history archive row."""
    from .history import (
        archive_summary_text,
        entry_detail_rows_from_archive,
        history_row_from_archive,
    )

    profile = _profile(request)
    rec = get_object_or_404(ProductionHistoryRecord, pk=pk)
    row = history_row_from_archive(rec)
    entries = entry_detail_rows_from_archive(rec)
    return render(
        request,
        "production/history_detail.html",
        {
            "profile": profile,
            "row": row,
            "program": None,
            "summary": archive_summary_text(rec),
            "entries": entries,
            "totals": {
                "produced": row["actual_qty"],
                "planned": row["planned_qty"],
                "deviation": (row["planned_qty"] or 0) - (row["actual_qty"] or 0),
                "hours": 0,
            },
        },
    )


ACTIVE_STATUSES = [ProductionProgram.Status.RUNNING, ProductionProgram.Status.TEMP_STOP]


@login_required
def program_status(request, pk):
    program = get_object_or_404(
        ProductionProgram.objects.select_related("item__product", "item__machine__unit"), pk=pk
    )
    profile = _profile(request)
    if not profile or not profile.can_enter_data:
        raise PermissionDenied("اجازه تعیین وضعیت ندارید.")

    from .conflicts import evaluate_status_change_block

    action = request.POST.get("action") or request.GET.get("action") or "auto"
    status = program.status
    partial = request.GET.get("partial") == "1"
    base_template = "production/_status_dialog.html" if partial else "production/program_status.html"

    # Manager-only re-open of a finished program (ignores the last status).
    if action == "reopen":
        if not profile.is_manager:
            raise PermissionDenied("فقط مدیر می‌تواند برنامهٔ خاتمه‌یافته را باز کند.")
        if request.method == "POST":
            block = evaluate_status_change_block(program, ProductionProgram.Status.RUNNING)
            if block:
                messages.error(request, block)
                return redirect("production_conflicts")
            program.status = ProductionProgram.Status.RUNNING
            program.stop_date = None
            program.stop_time = None
            program.save()
            messages.info(request, "برنامه مجدداً باز شد؛ آخرین وضعیت نادیده گرفته شد.")
            return redirect("program_list")

    if status == ProductionProgram.Status.AWAITING:
        if request.method == "POST":
            form = ProgramStartForm(request.POST, program=program)
            if form.is_valid():
                cd = form.cleaned_data
                production_type = int(cd["production_type"])
                lines = list(program.item.lines.all())
                line_idx = production_type - 1
                line = lines[line_idx] if 0 <= line_idx < len(lines) else None
                mold = line.mold if line else None
                block = evaluate_status_change_block(program, ProductionProgram.Status.RUNNING)
                if block:
                    messages.error(request, block)
                    return redirect("production_conflicts")
                program.change_type = cd["change_type"]
                program.change_reason = cd.get("change_reason")
                program.production_type = production_type
                program.mold = mold
                program.start_date = cd["start_date"]
                program.start_time = cd["start_time"]
                program.status = ProductionProgram.Status.RUNNING
                program.save()
                messages.success(request, "برنامه راه‌اندازی شد و تولید آغاز شد.")
                return redirect("program_list")
        else:
            import datetime
            import jdatetime
            form = ProgramStartForm(program=program, initial={
                "start_date": jdatetime.date.today(),
                "start_time": datetime.datetime.now().strftime("%H:%M"),
                "change_type": "setup",
                "production_type": "1",
            })
        return render(request, base_template,
                      {"program": program, "form": form, "phase": "start"})

    # RUNNING or TEMP_STOP -> status dropdown (ادامه / توقف موقت / اتمام تولید)
    if status in (ProductionProgram.Status.RUNNING, ProductionProgram.Status.TEMP_STOP):
        if request.method == "POST":
            form = ProgramStatusForm(request.POST, current=status)
            if form.is_valid():
                new_status = form.cleaned_data["new_status"]
                if new_status in (
                    ProductionProgram.Status.RUNNING,
                    ProductionProgram.Status.TEMP_STOP,
                ):
                    block = evaluate_status_change_block(program, new_status)
                    if block:
                        messages.error(request, block)
                        return redirect("production_conflicts")
                if new_status == ProductionProgram.Status.RUNNING:
                    program.status = ProductionProgram.Status.RUNNING
                    program.stop_date = None
                    program.stop_time = None
                else:
                    program.stop_date = form.cleaned_data["stop_date"]
                    program.stop_time = form.cleaned_data["stop_time"]
                    program.status = new_status
                program.save()
                for e in program.entries.all():  # stop time affects day windows
                    e.save()
                messages.success(request, "وضعیت به‌روزرسانی شد.")
                return redirect("program_list")
        else:
            import datetime
            import jdatetime
            form = ProgramStatusForm(current=status, initial={
                "stop_date": jdatetime.date.today(),
                "stop_time": datetime.datetime.now().strftime("%H:%M"),
            })
        return render(request, base_template,
                      {"program": program, "form": form, "phase": "status"})

    # FINISHED
    return render(request, base_template,
                  {"program": program, "form": None, "phase": "finished"})


# ---------------------------------------------------------------------------
# Pipe production (unchanged free entry; pipes have no weekly plan)
# ---------------------------------------------------------------------------

def _handle_pipe(request, instance=None):
    if request.method == "POST":
        form = PipeProductionForm(request.POST, instance=instance)
        formset = StoppageFormSetPipe(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            if obj.created_by_id is None:
                obj.created_by = request.user
            obj.save()
            formset.instance = obj
            if formset.is_valid():
                formset.save()
            messages.success(request, "اطلاعات با موفقیت ثبت شد.")
            return None
        return form, formset
    return PipeProductionForm(instance=instance), StoppageFormSetPipe(instance=instance)


@login_required
def pipe_create(request):
    _require_data_entry(request)
    result = _handle_pipe(request)
    if result is None:
        return redirect("production_list")
    form, formset = result
    return render(request, "production/pipe_form.html",
                  {"form": form, "formset": formset, "mode": "create",
                   "pipe_field_map": json.dumps(pipe_field_map())})


@login_required
def pipe_edit(request, pk):
    rec = get_object_or_404(PipeProduction, pk=pk)
    profile = _profile(request)
    if not profile or not profile.can_edit_record(rec):
        raise PermissionDenied("فقط مدیر یا ثبت‌کننده می‌تواند ویرایش کند.")
    result = _handle_pipe(request, instance=rec)
    if result is None:
        return redirect("production_list")
    form, formset = result
    return render(request, "production/pipe_form.html",
                  {"form": form, "formset": formset, "mode": "edit",
                   "pipe_field_map": json.dumps(pipe_field_map())})
