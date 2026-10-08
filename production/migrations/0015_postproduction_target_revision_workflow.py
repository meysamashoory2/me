import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def preflight_target_change_deviation_uniqueness(apps, schema_editor):
    alias = schema_editor.connection.alias
    Deviation = apps.get_model("production", "ProductionDeviation")
    duplicates = (
        Deviation.objects.using(alias)
        .filter(category="target_change", target_revision__isnull=False)
        .values("target_revision_id")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )
    if duplicates.exists():
        raise RuntimeError(
            "Cannot enforce C2 target-deviation uniqueness while duplicate "
            "historical target-change evidence exists."
        )


def refuse_reverse_with_target_decisions(apps, schema_editor):
    alias = schema_editor.connection.alias
    Decision = apps.get_model("production", "ProductionTargetDecision")
    TargetRevision = apps.get_model("production", "ProductionTargetRevision")
    if Decision.objects.using(alias).exists():
        raise RuntimeError(
            "Cannot reverse the C2 workflow after immutable target decisions exist."
        )
    if TargetRevision.objects.using(alias).filter(
        source="scrap_compensation"
    ).exists():
        raise RuntimeError(
            "Cannot reverse the C2 workflow while immutable Scrap Compensation "
            "requests exist."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0030_add_scrap_compensation_reason"),
        ("production", "0014_productiontargetrevision_productiondeviation_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="productiontargetrevision",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("planning", "Planning"),
                    ("reallocation", "Target reallocation"),
                    ("scrap_compensation", "Scrap compensation"),
                ],
                max_length=24,
            ),
        ),
        migrations.CreateModel(
            name="ProductionTargetDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("outcome", models.CharField(choices=[("approved", "Approved"), ("modified", "Modified and approved"), ("rejected", "Rejected")], max_length=12)),
                ("decided_target", models.PositiveIntegerField(blank=True, null=True)),
                ("signed_delta", models.IntegerField(blank=True, null=True)),
                ("cumulative_gross_at_decision", models.PositiveIntegerField()),
                ("decided_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("note", models.TextField(blank=True)),
                ("context", models.JSONField(blank=True, default=dict)),
                ("decided_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="target_revision_decisions", to=settings.AUTH_USER_MODEL)),
                ("request", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="decision", to="production.productiontargetrevision")),
            ],
            options={"ordering": ["decided_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="productiontargetdecision",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(outcome__in=["approved", "modified"], decided_target__isnull=False, signed_delta__isnull=False)
                    | models.Q(outcome="rejected", decided_target__isnull=True, signed_delta__isnull=True)
                ),
                name="target_decision_evidence_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="productiontargetdecision",
            constraint=models.CheckConstraint(
                condition=models.Q(decided_target__isnull=True) | models.Q(decided_target__gt=0),
                name="target_decision_target_positive",
            ),
        ),
        migrations.AddIndex(
            model_name="productiontargetdecision",
            index=models.Index(fields=["outcome", "decided_at"], name="target_decision_time_idx"),
        ),
        migrations.AddField(
            model_name="productiondeviation",
            name="target_decision",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="deviation", to="production.productiontargetdecision"),
        ),
        migrations.RunPython(preflight_target_change_deviation_uniqueness, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="productiondeviation",
            constraint=models.UniqueConstraint(
                condition=models.Q(category="target_change", target_revision__isnull=False),
                fields=("target_revision",),
                name="uniq_target_change_deviation_per_request",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_target_decisions),
    ]
