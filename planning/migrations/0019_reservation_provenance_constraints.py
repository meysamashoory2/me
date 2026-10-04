from django.contrib.postgres.constraints import ExclusionConstraint
from django.db import migrations, models


PROVENANCE_CONSTRAINT = models.CheckConstraint(
    condition=(
        models.Q(approval_revision__isnull=False)
        | models.Q(source_path__isnull=False)
    ),
    name="mold_reservation_has_provenance",
)

ACTIVE_EXECUTION_CONSTRAINT = models.CheckConstraint(
    condition=(
        ~models.Q(status="active", approval_revision__isnull=True)
        | models.Q(
            source_path__isnull=False,
            execution_path__isnull=False,
        )
    ),
    name="active_path_reservation_has_execution",
)

CONSTRAINTS = (PROVENANCE_CONSTRAINT, ACTIVE_EXECUTION_CONSTRAINT)


def add_provenance_constraints(apps, schema_editor):
    Reservation = apps.get_model("planning", "PhysicalMoldReservation")
    if schema_editor.connection.vendor != "sqlite":
        for constraint in CONSTRAINTS:
            schema_editor.add_constraint(Reservation, constraint)
        return

    original_constraints = Reservation._meta.constraints
    Reservation._meta.constraints = [
        constraint
        for constraint in original_constraints
        if not isinstance(constraint, ExclusionConstraint)
    ] + list(CONSTRAINTS)
    try:
        schema_editor._remake_table(Reservation)
    finally:
        Reservation._meta.constraints = original_constraints


def remove_provenance_constraints(apps, schema_editor):
    Reservation = apps.get_model("planning", "PhysicalMoldReservation")
    if schema_editor.connection.vendor != "sqlite":
        for constraint in reversed(CONSTRAINTS):
            schema_editor.remove_constraint(Reservation, constraint)
        return

    original_constraints = Reservation._meta.constraints
    removed_names = {constraint.name for constraint in CONSTRAINTS}
    Reservation._meta.constraints = [
        constraint
        for constraint in original_constraints
        if constraint.name not in removed_names
        and not isinstance(constraint, ExclusionConstraint)
    ]
    try:
        schema_editor._remake_table(Reservation)
    finally:
        Reservation._meta.constraints = original_constraints


class Migration(migrations.Migration):
    dependencies = [("planning", "0018_pathoperationalapproval")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_provenance_constraints,
                    remove_provenance_constraints,
                )
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="physicalmoldreservation",
                    constraint=PROVENANCE_CONSTRAINT,
                ),
                migrations.AddConstraint(
                    model_name="physicalmoldreservation",
                    constraint=ACTIVE_EXECUTION_CONSTRAINT,
                ),
            ],
        )
    ]
