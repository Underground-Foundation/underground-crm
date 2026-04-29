import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0012_basicpage_legacy_id"),
        ("wagtailcore", "0094_alter_page_locale"),
    ]

    operations = [
        migrations.CreateModel(
            name="UrlRedirect",
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
                (
                    "old_path",
                    models.CharField(
                        help_text="The URL path to redirect from, starting with a slash (e.g. /old-page/).",
                        max_length=255,
                        unique=True,
                    ),
                ),
                (
                    "redirect_url",
                    models.CharField(
                        blank=True,
                        help_text="External URL to redirect to. Use this when redirecting outside the site.",
                        max_length=2048,
                    ),
                ),
                (
                    "is_permanent",
                    models.BooleanField(
                        default=True,
                        help_text="Send a 301 permanent redirect. Uncheck to send a 302 temporary redirect instead.",
                        verbose_name="Permanent redirect",
                    ),
                ),
                (
                    "redirect_page",
                    models.ForeignKey(
                        blank=True,
                        help_text="The on-site page to redirect visitors to.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="incoming_redirects",
                        to="wagtailcore.page",
                    ),
                ),
            ],
            options={
                "verbose_name": "Redirect",
                "verbose_name_plural": "Redirects",
            },
        ),
    ]
