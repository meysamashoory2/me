from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import migrations, models

from planning.postgres_constraints import (
    ACTIVE_MOLD_RESERVATION_CONSTRAINT,
    InclusiveDateRange,
)


def _constraint():
    return ExclusionConstraint(
        name=ACTIVE_MOLD_RESERVATION_CONSTRAINT,
        expressions=[
            ("physical_mold", RangeOperators.EQUAL),
            (
                InclusiveDateRange("reserved_start_date", "reserved_end_date"),
                RangeOperators.OVERLAPS,
            ),
        ],
        condition=models.Q(status="active"),
    )


def add_postgresql_constraint(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    alias = schema_editor.connection.alias
    Reservation = apps.get_model("planning", "PhysicalMoldReservation")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = %s", ["btree_gist"])
        if cursor.fetchone() is None:
            raise RuntimeError(
                "btree_gist must be installed by a PostgreSQL administrator "
                "before applying planning.0017."
            )
    active = Reservation.objects.using(alias).filter(status="active").order_by(
        "physical_mold_id", "pk"
    )
    conflicts = []
    for left in active.iterator():
        right_ids = list(
            active.filter(
                physical_mold_id=left.physical_mold_id,
                pk__gt=left.pk,
                reserved_start_date__lte=left.reserved_end_date,
                reserved_end_date__gte=left.reserved_start_date,
            ).values_list("pk", flat=True)
        )
        conflicts.extend((left.pk, right_id) for right_id in right_ids)
    if conflicts:
        pairs = ", ".join(f"{left}/{right}" for left, right in conflicts[:20])
        raise RuntimeError(
            "Overlapping ACTIVE physical-mold reservations block planning.0017. "
            f"Conflicting reservation IDs: {pairs}"
        )
    schema_editor.add_constraint(Reservation, _constraint())


def remove_postgresql_constraint(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    Reservation = apps.get_model("planning", "PhysicalMoldReservation")
    schema_editor.remove_constraint(Reservation, _constraint())


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0016_physicalmoldreservation_execution_path"),
        ("production", "0012_path_day_entries"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_postgresql_constraint,
                    remove_postgresql_constraint,
                ),
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="physicalmoldreservation",
                    constraint=_constraint(),
                ),
            ],
        ),
    ]
