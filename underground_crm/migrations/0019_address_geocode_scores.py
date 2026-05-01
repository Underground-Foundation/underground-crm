from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("underground_crm", "0018_address_geocode"),
    ]

    operations = [
        migrations.AddField(
            model_name="address",
            name="geocode_reliability",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="address",
            name="geocode_confidence",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
