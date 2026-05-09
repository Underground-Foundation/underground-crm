# Generated manually 2026-05-04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("underground_email", "0003_alter_emailcampaign_state_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailcampaign",
            name="smtp2go_template_id",
            field=models.CharField(blank=True, max_length=50, verbose_name="smtp2go template ID"),
        ),
    ]
