"""Daily production logging for fittings (عددی) and pipes (شاخه/کلاف/متر)."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django_jalali.db import models as jmodels

from catalog.models import Machine, Product, ProductionUnit


def _apply_material_usage(record, product) -> None:
    """Auto-fill material_used/material_scrap (kg) from the product weight.

    material = weight(grams) × quantity / 1000. When no product/weight is
    available the values are left at zero.
    """
    weight = getattr(product, "unit_weight_grams", None) or Decimal("0")
    record.material_used = (Decimal(weight) * record.produced_quantity) / Decimal("1000")
    record.material_scrap = (Decimal(weight) * record.scrap_quantity) / Decimal("1000")


class BaseProduction(models.Model):
    """Fields shared by every daily production record."""

    unit = models.ForeignKey(
        ProductionUnit, on_delete=models.PROTECT, related_name="+", verbose_name="واحد تولیدی"
    )
    date = jmodels.jDateField("تاریخ")
    planned_quantity = models.PositiveIntegerField("مقدار برنامه‌ریزی‌شده", default=0)
    produced_quantity = models.PositiveIntegerField("مقدار تولیدشده", default=0)
    scrap_quantity = models.PositiveIntegerField("مقدار ضایعات", default=0)
    # Material usage is auto-computed from the product weight and quantities
    # (see save()); it is shown only in reports, never entered by hand.
    material_used = models.DecimalField(
        "مواد مصرف‌شده (kg)", max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    material_scrap = models.DecimalField(
        "مواد ضایعات‌شده (kg)", max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    deviation_reason = models.ForeignKey(
        "catalog.DeviationReason",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="دلیل انحراف",
    )
    description = models.TextField("توضیحات", blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    @property
    def deviation(self) -> int:
        """Produced minus planned (negative means under plan)."""
        return self.produced_quantity - self.planned_quantity


class FittingProduction(BaseProduction):
    """Daily production of a fitting on an injection machine."""

    machine = models.ForeignKey(
        Machine, on_delete=models.PROTECT, related_name="fitting_records", verbose_name="دستگاه"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="fitting_records", verbose_name="نام محصول"
    )
    shot_cycle = models.PositiveIntegerField("سیکل تولید یک‌ضرب (ثانیه)", default=0)
    active_cavities = models.PositiveSmallIntegerField("تعداد حفره فعال", default=1)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "تولید اتصالات"
        verbose_name_plural = "تولید روزانه اتصالات"

    def __str__(self) -> str:
        return f"{self.date} — {self.product.name} ({self.machine})"

    def save(self, *args, **kwargs):
        _apply_material_usage(self, self.product)
        super().save(*args, **kwargs)


class PipeProduction(BaseProduction):
    """Daily production of a pipe/tape on an extruder line.

    Extra fields adapt to the selected pipe type; unused ones stay blank.
    """

    class ThicknessUnit(models.TextChoices):
        MM = "mm", "میلی‌متر"
        MICRON = "micron", "میکرون"

    class MaterialGrade(models.TextChoices):
        PE80 = "PE80", "PE80"
        PE100 = "PE100", "PE100"
        PE32 = "PE32", "PE32"
        PE40 = "PE40", "PE40"

    class SocketLength(models.TextChoices):
        L30_1S = "30cm_1s", "۳۰ سانتی یک‌سر سوکت"
        L50_1S = "50cm_1s", "نیم‌متری یک‌سر سوکت"
        L100_1S = "100cm_1s", "۱ متری یک‌سر سوکت"
        L200_1S = "200cm_1s", "۲ متری یک‌سر سوکت"
        L300_1S = "300cm_1s", "۳ متری یک‌سر سوکت"
        L50_2S = "50cm_2s", "نیم‌متری دوسر سوکت"
        L100_2S = "100cm_2s", "۱ متری دوسر سوکت"
        L200_2S = "200cm_2s", "۲ متری دوسر سوکت"
        L300_2S = "300cm_2s", "۳ متری دوسر سوکت"

    line = models.ForeignKey(
        Machine, on_delete=models.PROTECT, related_name="pipe_records", verbose_name="خط"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="pipe_records",
        null=True,
        blank=True,
        verbose_name="نام محصول",
    )
    pipe_type = models.CharField("نوع محصول", max_length=60)
    size = models.CharField("سایز", max_length=40, blank=True)

    # Push-fit specifics
    socket_length = models.CharField(
        "اندازه لوله (سوکت)", max_length=20, choices=SocketLength.choices, blank=True
    )
    bling_machine = models.ForeignKey(
        Machine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bling_records",
        limit_choices_to={"machine_type": "bling"},
        verbose_name="دستگاه بلینگ",
    )

    # Sewage / water pipe specifics
    nominal_pressure = models.CharField("فشار اسمی", max_length=20, blank=True)
    thickness = models.DecimalField(
        "ضخامت", max_digits=8, decimal_places=2, null=True, blank=True
    )
    thickness_unit = models.CharField(
        "واحد ضخامت", max_length=10, choices=ThicknessUnit.choices, default=ThicknessUnit.MM
    )
    material_grade = models.CharField(
        "نوع مواد", max_length=20, choices=MaterialGrade.choices, blank=True
    )

    # Drip pipe / tape specifics
    dripper_spec = models.CharField("مشخصات دریپر", max_length=120, blank=True)
    nominal_flow = models.CharField("دبی اسمی", max_length=40, blank=True)
    dripper_spacing = models.PositiveIntegerField(
        "فاصله دریپر (cm)", null=True, blank=True
    )
    length_meters = models.PositiveIntegerField("متراژ تولید (m)", null=True, blank=True)

    # Corrugated specifics
    color = models.CharField("رنگ", max_length=40, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "تولید لوله"
        verbose_name_plural = "تولید روزانه لوله‌ها"

    def __str__(self) -> str:
        return f"{self.date} — {self.pipe_type} ({self.line})"

    def save(self, *args, **kwargs):
        # Keep pipe_type in sync with the chosen product's subgroup.
        if self.product_id and self.product.subgroup_id:
            self.pipe_type = self.product.subgroup.name
        _apply_material_usage(self, self.product)
        super().save(*args, **kwargs)


class ProductionProgram(models.Model):
    """Execution state of one approved weekly-plan item (a mold-change program).

    Created when its plan is approved; driven through its lifecycle by the
    «تعیین وضعیت» control.
    """

    class Status(models.TextChoices):
        AWAITING = "awaiting", "در انتظار تولید"
        RUNNING = "running", "در حال تولید"
        TEMP_STOP = "temp_stop", "توقف موقت"
        FINISHED = "finished", "اتمام تولید"

    class ChangeType(models.TextChoices):
        SETUP = "setup", "راه‌اندازی"
        CHANGE = "change", "تغییر برنامه"

    item = models.OneToOneField(
        "planning.WeeklyPlanItem", on_delete=models.CASCADE, related_name="program"
    )
    status = models.CharField(
        "وضعیت", max_length=12, choices=Status.choices, default=Status.AWAITING
    )
    change_type = models.CharField(
        "نوع شروع", max_length=8, choices=ChangeType.choices, blank=True
    )
    change_reason = models.ForeignKey(
        "catalog.ProgramChangeReason", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="دلیل تغییر برنامه",
    )
    # Which production line (نوع تولید) is being run: 1 = نوع اول, 2 = نوع دوم.
    production_type = models.PositiveSmallIntegerField("نوع تولید", default=1)
    mold = models.ForeignKey(
        "catalog.MoldOption", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="انتخاب قالب",
    )

    start_date = jmodels.jDateField("تاریخ شروع", null=True, blank=True)
    start_time = models.TimeField("ساعت شروع", null=True, blank=True)
    stop_date = jmodels.jDateField("تاریخ پایان/توقف", null=True, blank=True)
    stop_time = models.TimeField("ساعت پایان/توقف", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "برنامه تولید"
        verbose_name_plural = "برنامه‌های تولید"

    def __str__(self) -> str:
        return f"{self.resolved_uid} — {self.item.product.name}"

    # --- helpers -------------------------------------------------------
    @property
    def resolved_uid(self) -> str:
        """UID for the currently selected production type (14-digit scheme)."""
        line = self.line
        if line is not None and getattr(line, "uid", ""):
            return line.uid
        return self.item.uid_for_type(self.production_type or 1)

    @property
    def line(self):
        """The WeeklyPlanLine for the selected production_type (1-based)."""
        lines = list(self.item.lines.all())
        idx = (self.production_type or 1) - 1
        return lines[idx] if 0 <= idx < len(lines) else (lines[0] if lines else None)

    @property
    def default_cycle(self) -> int:
        line = self.line
        return int(line.cycle) if line and line.cycle else int(self.item.product.last_cycle or 0)

    @property
    def machine_label(self) -> str:
        m = self.item.machine
        return f"دستگاه {m.number} واحد {m.unit.number}"

    def start_datetime(self):
        if self.start_date and self.start_time:
            from .timeutils import to_gregorian
            return to_gregorian(self.start_date, self.start_time)
        return None

    def stop_datetime(self):
        if self.stop_date and self.stop_time:
            from .timeutils import to_gregorian
            return to_gregorian(self.stop_date, self.stop_time)
        return None


class ProductionRequirementProgram(models.Model):
    """Stable operational family for one planning requirement."""

    requirement = models.OneToOneField(
        "planning.WeeklyPlanLine",
        on_delete=models.PROTECT,
        related_name="requirement_program",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_requirement_programs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "برنامه نیاز تولید"
        verbose_name_plural = "برنامه‌های نیاز تولید"

    @property
    def program_number(self) -> str:
        return f"PP-{self.pk:05d}" if self.pk is not None else "PP-NEW"

    def __str__(self) -> str:
        return self.program_number


class ProductionExecutionPathQuerySet(models.QuerySet):
    immutable_fields = frozenset({"requirement_program", "requirement_program_id", "initial_good_target"})
    started_immutable_fields = frozenset(
        {"machine", "machine_id", "physical_mold", "physical_mold_id", "current_good_target", "first_production_at"}
    )

    def update(self, **kwargs):
        if "status" in kwargs or "status_changed_at" in kwargs or "first_production_at" in kwargs:
            raise ValueError("Execution lifecycle fields require the canonical execution service.")
        if self.immutable_fields.intersection(kwargs):
            raise ValueError("Execution path identity and initial target are immutable.")
        if self.started_immutable_fields.intersection(kwargs) and self.filter(first_production_at__isnull=False).exists():
            raise ValueError("Started execution path evidence is immutable.")
        return super().update(**kwargs)

    def _update_for_execution_service(self, **kwargs):
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        objs = tuple(objs)
        if {"status", "status_changed_at", "first_production_at"}.intersection(fields):
            raise ValueError("Execution lifecycle fields require the canonical execution service.")
        if self.immutable_fields.intersection(fields):
            raise ValueError("Execution path identity and initial target are immutable.")
        if self.started_immutable_fields.intersection(fields):
            pks = [obj.pk for obj in objs if obj.pk is not None]
            if pks and self.model.objects.using(self.db).filter(pk__in=pks, first_production_at__isnull=False).exists():
                raise ValueError("Started execution path evidence is immutable.")
        return super().bulk_update(objs, fields, **kwargs)


class ProductionExecutionPath(models.Model):
    class Status(models.TextChoices):
        PREPARING = "preparing", "در حال آماده‌سازی"
        AWAITING = "awaiting", "در انتظار تولید"
        RUNNING = "running", "در حال تولید"
        TEMPORARY_STOP = "temporary_stop", "توقف موقت"
        FULL_STOP = "full_stop", "توقف کامل"
        STATISTICS_COMPLETED = "statistics_completed", "آمار تکمیل‌شده"
        SUPERSEDED = "superseded", "جایگزین‌شده"
        CANCELLED = "cancelled", "لغوشده"

    requirement_program = models.ForeignKey(
        ProductionRequirementProgram, on_delete=models.PROTECT, related_name="execution_paths"
    )
    machine = models.ForeignKey(
        "catalog.Machine", on_delete=models.PROTECT, related_name="production_execution_paths"
    )
    physical_mold = models.ForeignKey(
        "catalog.PhysicalMold", on_delete=models.PROTECT, related_name="production_execution_paths"
    )
    initial_good_target = models.PositiveIntegerField()
    current_good_target = models.PositiveIntegerField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PREPARING)
    first_production_at = models.DateTimeField(null=True, blank=True)
    status_changed_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_production_execution_paths",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="updated_production_execution_paths",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductionExecutionPathQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(initial_good_target__gt=0), name="execution_path_initial_target_positive"),
            models.CheckConstraint(condition=models.Q(current_good_target__gt=0), name="execution_path_current_target_positive"),
            models.UniqueConstraint(
                fields=["requirement_program", "machine", "physical_mold"],
                condition=models.Q(first_production_at__isnull=True, status__in=["preparing", "awaiting"]),
                name="uniq_reusable_unstarted_execution_path",
            ),
        ]
        indexes = [
            models.Index(fields=["physical_mold", "first_production_at"], name="exec_path_mold_started_idx"),
            models.Index(fields=["status", "machine"], name="exec_path_status_machine_idx"),
        ]

    def clean(self):
        super().clean()
        if not self.initial_good_target or not self.current_good_target:
            raise ValidationError("Execution targets must be positive.")

    def save(self, *args, **kwargs):
        database = kwargs.get("using") or self._state.db or "default"
        if self.pk is not None:
            previous = type(self).objects.using(database).get(pk=self.pk)
            if previous.requirement_program_id != self.requirement_program_id or previous.initial_good_target != self.initial_good_target:
                raise ValueError("Execution path identity and initial target are immutable.")
            if previous.status != self.status or previous.status_changed_at != self.status_changed_at:
                raise ValueError("Execution lifecycle fields require the canonical execution service.")
            if previous.first_production_at is not None:
                protected = ("machine_id", "physical_mold_id", "current_good_target", "first_production_at")
                if any(getattr(previous, name) != getattr(self, name) for name in protected):
                    raise ValueError("Started execution path evidence is immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def cumulative_good(self) -> int:
        return int(self.day_entries.aggregate(total=Sum("good_pieces"))["total"] or 0)

    @property
    def good_remaining(self) -> int:
        return max(self.current_good_target - self.cumulative_good, 0)

    def __str__(self) -> str:
        return f"Path #{self.pk}" if self.pk else "Path #NEW"


class AppendOnlyExecutionEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Production execution events are immutable.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Production execution events are immutable.")

    def delete(self):
        raise ValueError("Production execution events are immutable.")

    def _delete_for_controlled_restore(self):
        """Internal backup-restore hook; never exposed through application flows."""
        return super().delete()


class ProductionExecutionEvent(models.Model):
    class EventType(models.TextChoices):
        ACTIVATED = "activated", "فعال‌سازی"
        FIRST_PRODUCTION = "first_production", "اولین تولید"
        PRODUCTION_ACCEPTED = "production_accepted", "ثبت تولید پذیرفته‌شده"
        TEMPORARY_STOP = "temporary_stop", "توقف موقت"
        RESUME = "resume", "ادامه تولید"
        FULL_STOP = "full_stop", "توقف کامل"
        MANAGER_REOPEN = "manager_reopen", "بازگشایی مدیر"
        STATISTICS_COMPLETED = "statistics_completed", "تکمیل آمار"
        STATISTICS_REOPENED = "statistics_reopened", "بازگشایی آمار"
        SUPERSEDED = "superseded", "جایگزینی"
        CANCELLED = "cancelled", "لغو"

    execution_path = models.ForeignKey(
        ProductionExecutionPath, on_delete=models.PROTECT, related_name="events"
    )
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    occurred_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="production_execution_events",
    )
    actor_identifier_snapshot = models.CharField(max_length=150, blank=True)
    old_status = models.CharField(max_length=24, choices=ProductionExecutionPath.Status.choices, blank=True)
    new_status = models.CharField(max_length=24, choices=ProductionExecutionPath.Status.choices, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = AppendOnlyExecutionEventQuerySet.as_manager()

    class Meta:
        ordering = ["occurred_at", "id"]
        indexes = [
            models.Index(fields=["execution_path", "occurred_at"], name="exec_event_path_time_idx"),
            models.Index(fields=["event_type", "occurred_at"], name="exec_event_type_time_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Production execution events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Production execution events are immutable.")


class ProductionDayEntryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if self.filter(execution_path__isnull=False).exists():
            raise ValueError("Path-based production entries are append-only.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        objs = tuple(objs)
        pks = [obj.pk for obj in objs if obj.pk is not None]
        if pks and self.model.objects.using(self.db).filter(pk__in=pks, execution_path__isnull=False).exists():
            raise ValueError("Path-based production entries are append-only.")
        return super().bulk_update(objs, fields, **kwargs)

    def delete(self):
        if self.filter(execution_path__isnull=False).exists():
            raise ValueError("Path-based production entries are append-only.")
        return super().delete()

    def _delete_for_controlled_restore(self):
        return super().delete()


class ProductionDayEntry(models.Model):
    """Recorded production statistics for one full work-day of a program."""

    program = models.ForeignKey(
        ProductionProgram, on_delete=models.CASCADE, related_name="entries", null=True, blank=True,
        verbose_name="برنامه تولید",
    )
    execution_path = models.ForeignKey(
        ProductionExecutionPath, on_delete=models.PROTECT, related_name="day_entries",
        null=True, blank=True, verbose_name="مسیر اجرای تولید",
    )
    date = jmodels.jDateField("تاریخ")
    produced_quantity = models.PositiveIntegerField("مقدار تولیدشده (ضرب)", default=0)
    scrap_quantity = models.PositiveIntegerField("مقدار ضایعات", default=0)
    cycle = models.PositiveIntegerField("سیکل یک‌ضرب (ثانیه)", default=0)
    active_cavities = models.PositiveSmallIntegerField("تعداد حفره فعال", default=1)
    shots = models.PositiveIntegerField(null=True, blank=True)
    gross_pieces = models.PositiveIntegerField(null=True, blank=True)
    good_pieces = models.PositiveIntegerField(null=True, blank=True)

    # Denormalised, computed on save from the shift/time model.
    planned_quantity = models.PositiveIntegerField("مقدار برنامه‌ریزی‌شده (ضرب)", default=0)
    active_seconds = models.PositiveIntegerField("زمان فعال (ثانیه)", default=0)

    deviation_reason = models.ForeignKey(
        "catalog.DeviationReason", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="دلیل انحراف",
    )
    description = models.TextField("توضیحات", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProductionDayEntryQuerySet.as_manager()

    class Meta:
        ordering = ["date"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(program__isnull=False, execution_path__isnull=True) | models.Q(program__isnull=True, execution_path__isnull=False)),
                name="production_day_entry_exactly_one_owner",
            ),
            models.UniqueConstraint(fields=["program", "date"], condition=models.Q(program__isnull=False), name="uniq_legacy_program_day"),
            models.UniqueConstraint(fields=["execution_path", "date"], condition=models.Q(execution_path__isnull=False), name="uniq_execution_path_day"),
            models.CheckConstraint(
                condition=(models.Q(execution_path__isnull=True) | models.Q(shots__isnull=False, gross_pieces__isnull=False, good_pieces__isnull=False, active_cavities__gt=0, scrap_quantity__gte=0)),
                name="path_day_entry_required_quantities",
            ),
            models.CheckConstraint(condition=(models.Q(execution_path__isnull=True) | models.Q(gross_pieces=models.F("shots") * models.F("active_cavities"))), name="path_day_entry_gross_consistent"),
            models.CheckConstraint(condition=(models.Q(execution_path__isnull=True) | models.Q(good_pieces=models.F("gross_pieces") - models.F("scrap_quantity"))), name="path_day_entry_good_consistent"),
            models.CheckConstraint(condition=(models.Q(execution_path__isnull=True) | models.Q(scrap_quantity__lte=models.F("gross_pieces"))), name="path_day_entry_scrap_not_over_gross"),
        ]
        verbose_name = "آمار تولید روزانه"
        verbose_name_plural = "آمار تولید روزانه"

    def __str__(self) -> str:
        owner = self.program.resolved_uid if self.program_id else str(self.execution_path)
        return f"{owner} — {self.date}"

    @property
    def deviation(self) -> int:
        """Planned minus produced (positive means production is behind plan)."""
        return self.planned_quantity - self.produced_quantity

    def recompute(self):
        if self.execution_path_id:
            return
        from .timeutils import day_active_seconds, expected_shots
        start = self.program.start_datetime()
        stop = self.program.stop_datetime()
        self.active_seconds = day_active_seconds(start, stop, self.date)
        self.planned_quantity = expected_shots(self.active_seconds, self.cycle)

    def save(self, *args, **kwargs):
        database = kwargs.get("using") or self._state.db or "default"
        if self._state.adding and self.execution_path_id is not None and not getattr(
            self, "_execution_service_create", False
        ):
            raise ValueError("Path-based production entries require the canonical execution service.")
        if self.pk is not None:
            previous = type(self).objects.using(database).get(pk=self.pk)
            if previous.execution_path_id is not None:
                raise ValueError("Path-based production entries are append-only.")
        self.recompute()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.execution_path_id is not None:
            raise ValueError("Path-based production entries are append-only.")
        return super().delete(*args, **kwargs)


class ProductionHistoryRecord(models.Model):
    """Archived / Excel-imported production history row, sorted by program UID.

    Live planning programs also appear in «سوابق تولید»; this model holds rows
    transferred from Excel (or other imports) that are not yet linked to a
    WeeklyPlanItem / ProductionProgram.
    """

    program_uid = models.CharField("شناسه تعویض", max_length=32, db_index=True)
    plan_number = models.CharField("شماره برنامه", max_length=40, blank=True)
    plan_date = models.DateField("تاریخ برنامه‌ریزی", null=True, blank=True)
    mold_change_date = models.DateField("تاریخ تعویض قالب", null=True, blank=True)
    unit_number = models.PositiveSmallIntegerField("شماره واحد", null=True, blank=True)
    machine_number = models.CharField("شماره دستگاه", max_length=40, blank=True)
    product_code = models.CharField("کد کالا", max_length=80, blank=True)
    product_name = models.CharField("نام جنس", max_length=200, blank=True)
    mold_name = models.CharField("نام قالب", max_length=200, blank=True)
    mold_number = models.CharField("شماره قالب", max_length=80, blank=True)
    unique_code = models.CharField("کد یکتا", max_length=80, blank=True)
    material = models.CharField("مواد", max_length=120, blank=True)
    color = models.CharField("رنگ", max_length=80, blank=True)
    sequence = models.PositiveSmallIntegerField("ترتیب", null=True, blank=True)
    plan_start_date = models.DateField("تاریخ شروع برنامه", null=True, blank=True)
    actual_start_date = models.DateField("تاریخ شروع واقعی", null=True, blank=True)
    actual_end_date = models.DateField("تاریخ پایان تولید", null=True, blank=True)
    planned_qty = models.IntegerField("مقدار تولید برنامه", null=True, blank=True)
    produced_qty = models.IntegerField("مقدار تولید واقعی", null=True, blank=True)
    planned_cycle = models.IntegerField("سیکل تولید برنامه", null=True, blank=True)
    last_cycle = models.IntegerField("آخرین سیکل تولید", null=True, blank=True)
    planned_hours = models.DecimalField(
        "ساعت تولید برنامه", max_digits=10, decimal_places=2, null=True, blank=True
    )
    active_cavities = models.PositiveSmallIntegerField("تعداد حفره فعال", null=True, blank=True)
    last_cavities = models.PositiveSmallIntegerField("آخرین وضعیت حفره", null=True, blank=True)
    scrap_qty = models.IntegerField("ضایعات تولید", null=True, blank=True)
    status = models.CharField("وضعیت", max_length=80, blank=True)
    notes = models.TextField("توضیحات", blank=True)
    source_table_name = models.CharField("نام جدول مبدأ", max_length=200, blank=True)
    transferred_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="انتقال‌دهنده",
    )
    extra = models.JSONField("اطلاعات تکمیلی", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["program_uid", "id"]
        verbose_name = "سابقه تولید"
        verbose_name_plural = "سوابق تولید (آرشیو)"

    def __str__(self) -> str:
        return f"{self.program_uid} — {self.product_name or self.product_code or '—'}"


class ProductionStoppage(models.Model):
    """A stoppage attached to a fitting or pipe production record."""

    reason = models.ForeignKey(
        "catalog.StoppageReason", on_delete=models.PROTECT, related_name="stoppages",
        verbose_name="دلیل توقف",
    )
    minutes = models.PositiveIntegerField("مدت توقف (دقیقه)", default=0)
    note = models.CharField("توضیح تکمیلی", max_length=255, blank=True)

    fitting = models.ForeignKey(
        FittingProduction,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="stoppages",
    )
    pipe = models.ForeignKey(
        PipeProduction,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="stoppages",
    )

    class Meta:
        verbose_name = "توقف"
        verbose_name_plural = "توقفات"

    def __str__(self) -> str:
        return f"{self.reason} ({self.minutes} دقیقه)"
