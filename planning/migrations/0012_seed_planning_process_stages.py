# Generated manually — seed PDF planning process stages

from django.db import migrations

from planning.migrations._0012_process_seed_data import PROCESS_SPECS


def seed_processes(apps, schema_editor):
    PlanningProcessDefinition = apps.get_model("planning", "PlanningProcessDefinition")
    PlanningProcessStep = apps.get_model("planning", "PlanningProcessStep")
    database = schema_editor.connection.alias

    for code, title, description, entry_step_number, steps in PROCESS_SPECS:
        process, _ = PlanningProcessDefinition.objects.using(database).update_or_create(
            code=code,
            defaults={
                "title": title,
                "description": description,
                "entry_step_number": entry_step_number,
                "is_active": True,
            },
        )
        PlanningProcessStep.objects.using(database).filter(process=process).delete()
        PlanningProcessStep.objects.using(database).bulk_create(
            [
                PlanningProcessStep(
                    process=process,
                    step_number=step["step_number"],
                    title=step["title"],
                    description=step.get("description") or "",
                    kind=step["kind"],
                    question=step.get("question") or "",
                    yes_next_number=step.get("yes_next_number"),
                    no_next_number=step.get("no_next_number"),
                    next_number=step.get("next_number"),
                    data_binding=step.get("data_binding") or "none",
                    is_active=True,
                    sort_order=step["step_number"],
                )
                for step in steps
            ]
        )


def unseed(apps, schema_editor):
    PlanningProcessDefinition = apps.get_model("planning", "PlanningProcessDefinition")
    database = schema_editor.connection.alias
    PlanningProcessDefinition.objects.using(database).filter(
        code__in=["mto", "mts"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0011_planning_process_stages"),
    ]

    operations = [
        migrations.RunPython(seed_processes, unseed),
    ]
