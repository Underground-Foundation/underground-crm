from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("underground_crm", "0017_remove_urlredirection"),
    ]

    operations = [
        migrations.AddField(
            model_name="address",
            name="latitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name="address",
            name="longitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
    ]
