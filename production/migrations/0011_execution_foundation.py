import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0028_physicalmold_moldmachineperformance_and_more"),
        ("planning", "0015_planapprovaldecision_approval_fingerprint_and_more"),
        ("production", "0010_pipe_socket_length_200_2s"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductionRequirementProgram",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_requirement_programs", to=settings.AUTH_USER_MODEL)),
                ("requirement", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="requirement_program", to="planning.weeklyplanline")),
            ],
            options={"verbose_name": "برنامه نیاز تولید", "verbose_name_plural": "برنامه‌های نیاز تولید"},
        ),
        migrations.CreateModel(
            name="ProductionExecutionPath",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("initial_good_target", models.PositiveIntegerField()),
                ("current_good_target", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("preparing", "در حال آماده‌سازی"), ("awaiting", "در انتظار تولید"), ("running", "در حال تولید"), ("temporary_stop", "توقف موقت"), ("full_stop", "توقف کامل"), ("statistics_completed", "آمار تکمیل‌شده"), ("superseded", "جایگزین‌شده"), ("cancelled", "لغوشده")], default="preparing", max_length=24)),
                ("first_production_at", models.DateTimeField(blank=True, null=True)),
                ("status_changed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_production_execution_paths", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_production_execution_paths", to=settings.AUTH_USER_MODEL)),
                ("machine", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="production_execution_paths", to="catalog.machine")),
                ("physical_mold", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="production_execution_paths", to="catalog.physicalmold")),
                ("requirement_program", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="execution_paths", to="production.productionrequirementprogram")),
            ],
        ),
        migrations.CreateModel(
            name="ProductionExecutionEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("activated", "فعال‌سازی"), ("first_production", "اولین تولید"), ("production_accepted", "ثبت تولید پذیرفته‌شده"), ("temporary_stop", "توقف موقت"), ("resume", "ادامه تولید"), ("full_stop", "توقف کامل"), ("manager_reopen", "بازگشایی مدیر"), ("statistics_completed", "تکمیل آمار"), ("statistics_reopened", "بازگشایی آمار"), ("superseded", "جایگزینی"), ("cancelled", "لغو")], max_length=24)),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("actor_identifier_snapshot", models.CharField(blank=True, max_length=150)),
                ("old_status", models.CharField(blank=True, choices=[("preparing", "در حال آماده‌سازی"), ("awaiting", "در انتظار تولید"), ("running", "در حال تولید"), ("temporary_stop", "توقف موقت"), ("full_stop", "توقف کامل"), ("statistics_completed", "آمار تکمیل‌شده"), ("superseded", "جایگزین‌شده"), ("cancelled", "لغوشده")], max_length=24)),
                ("new_status", models.CharField(blank=True, choices=[("preparing", "در حال آماده‌سازی"), ("awaiting", "در انتظار تولید"), ("running", "در حال تولید"), ("temporary_stop", "توقف موقت"), ("full_stop", "توقف کامل"), ("statistics_completed", "آمار تکمیل‌شده"), ("superseded", "جایگزین‌شده"), ("cancelled", "لغوشده")], max_length=24)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="production_execution_events", to=settings.AUTH_USER_MODEL)),
                ("execution_path", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="production.productionexecutionpath")),
            ],
            options={"ordering": ["occurred_at", "id"]},
        ),
        migrations.AddIndex(model_name="productionexecutionevent", index=models.Index(fields=["execution_path", "occurred_at"], name="exec_event_path_time_idx")),
        migrations.AddIndex(model_name="productionexecutionevent", index=models.Index(fields=["event_type", "occurred_at"], name="exec_event_type_time_idx")),
        migrations.AddIndex(model_name="productionexecutionpath", index=models.Index(fields=["physical_mold", "first_production_at"], name="exec_path_mold_started_idx")),
        migrations.AddIndex(model_name="productionexecutionpath", index=models.Index(fields=["status", "machine"], name="exec_path_status_machine_idx")),
        migrations.AddConstraint(model_name="productionexecutionpath", constraint=models.CheckConstraint(condition=models.Q(initial_good_target__gt=0), name="execution_path_initial_target_positive")),
        migrations.AddConstraint(model_name="productionexecutionpath", constraint=models.CheckConstraint(condition=models.Q(current_good_target__gt=0), name="execution_path_current_target_positive")),
        migrations.AddConstraint(model_name="productionexecutionpath", constraint=models.UniqueConstraint(fields=("requirement_program", "machine", "physical_mold"), condition=models.Q(first_production_at__isnull=True, status__in=["preparing", "awaiting"]), name="uniq_reusable_unstarted_execution_path")),
    ]
