from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0004_drop_primary_address"),
    ]

    operations = [
        migrations.AddField(
            model_name="persontag",
            name="was_authenticated",
            field=models.BooleanField(
                default=True,
                help_text="Whether this tag was applied as a result of an authenticated action, rather than an anonymous form submission.",
                verbose_name="Was authenticated",
            ),
        ),
    ]
