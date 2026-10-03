import django.db.models.deletion
import django.db.models.expressions
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0016_physicalmoldreservation_execution_path"),
        ("production", "0011_execution_foundation"),
    ]

    operations = [
        migrations.AlterUniqueTogether(name="productiondayentry", unique_together=set()),
        migrations.AlterField(
            model_name="productiondayentry", name="program",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="entries", to="production.productionprogram", verbose_name="برنامه تولید"),
        ),
        migrations.AddField(model_name="productiondayentry", name="execution_path", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="day_entries", to="production.productionexecutionpath", verbose_name="مسیر اجرای تولید")),
        migrations.AddField(model_name="productiondayentry", name="shots", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="productiondayentry", name="gross_pieces", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="productiondayentry", name="good_pieces", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddConstraint(model_name="productiondayentry", constraint=models.CheckConstraint(condition=models.Q(models.Q(execution_path__isnull=True, program__isnull=False), models.Q(execution_path__isnull=False, program__isnull=True), _connector="OR"), name="production_day_entry_exactly_one_owner")),
        migrations.AddConstraint(model_name="productiondayentry", constraint=models.UniqueConstraint(fields=("program", "date"), condition=models.Q(program__isnull=False), name="uniq_legacy_program_day")),
        migrations.AddConstraint(model_name="productiondayentry", constraint=models.UniqueConstraint(fields=("execution_path", "date"), condition=models.Q(execution_path__isnull=False), name="uniq_execution_path_day")),
        migrations.AddConstraint(model_name="productiondayentry", constraint=models.CheckConstraint(condition=models.Q(("execution_path__isnull", True), models.Q(active_cavities__gt=0, good_pieces__isnull=False, gross_pieces__isnull=False, scrap_quantity__gte=0, shots__isnull=False), _connector="OR"), name="path_day_entry_required_quantities")),
        migrations.AddConstraint(model_name="productiondayentry", constraint=models.CheckConstraint(condition=models.Q(("execution_path__isnull", True), ("gross_pieces", django.db.models.expressions.CombinedExpression(models.F("shots"), "*", models.F("active_cavities"))), _connector="OR"), name="path_day_entry_gross_consistent")),
        migrations.AddConstraint(model_name="productiondayentry", constraint=models.CheckConstraint(condition=models.Q(("execution_path__isnull", True), ("good_pieces", django.db.models.expressions.CombinedExpression(models.F("gross_pieces"), "-", models.F("scrap_quantity"))), _connector="OR"), name="path_day_entry_good_consistent")),
        migrations.AddConstraint(model_name="productiondayentry", constraint=models.CheckConstraint(condition=models.Q(("execution_path__isnull", True), ("scrap_quantity__lte", models.F("gross_pieces")), _connector="OR"), name="path_day_entry_scrap_not_over_gross")),
    ]
