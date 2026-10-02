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


class WeeklyPlan(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "در انتظار تأیید"
        APPROVED = "approved", "تأییدشده"

    class PlanningMode(models.TextChoices):
        MANUAL = "manual", "دستی"
        SYSTEMIC = "systemic", "سیستمی"

    program_number = models.CharField("شماره برنامه", max_length=30, unique=True)
    date = jmodels.jDateField("تاریخ برنامه‌ریزی", unique=True)
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

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "برنامه هفتگی"
        verbose_name_plural = "برنامه‌ریزی هفتگی"

    def __str__(self) -> str:
        from .utils import format_jdate
        return f"برنامه {self.program_number} — {format_jdate(self.date)}"

    @property
    def weekday_name(self) -> str:
        return persian_weekday(self.date)


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

    class Meta:
        verbose_name = "کالای برنامه"
        verbose_name_plural = "کالاهای برنامه"

    def __str__(self) -> str:
        return f"{self.product.name} @ {self.machine}"

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

    class Meta:
        verbose_name = "ردیف تولید"
        verbose_name_plural = "ردیف‌های تولید"

    def __str__(self) -> str:
        return f"{self.production_type} — {self.quantity}"

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


class PhysicalMoldReservationQuerySet(SnapshotWriteGuardQuerySet):
    immutable_fields = frozenset(
        {
            "approval_revision", "approval_revision_id", "source_path", "source_path_id",
            "requirement", "requirement_id", "machine", "machine_id", "physical_mold",
            "physical_mold_id", "approved_planned_share_quantity", "reserved_start_date",
            "reserved_end_date", "compatibility_status_snapshot", "mold_status_snapshot",
            "estimated_ready_date_snapshot",
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


