import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0013_urlredirect"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonFilter",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=200, unique=True)),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="Optional human-readable description of who this filter selects.",
                    ),
                ),
                (
                    "criteria",
                    models.JSONField(
                        help_text='Django ORM filter kwargs as a JSON object, e.g. {"primary_address__postcode": "3056", "gender": "M"}'
                    ),
                ),
            ],
            options={
                "verbose_name": "Person filter",
                "verbose_name_plural": "Person filters",
                "ordering": ["name"],
            },
        ),
    ]
