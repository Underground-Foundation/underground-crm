import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0009_eventpage_population"),
    ]

    operations = [
        migrations.CreateModel(
            name="Blog",
            fields=[
                (
                    "basicpage_ptr",
                    models.OneToOneField(
                        auto_created=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to="underground_crm.basicpage",
                    ),
                ),
                (
                    "page_size",
                    models.PositiveIntegerField(
                        default=10,
                        help_text="Number of posts to display per page.",
                    ),
                ),
            ],
            options={
                "verbose_name": "Blog",
            },
            bases=("underground_crm.basicpage",),
        ),
    ]
