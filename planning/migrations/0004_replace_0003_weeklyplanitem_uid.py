import planning.models
from django.db import migrations, models


MAX_UID_GENERATION_ATTEMPTS = 100
UID_MAX_LENGTH = 16


def generate_unique_uid(reserved_uids):
    for _ in range(MAX_UID_GENERATION_ATTEMPTS):
        uid = planning.models.generate_program_uid()
        if (
            uid
            and uid.strip()
            and len(uid) <= UID_MAX_LENGTH
            and uid not in reserved_uids
        ):
            return uid

    raise RuntimeError(
        "Unable to generate a unique WeeklyPlanItem UID after "
        f"{MAX_UID_GENERATION_ATTEMPTS} attempts."
    )


def collect_existing_uids(rows):
    existing_uids = set()
    missing_uid_pks = []

    for pk, uid in rows:
        if uid is None or uid == "":
            missing_uid_pks.append(pk)
            continue

        if not uid.strip() or len(uid) > UID_MAX_LENGTH:
            raise RuntimeError(
                f"WeeklyPlanItem {pk} has an invalid existing UID; migration aborted."
            )
        if uid in existing_uids:
            raise RuntimeError(
                f"Duplicate existing WeeklyPlanItem UID {uid!r}; migration aborted."
            )
        existing_uids.add(uid)

    return existing_uids, missing_uid_pks


def backfill_weekly_plan_item_uids(apps, schema_editor):
    WeeklyPlanItem = apps.get_model("planning", "WeeklyPlanItem")
    database = schema_editor.connection.alias

    rows = (
        WeeklyPlanItem.objects.using(database)
        .order_by("pk")
        .values_list("pk", "uid")
        .iterator()
    )
    existing_uids, missing_uid_pks = collect_existing_uids(rows)

    for pk in missing_uid_pks:
        uid = generate_unique_uid(existing_uids)
        WeeklyPlanItem.objects.using(database).filter(pk=pk).update(uid=uid)
        existing_uids.add(uid)


class Migration(migrations.Migration):
    replaces = [
        ("planning", "0003_weeklyplanitem_sequence_weeklyplanitem_uid"),
    ]

    dependencies = [
        (
            "planning",
            "0002_alter_weeklyplanitem_options_alter_weeklyplan_status_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="weeklyplanitem",
            name="sequence",
            field=models.PositiveSmallIntegerField(
                default=1, verbose_name="ترتیب روی دستگاه"
            ),
        ),
        migrations.AddField(
            model_name="weeklyplanitem",
            name="uid",
            field=models.CharField(
                blank=True,
                max_length=UID_MAX_LENGTH,
                null=True,
                verbose_name="شناسه برنامه",
            ),
        ),
        migrations.RunPython(
            backfill_weekly_plan_item_uids,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="weeklyplanitem",
            name="uid",
            field=models.CharField(
                db_index=True,
                default=planning.models.generate_program_uid,
                editable=False,
                max_length=UID_MAX_LENGTH,
                unique=True,
                verbose_name="شناسه برنامه",
            ),
        ),
    ]
