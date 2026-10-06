"""Weekly production planning for fittings, with a manager-approval workflow."""

from django.conf import settings
from django.db import models
from django_jalali.db import models as jmodels

from catalog.models import (
    Machine,
    MoldMachineCompatibility,
    MoldOption,
    PhysicalMold,
    Product,
    ProductionTypeOption,
    ProductionUnit,
    ProductSubGroup,
)
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators

from .postgres_constraints import (
    ACTIVE_MOLD_RESERVATION_CONSTRAINT,
    InclusiveDateRange,
)


def generate_program_uid() -> str:
    """Placeholder default; real UIDs are assigned via planning.uid after save."""
    import secrets
    return secrets.token_hex(4).upper()


PERSIAN_WEEKDAYS = [
    "شنبه",
    "یکشنبه",
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنج‌شنبه",
    "جمعه",
]


def persian_weekday(jdate) -> str:
    """Return the Persian weekday name for a jdatetime.date (Saturday=0)."""
    try:
        return PERSIAN_WEEKDAYS[jdate.weekday()]
    except (AttributeError, IndexError):
        return ""


class Weekday(models.IntegerChoices):
    SHANBE = 0, "شنبه"
    YEKSHANBE = 1, "یکشنبه"
    DOSHANBE = 2, "دوشنبه"
    SESHANBE = 3, "سه‌شنبه"
    CHAHARSHANBE = 4, "چهارشنبه"
    PANJSHANBE = 5, "پنج‌شنبه"
    JOME = 6, "جمعه"


class WeeklyPlanQuerySet(models.QuerySet):
    def content_locked(self):
        return self.filter(
            models.Q(source_state="history")
            | models.Q(source_approval__isnull=False)
            | models.Q(system_row_decisions__isnull=False)
            | models.Q(final_revision__isnull=False)
        ).distinct()

    def update(self, **kwargs):
        if self.content_locked().exists():
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        object_ids = [obj.pk for obj in objs if obj.pk is not None]
        if self.model.objects.filter(pk__in=object_ids).content_locked().exists():
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().bulk_update(objs, fields, **kwargs)

    def delete(self):
        if self.filter(governance_cycle__isnull=False).exists():
            raise ValueError("Governed program sources are immutable audit records.")
        return super().delete()

    def _update_for_governance_service(self, **kwargs):
        return super().update(**kwargs)


class WeeklyPlan(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "در انتظار تأیید"
        APPROVED = "approved", "تأییدشده"

    class PlanningMode(models.TextChoices):
        MANUAL = "manual", "دستی"
        SYSTEMIC = "systemic", "سیستمی"
        FINAL = "final", "نهایی/تلفیقی"

    program_number = models.CharField("شماره برنامه", max_length=30, db_index=True)
    date = jmodels.jDateField("تاریخ برنامه‌ریزی", db_index=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    planning_mode = models.CharField(
        "نحوه برنامه‌ریزی",
        max_length=12,
        choices=PlanningMode.choices,
        default=PlanningMode.MANUAL,
    )
    alarms = models.TextField("هشدارها", blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_plans",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_plans",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    governance_cycle = models.ForeignKey(
        "WeeklyProgramCycle",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sources",
    )
    source_state = models.CharField(
        max_length=16,
        choices=[("active", "فعال"), ("history", "تاریخچه")],
        default="active",
        db_index=True,
    )

    objects = WeeklyPlanQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "برنامه هفتگی"
        verbose_name_plural = "برنامه‌ریزی هفتگی"
        constraints = [
            models.UniqueConstraint(
                fields=["program_number"],
                condition=models.Q(governance_cycle__isnull=True),
                name="uniq_legacy_plan_program_number",
            ),
            models.UniqueConstraint(
                fields=["date"],
                condition=models.Q(governance_cycle__isnull=True),
                name="uniq_legacy_plan_date",
            ),
            models.UniqueConstraint(
                fields=["governance_cycle", "planning_mode"],
                condition=models.Q(
                    governance_cycle__isnull=False,
                    planning_mode__in=["manual", "systemic"],
                ),
                name="uniq_governed_program_source",
            ),
        ]

    def delete(self, *args, **kwargs):
        if self.governance_cycle_id is not None:
            raise ValueError("Governed program sources are immutable audit records.")
        return super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or "default"
        if (
            self._state.adding
            and self.planning_mode == self.PlanningMode.FINAL
            and not getattr(self, "_merge_service_create", False)
        ):
            raise ValueError("Final Programs require the canonical merge service.")
        if self.pk is not None and not getattr(self, "_governance_service_update", False):
            if WeeklyPlan.objects.using(using).filter(pk=self.pk).content_locked().exists():
                raise ValueError("Governed source content is immutable after evidence exists.")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        from .utils import format_jdate
        return f"برنامه {self.program_number} — {format_jdate(self.date)}"

    @property
    def weekday_name(self) -> str:
        return persian_weekday(self.date)


class WeeklyProgramSequenceQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Program numbering may change only through the canonical allocator.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Program numbering may change only through the canonical allocator.")

    def delete(self):
        raise ValueError("Program numbering cannot be deleted.")

    def _update_for_governance_service(self, **kwargs):
        return super().update(**kwargs)


class WeeklyProgramSequence(models.Model):
    """Single locked allocator for contiguous governed program numbers."""

    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_allocated_number = models.PositiveIntegerField()
    initialized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    initialized_at = models.DateTimeField(auto_now_add=True)

    objects = WeeklyProgramSequenceQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(singleton=1),
                name="weekly_program_sequence_singleton",
            )
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(self, "_governance_service_create", False):
            raise ValueError("Program numbering requires the canonical allocator.")
        if self.pk is not None and not self._state.adding:
            raise ValueError("Program numbering may change only through the canonical allocator.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Program numbering cannot be deleted.")


class WeeklyProgramCycle(models.Model):
    """Governed weekly cycle containing at most one Manual and one System source."""

    program_number = models.PositiveIntegerField(unique=True)
    program_date = jmodels.jDateField(unique=True)
    final_plan = models.OneToOneField(
        WeeklyPlan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="final_for_governance_cycle",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_program_cycles"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-program_number"]

    def delete(self, *args, **kwargs):
        raise ValueError("Governed program cycles cannot be deleted.")


class AppendOnlyProgramEvidenceQuerySet(models.QuerySet):
    def bulk_create(self, objs, **kwargs):
        raise ValueError("Program governance evidence requires the canonical service.")

    def update(self, **kwargs):
        raise ValueError("Program governance evidence is immutable.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Program governance evidence is immutable.")

    def delete(self):
        raise ValueError("Program governance evidence is immutable.")


class ProgramSourceApproval(models.Model):
    class ApprovalType(models.TextChoices):
        MANUAL = "manual", "تأیید برنامه دستی"
        SYSTEM_FINAL = "system_final", "تأیید نهایی برنامه سیستمی"

    source_plan = models.OneToOneField(
        WeeklyPlan, on_delete=models.PROTECT, related_name="source_approval"
    )
    approval_type = models.CharField(max_length=16, choices=ApprovalType.choices)
    content_fingerprint = models.CharField(max_length=64, db_index=True)
    content_snapshot = models.JSONField(default=dict)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="program_source_approvals"
    )
    approved_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyProgramEvidenceQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(approval_type__in=["manual", "system_final"]),
                name="program_source_approval_valid_type",
            )
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(self, "_governance_service_create", False):
            raise ValueError("Program governance evidence requires the canonical service.")
        if self.pk is not None:
            raise ValueError("Program governance evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Program governance evidence is immutable.")


class SystemProgramRowDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVE = "approve", "تأیید"
        MODIFY = "modify", "تأیید با اصلاح"
        REJECT = "reject", "رد"

    source_plan = models.ForeignKey(
        WeeklyPlan, on_delete=models.PROTECT, related_name="system_row_decisions"
    )
    item = models.ForeignKey(
        "WeeklyPlanItem", on_delete=models.PROTECT, related_name="system_review_decisions"
    )
    sequence = models.PositiveIntegerField()
    decision = models.CharField(max_length=12, choices=Decision.choices)
    decision_snapshot = models.JSONField(default=dict)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="system_program_decisions"
    )
    decided_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyProgramEvidenceQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_plan", "item", "sequence"],
                name="uniq_system_row_decision_sequence",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gt=0),
                name="system_row_decision_sequence_positive",
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(self, "_governance_service_create", False):
            raise ValueError("Program governance evidence requires the canonical service.")
        if self.pk is not None:
            raise ValueError("Program governance evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Program governance evidence is immutable.")


class FinalProgramRowProvenance(models.Model):
    """Reserved B2 evidence linking each future Final row to reviewed sources."""

    class Origin(models.TextChoices):
        MANUAL = "manual", "دستی"
        SYSTEM = "system", "سیستمی"
        MANUAL_SYSTEM_MODIFIED = "manual_system_modified", "دستی + سیستمی/اصلاح‌شده"

    final_item = models.OneToOneField(
        "WeeklyPlanItem", on_delete=models.PROTECT, related_name="final_provenance"
    )
    manual_source_item = models.ForeignKey(
        "WeeklyPlanItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="manual_final_provenance",
    )
    system_source_item = models.ForeignKey(
        "WeeklyPlanItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="system_final_provenance",
    )
    system_decision = models.ForeignKey(
        SystemProgramRowDecision,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="final_row_provenance",
    )
    origin = models.CharField(max_length=24, choices=Origin.choices)
    provenance_snapshot = models.JSONField(default=dict)
    recorded_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyProgramEvidenceQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(origin="manual", manual_source_item__isnull=False)
                    | models.Q(origin="system", system_source_item__isnull=False)
                    | models.Q(
                        origin="manual_system_modified",
                        manual_source_item__isnull=False,
                        system_source_item__isnull=False,
                    )
                ),
                name="final_row_provenance_has_required_sources",
            )
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(self, "_governance_service_create", False):
            raise ValueError("Program governance evidence requires the canonical service.")
        if self.pk is not None:
            raise ValueError("Program governance evidence is immutable.")
        self.clean()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        final_cycle = getattr(getattr(self.final_item, "plan", None), "governance_cycle_id", None)
        for source in (self.manual_source_item, self.system_source_item):
            if source is not None and source.plan.governance_cycle_id != final_cycle:
                raise ValueError("Final provenance cannot cross Program cycles.")
        if self.system_decision_id and self.system_source_item_id:
            if self.system_decision.item_id != self.system_source_item_id:
                raise ValueError("Final provenance decision must belong to its System row.")
            if self.system_decision.source_plan_id != self.system_source_item.plan_id:
                raise ValueError("Final provenance decision must belong to its System source.")

    def delete(self, *args, **kwargs):
        raise ValueError("Program governance evidence is immutable.")


class FinalProgramRevisionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Final Program revisions require the canonical merge service.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Final Program revisions require the canonical merge service.")

    def bulk_create(self, objs, **kwargs):
        raise ValueError("Final Program revisions require the canonical merge service.")

    def delete(self):
        raise ValueError("Final Program revisions are immutable evidence.")

    def _update_for_merge_service(self, **kwargs):
        return super().update(**kwargs)


class FinalProgramRevision(models.Model):
    class Status(models.TextChoices):
        EFFECTIVE = "effective", "مؤثر"
        SUPERSEDED = "superseded", "جایگزین‌شده"

    cycle = models.ForeignKey(
        WeeklyProgramCycle, on_delete=models.PROTECT, related_name="final_revisions"
    )
    revision_number = models.PositiveIntegerField()
    final_plan = models.OneToOneField(
        WeeklyPlan, on_delete=models.PROTECT, related_name="final_revision"
    )
    manual_source = models.ForeignKey(
        WeeklyPlan, on_delete=models.PROTECT, related_name="manual_final_revisions"
    )
    system_source = models.ForeignKey(
        WeeklyPlan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="system_final_revisions",
    )
    supersedes = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    source_review_fingerprint = models.CharField(max_length=64, db_index=True)
    result_fingerprint = models.CharField(max_length=64, db_index=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_final_program_revisions",
    )
    approved_at = models.DateTimeField(auto_now_add=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    objects = FinalProgramRevisionQuerySet.as_manager()

    class Meta:
        ordering = ["cycle_id", "revision_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["cycle", "revision_number"],
                name="uniq_final_revision_number_per_cycle",
            ),
            models.UniqueConstraint(
                fields=["cycle"],
                condition=models.Q(status="effective"),
                name="uniq_effective_final_revision_per_cycle",
            ),
            models.CheckConstraint(
                condition=models.Q(revision_number__gt=0),
                name="final_revision_number_positive",
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(self, "_merge_service_create", False):
            raise ValueError("Final Program revisions require the canonical merge service.")
        if self.pk is not None:
            raise ValueError("Final Program revisions are immutable evidence.")
        self.clean()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.final_plan.governance_cycle_id != self.cycle_id:
            raise ValueError("Final revision plan must belong to its Program cycle.")
        if self.final_plan.planning_mode != WeeklyPlan.PlanningMode.FINAL:
            raise ValueError("Final revision must reference a Final Program plan.")
        if (
            self.manual_source.governance_cycle_id != self.cycle_id
            or self.manual_source.planning_mode != WeeklyPlan.PlanningMode.MANUAL
        ):
            raise ValueError("Final revision Manual source must belong to its Program cycle.")
        if self.system_source_id and (
            self.system_source.governance_cycle_id != self.cycle_id
            or self.system_source.planning_mode != WeeklyPlan.PlanningMode.SYSTEMIC
        ):
            raise ValueError("Final revision System source must belong to its Program cycle.")
        if self.supersedes_id and self.supersedes.cycle_id != self.cycle_id:
            raise ValueError("Final revision cannot supersede another Program cycle.")

    def delete(self, *args, **kwargs):
        raise ValueError("Final Program revisions are immutable evidence.")


class SystemFinalApproval(models.Model):
    cycle = models.ForeignKey(
        WeeklyProgramCycle, on_delete=models.PROTECT, related_name="system_final_approvals"
    )
    system_source = models.OneToOneField(
        WeeklyPlan, on_delete=models.PROTECT, related_name="system_final_approval"
    )
    review_fingerprint = models.CharField(max_length=64, db_index=True)
    final_revision = models.OneToOneField(
        FinalProgramRevision,
        on_delete=models.PROTECT,
        related_name="system_final_approval",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="system_final_approvals",
    )
    approved_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyProgramEvidenceQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(self, "_governance_service_create", False):
            raise ValueError("System Final Approval requires the canonical merge service.")
        if self.pk is not None:
            raise ValueError("System Final Approval evidence is immutable.")
        self.clean()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if (
            self.system_source.governance_cycle_id != self.cycle_id
            or self.system_source.planning_mode != WeeklyPlan.PlanningMode.SYSTEMIC
        ):
            raise ValueError("System Final Approval source must belong to its Program cycle.")
        if self.final_revision.cycle_id != self.cycle_id:
            raise ValueError("System Final Approval revision must belong to its Program cycle.")
        if self.final_revision.system_source_id != self.system_source_id:
            raise ValueError("System Final Approval source must match the Final revision source.")

    def delete(self, *args, **kwargs):
        raise ValueError("System Final Approval evidence is immutable.")


class PlanningGovernanceAlarm(models.Model):
    class Kind(models.TextChoices):
        MANUAL_WITHOUT_SYSTEM = "manual_without_system", "برنامه دستی بدون پیشنهاد سیستمی"

    cycle = models.ForeignKey(
        WeeklyProgramCycle, on_delete=models.PROTECT, related_name="governance_alarms"
    )
    source_plan = models.ForeignKey(
        WeeklyPlan, on_delete=models.PROTECT, related_name="governance_alarms"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=200)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyProgramEvidenceQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(self, "_governance_service_create", False):
            raise ValueError("Program governance evidence requires the canonical service.")
        if self.pk is not None:
            raise ValueError("Program governance evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Program governance evidence is immutable.")


class PlanningGovernanceAlarmView(models.Model):
    alarm = models.ForeignKey(
        PlanningGovernanceAlarm, on_delete=models.PROTECT, related_name="views"
    )
    viewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="viewed_governance_alarms"
    )
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["alarm", "viewed_by"], name="uniq_governance_alarm_viewer"
            )
        ]


class GovernedSourceContentQuerySet(models.QuerySet):
    def _locked(self):
        if self.model.__name__ == "WeeklyPlanItem":
            return self.filter(
                models.Q(plan__source_state="history")
                | models.Q(plan__source_approval__isnull=False)
                | models.Q(plan__system_row_decisions__isnull=False)
                | models.Q(plan__final_revision__isnull=False)
            ).distinct()
        prefix = "requirement__item__plan" if self.model.__name__ == "PlannedProductionPath" else "item__plan"
        return self.filter(
            models.Q(**{f"{prefix}__source_state": "history"})
            | models.Q(**{f"{prefix}__source_approval__isnull": False})
            | models.Q(**{f"{prefix}__system_row_decisions__isnull": False})
            | models.Q(**{f"{prefix}__final_revision__isnull": False})
        ).distinct()

    def update(self, **kwargs):
        if self._locked().exists():
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        object_ids = [obj.pk for obj in objs if obj.pk is not None]
        if self.model.objects.filter(pk__in=object_ids)._locked().exists():
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().bulk_update(objs, fields, **kwargs)

    def delete(self):
        if self._locked().exists():
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().delete()


class WeeklyPlanItem(models.Model):
    plan = models.ForeignKey(
        WeeklyPlan, on_delete=models.CASCADE, related_name="items"
    )
    subgroup = models.ForeignKey(
        ProductSubGroup, on_delete=models.PROTECT, related_name="+", verbose_name="زیرگروه"
    )
    unit = models.ForeignKey(
        ProductionUnit, on_delete=models.PROTECT, related_name="+", verbose_name="واحد تولیدی"
    )
    machine = models.ForeignKey(
        Machine, on_delete=models.PROTECT, related_name="+", verbose_name="دستگاه"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="+", verbose_name="نام محصول"
    )
    mold = models.ForeignKey(
        MoldOption, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="نوع قالب",
    )

    mold_change_weekday = models.IntegerField(
        "روز تعویض قالب", choices=Weekday.choices
    )
    mold_change_date = jmodels.jDateField("تاریخ تعویض قالب")
    active_cavities = models.PositiveSmallIntegerField("تعداد حفره فعال", default=1)
    production_days = models.JSONField(
        "روزهای تولید", default=list, blank=True,
        help_text="فهرست {date, shift, note}",
    )

    uid = models.CharField(
        "شناسه برنامه", max_length=32, unique=True, default=generate_program_uid,
        editable=False, db_index=True,
    )
    # Sequence position on the same machine within a plan (1 = تولید اول, ...).
    sequence = models.PositiveSmallIntegerField("ترتیب روی دستگاه", default=1)

    history_alarm = models.BooleanField(default=False)

    objects = GovernedSourceContentQuerySet.as_manager()

    class Meta:
        verbose_name = "کالای برنامه"
        verbose_name_plural = "کالاهای برنامه"

    def __str__(self) -> str:
        return f"{self.product.name} @ {self.machine}"

    def _locked_source_exists(self, using):
        plan_ids = {self.plan_id}
        if self.pk is not None:
            previous_plan_id = type(self).objects.using(using).filter(pk=self.pk).values_list(
                "plan_id", flat=True
            ).first()
            plan_ids.add(previous_plan_id)
        plan_ids.discard(None)
        return WeeklyPlan.objects.using(using).filter(pk__in=plan_ids).content_locked().exists()

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or "default"
        if self._locked_source_exists(using):
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or "default"
        if self._locked_source_exists(using):
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().delete(*args, **kwargs)

    def uid_for_type(self, production_type_index: int = 1) -> str:
        from .uid import uid_for_item
        return uid_for_item(self, production_type_index=production_type_index)

    @property
    def has_production_days(self) -> bool:
        days = self.production_days or []
        if not isinstance(days, list):
            return False
        return any(
            isinstance(d, dict) and str(d.get("date") or "").strip()
            for d in days
        )


class WeeklyPlanLine(models.Model):
    """A repeatable (نوع تولید، قالب، مقدار تولید، سیکل تولید) row on a plan item."""

    item = models.ForeignKey(
        WeeklyPlanItem, on_delete=models.CASCADE, related_name="lines"
    )
    production_type = models.ForeignKey(
        ProductionTypeOption, on_delete=models.PROTECT, related_name="+",
        verbose_name="نوع تولید", null=True, blank=True,
    )
    mold = models.ForeignKey(
        MoldOption, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="انتخاب قالب",
    )
    quantity = models.PositiveIntegerField("مقدار تولید", default=0)
    cycle = models.PositiveIntegerField("سیکل تولید (ثانیه)", default=0)
    active_cavities = models.PositiveSmallIntegerField("تعداد حفره", default=1)
    uid = models.CharField(
        "شناسه ردیف تولید",
        max_length=32,
        blank=True,
        default="",
        db_index=True,
        help_text="شناسه ۱۴ رقمی مخصوص این نوع تولید",
    )

    objects = GovernedSourceContentQuerySet.as_manager()

    class Meta:
        verbose_name = "ردیف تولید"
        verbose_name_plural = "ردیف‌های تولید"

    def __str__(self) -> str:
        return f"{self.production_type} — {self.quantity}"

    def _locked_source_exists(self, using):
        item_ids = {self.item_id}
        if self.pk is not None:
            previous_item_id = type(self).objects.using(using).filter(pk=self.pk).values_list(
                "item_id", flat=True
            ).first()
            item_ids.add(previous_item_id)
        item_ids.discard(None)
        return WeeklyPlanItem.objects.using(using).filter(pk__in=item_ids)._locked().exists()

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or "default"
        if self._locked_source_exists(using):
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or "default"
        if self._locked_source_exists(using):
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().delete(*args, **kwargs)

    @property
    def production_hours(self) -> float:
        """Estimated production hours: (qty / cavities) × cycle seconds / 3600."""
        cavities = max(int(self.active_cavities or getattr(self.item, "active_cavities", 1) or 1), 1)
        qty = int(self.quantity or 0)
        cycle = int(self.cycle or 0)
        if qty <= 0 or cycle <= 0:
            return 0.0
        return round((qty / cavities) * cycle / 3600.0, 2)

    @property
    def assigned_planned_share_total(self) -> int:
        return int(
            self.planned_paths.filter(is_active=True).aggregate(
                total=models.Sum("planned_share_quantity")
            )["total"]
            or 0
        )

    @property
    def assignment_difference(self) -> int:
        return self.assigned_planned_share_total - int(self.quantity or 0)

    @property
    def unassigned_quantity(self) -> int:
        return max(-self.assignment_difference, 0)

    @property
    def overassigned_quantity(self) -> int:
        return max(self.assignment_difference, 0)


class PlannedProductionPath(models.Model):
    """Mutable planning intent beneath one component production requirement."""

    requirement = models.ForeignKey(
        WeeklyPlanLine,
        on_delete=models.CASCADE,
        related_name="planned_paths",
        verbose_name="نیاز تولید قطعه",
    )
    machine = models.ForeignKey(
        Machine,
        on_delete=models.PROTECT,
        related_name="planned_production_paths",
        verbose_name="دستگاه",
    )
    physical_mold = models.ForeignKey(
        PhysicalMold,
        on_delete=models.PROTECT,
        related_name="planned_production_paths",
        verbose_name="قالب فیزیکی",
    )
    planned_share_quantity = models.PositiveIntegerField(
        "سهم برنامه‌ریزی‌شده", default=0
    )
    planned_start_date = jmodels.jDateField("تاریخ شروع برنامه‌ریزی‌شده")
    planned_end_date = jmodels.jDateField("تاریخ پایان برنامه‌ریزی‌شده")
    sequence = models.PositiveSmallIntegerField("ترتیب مسیر", default=1)
    is_active = models.BooleanField("فعال", default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_planned_production_paths",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_planned_production_paths",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GovernedSourceContentQuerySet.as_manager()

    class Meta:
        ordering = ["requirement_id", "sequence", "id"]
        verbose_name = "مسیر برنامه‌ریزی تولید"
        verbose_name_plural = "مسیرهای برنامه‌ریزی تولید"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(planned_start_date__lte=models.F("planned_end_date")),
                name="planned_path_valid_date_range",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gt=0),
                name="planned_path_positive_sequence",
            ),
            models.UniqueConstraint(
                fields=["requirement", "sequence"],
                name="uniq_planned_path_requirement_sequence",
            ),
        ]
        indexes = [
            models.Index(
                fields=["physical_mold", "planned_start_date", "planned_end_date"],
                name="planned_path_mold_dates_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.requirement} / {self.machine} / {self.physical_mold}"

    def _locked_source_exists(self, using):
        requirement_ids = {self.requirement_id}
        if self.pk is not None:
            previous_id = type(self).objects.using(using).filter(pk=self.pk).values_list(
                "requirement_id", flat=True
            ).first()
            requirement_ids.add(previous_id)
        requirement_ids.discard(None)
        return type(self).objects.using(using).filter(
            requirement_id__in=requirement_ids
        )._locked().exists()

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or "default"
        if self._locked_source_exists(using):
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or "default"
        if self._locked_source_exists(using):
            raise ValueError("Governed source content is immutable after evidence exists.")
        return super().delete(*args, **kwargs)


class SnapshotWriteGuardQuerySet(models.QuerySet):
    """Prevent accidental bulk rewrites of immutable approval evidence."""

    immutable_fields: frozenset[str] = frozenset()

    def update(self, **kwargs):
        blocked = self.immutable_fields.intersection(kwargs)
        if blocked:
            raise ValueError(
                "Immutable snapshot fields cannot be updated: "
                + ", ".join(sorted(blocked))
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        blocked = self.immutable_fields.intersection(fields)
        if blocked:
            raise ValueError(
                "Immutable snapshot fields cannot be bulk-updated: "
                + ", ".join(sorted(blocked))
            )
        return super().bulk_update(objs, fields, **kwargs)


class PlanApprovalRevisionQuerySet(SnapshotWriteGuardQuerySet):
    immutable_fields = frozenset(
        {"plan", "plan_id", "revision_number"}
    )

    approval_evidence_fields = frozenset(
        {"approved_by", "approved_by_id", "approved_at", "content_fingerprint"}
    )

    def _contains_protected_revisions(self) -> bool:
        return self.exclude(status=self.model.Status.PREPARING).exists()

    def update(self, **kwargs):
        if self.approval_evidence_fields.intersection(kwargs):
            if self._contains_protected_revisions():
                raise ValueError(
                    "Approval evidence is immutable after a revision leaves PREPARING."
                )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        objs = tuple(objs)
        if self.approval_evidence_fields.intersection(fields):
            pks = [obj.pk for obj in objs if obj.pk is not None]
            if (
                pks
                and self.model.objects.using(self.db)
                .filter(pk__in=pks)
                .exclude(status=self.model.Status.PREPARING)
                .exists()
            ):
                raise ValueError(
                    "Approval evidence is immutable after a revision leaves PREPARING."
                )
        return super().bulk_update(objs, fields, **kwargs)

    def delete(self):
        if self._contains_protected_revisions():
            raise ValueError(
                "Only PREPARING approval revisions may be deleted."
            )
        return super().delete()


class PlanApprovalRevision(models.Model):
    """Future immutable identity for one complete approved planning snapshot."""

    class Status(models.TextChoices):
        PREPARING = "preparing", "در حال آماده‌سازی"
        ACTIVE = "active", "فعال"
        SUPERSEDED = "superseded", "جایگزین‌شده"
        CANCELLED = "cancelled", "لغوشده"

    plan = models.ForeignKey(
        WeeklyPlan,
        on_delete=models.PROTECT,
        related_name="approval_revisions",
        verbose_name="برنامه هفتگی",
    )
    revision_number = models.PositiveIntegerField("شماره بازنگری")
    status = models.CharField(
        "وضعیت", max_length=16, choices=Status.choices, default=Status.PREPARING
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="planning_approval_revisions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    content_fingerprint = models.CharField(
        "اثر انگشت محتوای تأیید",
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="SHA-256 deterministic fingerprint of approval-relevant draft content.",
    )
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_revisions",
    )
    superseded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PlanApprovalRevisionQuerySet.as_manager()

    class Meta:
        ordering = ["plan_id", "revision_number"]
        verbose_name = "بازنگری تأیید برنامه"
        verbose_name_plural = "بازنگری‌های تأیید برنامه"
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "revision_number"],
                name="uniq_plan_approval_revision_number",
            ),
            models.UniqueConstraint(
                fields=["plan"],
                condition=models.Q(status="active"),
                name="uniq_active_approval_revision_per_plan",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["preparing", "active", "superseded", "cancelled"]
                ),
                name="plan_approval_revision_valid_status",
            ),
        ]

    def save(self, *args, **kwargs):
        database = kwargs.get("using") or self._state.db or "default"
        if self.pk is not None:
            previous = type(self).objects.using(database).get(pk=self.pk)
            immutable = ("plan_id", "revision_number")
            if any(getattr(previous, field) != getattr(self, field) for field in immutable):
                raise ValueError("Approval revision identity is immutable.")
            if previous.status != self.Status.PREPARING and (
                previous.approved_by_id != self.approved_by_id
                or previous.approved_at != self.approved_at
                or previous.content_fingerprint != self.content_fingerprint
            ):
                raise ValueError("Activated approval evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        database = kwargs.get("using") or self._state.db or "default"
        if self.pk is not None:
            persisted_status = (
                type(self).objects.using(database)
                .filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
            if persisted_status and persisted_status != self.Status.PREPARING:
                raise ValueError("Only PREPARING approval revisions may be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.plan} / بازنگری {self.revision_number}"


class PlanApprovalDecisionQuerySet(models.QuerySet):
    """Approval exception evidence is append-only through normal ORM paths."""

    def update(self, **kwargs):
        raise ValueError("Approval decision evidence is immutable.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Approval decision evidence is immutable.")

    def bulk_create(self, objs, **kwargs):
        objs = tuple(objs)
        for obj in objs:
            obj._validate_decision_authority(using=self.db)
        return super().bulk_create(objs, **kwargs)

    def delete(self):
        raise ValueError("Approval decision evidence cannot be deleted.")


class PlanApprovalDecision(models.Model):
    """Immutable manager evidence for one approval-time exception."""

    class DecisionType(models.TextChoices):
        UNKNOWN_COMPATIBILITY = "unknown_compatibility", "سازگاری نامشخص"
        NEEDS_REVIEW_COMPATIBILITY = "needs_review_compatibility", "سازگاری نیازمند بررسی"
        UNDER_REPAIR_FUTURE_USE = "under_repair_future_use", "استفاده آینده از قالب در تعمیر"

    class Outcome(models.TextChoices):
        APPROVED = "approved", "تأیید شد"

    approval_revision = models.ForeignKey(
        PlanApprovalRevision,
        on_delete=models.PROTECT,
        related_name="approval_decisions",
        verbose_name="بازنگری تأیید",
    )
    planned_path = models.ForeignKey(
        PlannedProductionPath,
        on_delete=models.PROTECT,
        related_name="approval_decisions",
        verbose_name="مسیر برنامه‌ریزی",
    )
    reservation = models.ForeignKey(
        "PhysicalMoldReservation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approval_decisions",
        verbose_name="رزرو مرتبط",
    )
    decision_type = models.CharField(
        "نوع تصمیم", max_length=32, choices=DecisionType.choices
    )
    outcome = models.CharField(
        "نتیجه", max_length=16, choices=Outcome.choices, default=Outcome.APPROVED
    )
    condition_snapshot = models.JSONField("تصویر وضعیت هشدار", default=dict)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="planning_approval_decisions",
        verbose_name="تصمیم‌گیرنده",
    )
    decided_at = models.DateTimeField("زمان تصمیم", auto_now_add=True)
    reason = models.TextField("دلیل اختیاری", blank=True)

    objects = PlanApprovalDecisionQuerySet.as_manager()

    class Meta:
        ordering = ["approval_revision_id", "planned_path_id", "decision_type", "id"]
        verbose_name = "تصمیم استثنای تأیید برنامه"
        verbose_name_plural = "تصمیم‌های استثنای تأیید برنامه"
        constraints = [
            models.UniqueConstraint(
                fields=["approval_revision", "planned_path", "decision_type"],
                name="uniq_plan_approval_decision_evidence",
            ),
            models.CheckConstraint(
                condition=models.Q(outcome="approved"),
                name="plan_approval_decision_valid_outcome",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Approval decision evidence is immutable.")
        self._validate_decision_authority(
            using=kwargs.get("using") or self._state.db or "default"
        )
        return super().save(*args, **kwargs)

    def _validate_decision_authority(self, *, using):
        from accounts.models import UserProfile

        if not self.decided_by_id:
            raise ValueError("Approval decision evidence requires a decision maker.")
        if getattr(self.decided_by, "is_superuser", False):
            return
        if not UserProfile.objects.using(using).filter(
            user_id=self.decided_by_id,
            role="planning_manager",
        ).exists():
            raise ValueError("Only a planning manager may create approval decision evidence.")

    def delete(self, *args, **kwargs):
        raise ValueError("Approval decision evidence cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.approval_revision} / {self.get_decision_type_display()}"


class PathOperationalApprovalQuerySet(models.QuerySet):
    """Append-only path-level operational decisions."""

    def bulk_create(self, objs, **kwargs):
        raise ValueError(
            "Path operational approval evidence must be created through the canonical service."
        )

    def update(self, **kwargs):
        raise ValueError("Path operational approval evidence is immutable.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Path operational approval evidence is immutable.")

    def delete(self):
        raise ValueError("Path operational approval evidence is immutable.")


class PathOperationalApproval(models.Model):
    """Immutable path-scoped approval/edit/cancellation evidence."""

    class Action(models.TextChoices):
        APPROVED = "approved", "تأیید و فعال‌سازی"
        EDITED = "edited", "ویرایش عملیاتی"
        CANCELLED = "cancelled", "لغو عملیاتی"

    planned_path = models.ForeignKey(
        PlannedProductionPath,
        on_delete=models.PROTECT,
        related_name="operational_approvals",
    )
    execution_path = models.ForeignKey(
        "production.ProductionExecutionPath",
        on_delete=models.PROTECT,
        related_name="operational_approvals",
    )
    reservation = models.ForeignKey(
        "PhysicalMoldReservation",
        on_delete=models.PROTECT,
        related_name="operational_approvals",
    )
    sequence = models.PositiveIntegerField()
    action = models.CharField(max_length=16, choices=Action.choices)
    path_fingerprint = models.CharField(max_length=64, db_index=True)
    before_snapshot = models.JSONField(default=dict, blank=True)
    after_snapshot = models.JSONField(default=dict)
    manager_confirmations = models.JSONField(default=list, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="path_operational_approvals",
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PathOperationalApprovalQuerySet.as_manager()

    class Meta:
        ordering = ["planned_path_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["planned_path", "sequence"],
                name="uniq_path_operational_approval_sequence",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gt=0),
                name="path_operational_approval_sequence_positive",
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(
            self, "_operational_approval_service_create", False
        ):
            raise ValueError(
                "Path operational approval evidence must be created through the canonical service."
            )
        if self.pk is not None:
            raise ValueError("Path operational approval evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Path operational approval evidence is immutable.")


class PhysicalMoldReservationQuerySet(SnapshotWriteGuardQuerySet):
    immutable_fields = frozenset(
        {
            "approval_revision", "approval_revision_id", "source_path", "source_path_id",
            "requirement", "requirement_id", "machine", "machine_id", "physical_mold",
            "physical_mold_id", "approved_planned_share_quantity", "reserved_start_date",
            "reserved_end_date", "compatibility_status_snapshot", "mold_status_snapshot",
            "estimated_ready_date_snapshot",
            "execution_path", "execution_path_id",
        }
    )

    def _contains_activated_reservations(self) -> bool:
        return self.exclude(status=self.model.Status.PREPARING).exists()

    def update(self, **kwargs):
        if "activated_at" in kwargs and self._contains_activated_reservations():
            raise ValueError("Reservation activation time is immutable after preparation.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        objs = tuple(objs)
        if "activated_at" in fields:
            pks = [obj.pk for obj in objs if obj.pk is not None]
            if (
                pks
                and self.model.objects.using(self.db)
                .filter(pk__in=pks)
                .exclude(status=self.model.Status.PREPARING)
                .exists()
            ):
                raise ValueError("Reservation activation time is immutable after preparation.")
        return super().bulk_update(objs, fields, **kwargs)

    def active_overlaps(self, *, physical_mold, start_date, end_date):
        return self.filter(
            status=self.model.Status.ACTIVE,
            physical_mold=physical_mold,
            reserved_start_date__lte=end_date,
            reserved_end_date__gte=start_date,
        )

    def delete(self):
        raise ValueError("Physical mold reservation snapshots are immutable.")


class PhysicalMoldReservation(models.Model):
    """Immutable approved-path snapshot and future operational mold reservation."""

    class Status(models.TextChoices):
        PREPARING = "preparing", "در حال آماده‌سازی"
        ACTIVE = "active", "فعال"
        SUPERSEDED = "superseded", "جایگزین‌شده"
        RELEASED = "released", "آزادشده"
        CANCELLED = "cancelled", "لغوشده"

    approval_revision = models.ForeignKey(
        PlanApprovalRevision,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mold_reservations",
        verbose_name="بازنگری تأیید",
    )
    source_path = models.ForeignKey(
        PlannedProductionPath,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reservation_snapshots",
        verbose_name="مسیر برنامه‌ریزی مبدأ",
    )
    requirement = models.ForeignKey(
        WeeklyPlanLine,
        on_delete=models.PROTECT,
        related_name="mold_reservations",
        verbose_name="نیاز تولید قطعه",
    )
    machine = models.ForeignKey(
        Machine,
        on_delete=models.PROTECT,
        related_name="physical_mold_reservations",
        verbose_name="دستگاه تأییدشده",
    )
    physical_mold = models.ForeignKey(
        PhysicalMold,
        on_delete=models.PROTECT,
        related_name="reservations",
        verbose_name="قالب فیزیکی",
    )
    execution_path = models.ForeignKey(
        "production.ProductionExecutionPath",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reservation_snapshots",
        verbose_name="مسیر اجرای تولید",
    )
    approved_planned_share_quantity = models.PositiveIntegerField(
        "سهم برنامه‌ریزی‌شده تأییدشده", default=0
    )
    reserved_start_date = jmodels.jDateField("شروع رزرو")
    reserved_end_date = jmodels.jDateField("پایان رزرو")
    compatibility_status_snapshot = models.CharField(
        "وضعیت سازگاری هنگام تأیید",
        max_length=20,
        choices=MoldMachineCompatibility.Status.choices,
        default=MoldMachineCompatibility.Status.UNKNOWN,
    )
    mold_status_snapshot = models.CharField(
        "وضعیت قالب هنگام تأیید",
        max_length=20,
        choices=PhysicalMold.Status.choices,
    )
    estimated_ready_date_snapshot = jmodels.jDateField(
        "تاریخ تخمینی آمادگی هنگام تأیید", null=True, blank=True
    )
    status = models.CharField(
        "وضعیت رزرو", max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    objects = PhysicalMoldReservationQuerySet.as_manager()

    class Meta:
        ordering = ["reserved_start_date", "physical_mold_id", "id"]
        verbose_name = "رزرو قالب فیزیکی"
        verbose_name_plural = "رزروهای قالب فیزیکی"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reserved_start_date__lte=models.F("reserved_end_date")),
                name="mold_reservation_valid_date_range",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["preparing", "active", "superseded", "released", "cancelled"]
                ),
                name="mold_reservation_valid_status",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="preparing", activated_at__isnull=True)
                    | ~models.Q(status="preparing") & models.Q(activated_at__isnull=False)
                ),
                name="mold_reservation_activation_matches_status",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(approval_revision__isnull=False)
                    | models.Q(source_path__isnull=False)
                ),
                name="mold_reservation_has_provenance",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="active", approval_revision__isnull=True)
                    | models.Q(
                        source_path__isnull=False,
                        execution_path__isnull=False,
                    )
                ),
                name="active_path_reservation_has_execution",
            ),
            ExclusionConstraint(
                name=ACTIVE_MOLD_RESERVATION_CONSTRAINT,
                expressions=[
                    ("physical_mold", RangeOperators.EQUAL),
                    (
                        InclusiveDateRange(
                            "reserved_start_date", "reserved_end_date"
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=models.Q(status="active"),
            ),
        ]
        indexes = [
            models.Index(
                fields=["physical_mold", "status", "reserved_start_date", "reserved_end_date"],
                name="mold_reservation_overlap_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        database = kwargs.get("using") or self._state.db or "default"
        if self.pk is not None:
            previous = type(self).objects.using(database).get(pk=self.pk)
            immutable = (
                "approval_revision_id", "source_path_id", "requirement_id", "machine_id",
                "physical_mold_id", "approved_planned_share_quantity", "reserved_start_date",
                "reserved_end_date", "compatibility_status_snapshot", "mold_status_snapshot",
                "estimated_ready_date_snapshot",
                "execution_path_id",
            )
            if any(getattr(previous, field) != getattr(self, field) for field in immutable):
                raise ValueError("Reservation snapshot fields are immutable.")
            if (
                previous.status != self.Status.PREPARING
                and previous.activated_at != self.activated_at
            ):
                raise ValueError("Reservation activation time is immutable after preparation.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Physical mold reservation snapshots are immutable.")

    def __str__(self) -> str:
        return f"{self.physical_mold} / {self.reserved_start_date} تا {self.reserved_end_date}"


class CustomerOrder(models.Model):
    """Weekly / backlog customer order line — fed from Excel via بررسی موجودی و سفارشات."""

    order_ref = models.CharField("شماره سفارش", max_length=80, blank=True, db_index=True)
    product_code = models.CharField("کد کالا", max_length=40, db_index=True)
    product_name = models.CharField("نام کالا", max_length=200, blank=True)
    quantity = models.PositiveIntegerField("مقدار سفارش", default=0)
    delivery_date = jmodels.jDateField("تاریخ تحویل", null=True, blank=True)
    priority = models.PositiveIntegerField(
        "اولویت", default=100, help_text="عدد کمتر = اولویت بالاتر"
    )
    is_backlog = models.BooleanField("سفارش معوق", default=False)
    customer_name = models.CharField("مشتری", max_length=200, blank=True)
    notes = models.CharField("توضیحات", max_length=255, blank=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_orders",
        verbose_name="محصول",
    )
    is_active = models.BooleanField("فعال", default=True)
    source_table_name = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["priority", "delivery_date", "id"]
        verbose_name = "سفارش"
        verbose_name_plural = "سفارشات"
        indexes = [
            models.Index(fields=["order_ref", "product_code"]),
        ]

    def __str__(self) -> str:
        return f"{self.order_ref or '—'} / {self.product_code} × {self.quantity}"


class SalesForecast(models.Model):
    """Sales forecast snapshot used as MTS demand in systemic planning."""

    product_code = models.CharField("کد کالا", max_length=40, db_index=True)
    product_name = models.CharField("نام کالا", max_length=200, blank=True)
    period_label = models.CharField("دوره پیش‌بینی", max_length=80, blank=True)
    quantity = models.PositiveIntegerField("مقدار پیش‌بینی", default=0)
    notes = models.CharField("توضیحات", max_length=255, blank=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_forecasts",
        verbose_name="محصول",
    )
    is_active = models.BooleanField("فعال", default=True)
    source_table_name = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["product_code", "period_label", "id"]
        verbose_name = "پیش‌بینی فروش"
        verbose_name_plural = "پیش‌بینی فروش"

    def __str__(self) -> str:
        return f"{self.product_code} / {self.period_label or '—'} = {self.quantity}"


class PlanningProcessDefinition(models.Model):
    """A redefinable planning workflow (e.g. Make to Order / Make to Stock)."""

    code = models.SlugField("کد فرآیند", max_length=40, unique=True)
    title = models.CharField("عنوان", max_length=200)
    description = models.TextField("توضیحات", blank=True)
    entry_step_number = models.PositiveIntegerField("شماره مرحله شروع", default=1)
    is_active = models.BooleanField("فعال", default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "تعریف فرآیند برنامه‌ریزی"
        verbose_name_plural = "تعاریف فرآیند برنامه‌ریزی"

    def __str__(self) -> str:
        return f"{self.code} — {self.title}"


class PlanningProcessStep(models.Model):
    """One numbered stage in a planning process; decisions link yes/no to other stages."""

    class Kind(models.TextChoices):
        ACTION = "action", "اقدام"
        DECISION = "decision", "سوال بله/خیر"
        TERMINAL = "terminal", "پایان"

    process = models.ForeignKey(
        PlanningProcessDefinition,
        on_delete=models.CASCADE,
        related_name="steps",
        verbose_name="فرآیند",
    )
    step_number = models.PositiveIntegerField("شماره مرحله")
    title = models.CharField("عنوان مرحله", max_length=255)
    description = models.TextField("توضیحات", blank=True)
    kind = models.CharField(
        "نوع", max_length=12, choices=Kind.choices, default=Kind.ACTION
    )
    question = models.CharField(
        "متن سوال (برای بله/خیر)", max_length=500, blank=True
    )
    yes_next_number = models.PositiveIntegerField(
        "در صورت بله → مرحله", null=True, blank=True
    )
    no_next_number = models.PositiveIntegerField(
        "در صورت خیر → مرحله", null=True, blank=True
    )
    next_number = models.PositiveIntegerField(
        "مرحله بعدی (برای اقدام)", null=True, blank=True
    )
    data_binding = models.CharField(
        "اتصال به داده سیستم",
        max_length=40,
        default="none",
        help_text="کلید اتصال به موجودی، BOM، سفارشات، قالب و …",
    )
    is_active = models.BooleanField("فعال", default=True)
    sort_order = models.PositiveIntegerField("ترتیب نمایش", default=0)

    class Meta:
        ordering = ["process", "sort_order", "step_number"]
        verbose_name = "مرحله فرآیند برنامه‌ریزی"
        verbose_name_plural = "مراحل فرآیند برنامه‌ریزی"
        unique_together = ("process", "step_number")

    def __str__(self) -> str:
        return f"{self.process.code}#{self.step_number} {self.title}"

    @property
    def data_binding_label(self) -> str:
        from .process_data import binding_label

        return binding_label(self.data_binding)


