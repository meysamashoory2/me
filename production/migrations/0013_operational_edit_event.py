from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("production", "0012_path_day_entries")]

    operations = [
        migrations.AlterField(
            model_name="productionexecutionevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("activated", "فعال‌سازی"),
                    ("first_production", "اولین تولید"),
                    ("production_accepted", "ثبت تولید پذیرفته‌شده"),
                    ("temporary_stop", "توقف موقت"),
                    ("resume", "ادامه تولید"),
                    ("full_stop", "توقف کامل"),
                    ("manager_reopen", "بازگشایی مدیر"),
                    ("statistics_completed", "تکمیل آمار"),
                    ("statistics_reopened", "بازگشایی آمار"),
                    ("superseded", "جایگزینی"),
                    ("cancelled", "لغو"),
                    ("operational_edit", "ویرایش عملیاتی"),
                ],
                max_length=24,
            ),
        ),
    ]
