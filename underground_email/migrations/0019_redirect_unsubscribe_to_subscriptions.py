"""
Redirect /unsubscribe, the address of the view that preceded the subscription
page, to /subscriptions, so that the links in emails already sent still lead
somewhere. The redirect applies to
every site, and is permanent.

Wagtail's RedirectMiddleware only acts on requests that would otherwise be
answered with "Not Found", which /unsubscribe now is, since nothing else is
served there.
"""

from django.db import migrations

OLD_PATH = "/unsubscribe"
NEW_PATH = "/subscriptions"


def create_redirect(apps, schema_editor) -> None:
    Redirect = apps.get_model("wagtailredirects", "Redirect")
    Redirect.objects.get_or_create(
        old_path=OLD_PATH,
        site=None,
        defaults={"redirect_link": NEW_PATH, "is_permanent": True},
    )


def delete_redirect(apps, schema_editor) -> None:
    Redirect = apps.get_model("wagtailredirects", "Redirect")
    Redirect.objects.filter(old_path=OLD_PATH, site=None, redirect_link=NEW_PATH).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("wagtailredirects", "0006_redirect_increase_max_length"),
        ("underground_email", "0018_create_subscription_pages"),
    ]

    operations = [
        migrations.RunPython(create_redirect, delete_redirect),
    ]
