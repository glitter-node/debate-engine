from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        (
            "thinking",
            "0009_remove_contentreport_uniq_report_per_reporter_thesis_status_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="counter",
            name="parent_counter",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rebuttals",
                to="thinking.counter",
            ),
        ),
    ]
