import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.contrib.postgres.constraints import ExclusionConstraint


def make_approval_revision_nullable(apps, schema_editor):
    Reservation = apps.get_model("planning", "PhysicalMoldReservation")
    old_field = Reservation._meta.get_field("approval_revision")
    new_field = old_field.clone()
    new_field.null = True
    new_field.blank = True
    new_field.remote_field.model = apps.get_model("planning", "PlanApprovalRevision")
    new_field.remote_field.field_name = "id"
    new_field.set_attributes_from_name(old_field.name)
    original_constraints = Reservation._meta.constraints
    if schema_editor.connection.vendor == "sqlite":
        Reservation._meta.constraints = [
            constraint
            for constraint in original_constraints
            if not isinstance(constraint, ExclusionConstraint)
        ]
    try:
        schema_editor.alter_field(Reservation, old_field, new_field)
    finally:
        Reservation._meta.constraints = original_constraints


def make_approval_revision_required(apps, schema_editor):
    Reservation = apps.get_model("planning", "PhysicalMoldReservation")
    alias = schema_editor.connection.alias
    if Reservation.objects.using(alias).filter(approval_revision__isnull=True).exists():
        raise RuntimeError(
            "Cannot reverse planning.0018 while path-level reservations without "
            "a whole-plan approval revision exist."
        )
    old_field = Reservation._meta.get_field("approval_revision")
    new_field = old_field.clone()
    new_field.null = False
    new_field.blank = False
    new_field.remote_field.model = apps.get_model("planning", "PlanApprovalRevision")
    new_field.remote_field.field_name = "id"
    new_field.set_attributes_from_name(old_field.name)
    original_constraints = Reservation._meta.constraints
    if schema_editor.connection.vendor == "sqlite":
        Reservation._meta.constraints = [
            constraint
            for constraint in original_constraints
            if not isinstance(constraint, ExclusionConstraint)
        ]
    try:
        schema_editor.alter_field(Reservation, old_field, new_field)
    finally:
        Reservation._meta.constraints = original_constraints


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0017_active_mold_reservation_exclusion"),
        ("production", "0013_operational_edit_event"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    make_approval_revision_nullable,
                    make_approval_revision_required,
                )
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="physicalmoldreservation",
                    name="approval_revision",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mold_reservations",
                        to="planning.planapprovalrevision",
                        verbose_name="بازنگری تأیید",
                    ),
                )
            ],
        ),
        migrations.CreateModel(
            name="PathOperationalApproval",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sequence", models.PositiveIntegerField()),
                ("action", models.CharField(choices=[("approved", "تأیید و فعال‌سازی"), ("edited", "ویرایش عملیاتی"), ("cancelled", "لغو عملیاتی")], max_length=16)),
                ("path_fingerprint", models.CharField(db_index=True, max_length=64)),
                ("before_snapshot", models.JSONField(blank=True, default=dict)),
                ("after_snapshot", models.JSONField(default=dict)),
                ("manager_confirmations", models.JSONField(blank=True, default=list)),
                ("reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="path_operational_approvals", to=settings.AUTH_USER_MODEL)),
                ("execution_path", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="operational_approvals", to="production.productionexecutionpath")),
                ("planned_path", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="operational_approvals", to="planning.plannedproductionpath")),
                ("reservation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="operational_approvals", to="planning.physicalmoldreservation")),
            ],
            options={"ordering": ["planned_path_id", "sequence"]},
        ),
        migrations.AddConstraint(
            model_name="pathoperationalapproval",
            constraint=models.UniqueConstraint(fields=("planned_path", "sequence"), name="uniq_path_operational_approval_sequence"),
        ),
        migrations.AddConstraint(
            model_name="pathoperationalapproval",
            constraint=models.CheckConstraint(condition=models.Q(sequence__gt=0), name="path_operational_approval_sequence_positive"),
        ),
    ]
