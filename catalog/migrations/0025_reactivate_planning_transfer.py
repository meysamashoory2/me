from django.db import migrations


PLANNING_TRANSFER_PREFIXES = (
    "transfer.dest.inventory_orders",
    "transfer.level.inventory_orders.",
    "transfer.field.inventory_orders.",
)


def reactivate_planning_transfer(apps, schema_editor):
    SystemNamingKey = apps.get_model("catalog", "SystemNamingKey")
    for prefix in PLANNING_TRANSFER_PREFIXES:
        SystemNamingKey.objects.filter(key__startswith=prefix).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0024_table_layout_section_layouts"),
    ]

    operations = [
        migrations.RunPython(reactivate_planning_transfer, migrations.RunPython.noop),
    ]
