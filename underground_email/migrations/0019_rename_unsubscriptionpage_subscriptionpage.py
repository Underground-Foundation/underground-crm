"""
Rename UnsubscriptionPage to SubscriptionPage, now that people can subscribe on
it as well as unsubscribe, and move the pages that migration 0018 created from
"Unsubscribe" (/unsubscribe/) to "Subscriptions" (/subscriptions/) in the page
tree. A page whose title or slug an editor has changed keeps what they gave it.

Django renames the page type's ContentType itself, straight after RenameModel.
"""

from django.db import migrations

OLD_TITLE = "Unsubscribe"
NEW_TITLE = "Subscriptions"
OLD_SLUG = "unsubscribe"
NEW_SLUG = "subscriptions"
# Treebeard's MP_Node adds this many characters to the path for each level.
PATH_STEP_LENGTH = 4


def _rename_pages(apps, old_title: str, new_title: str, old_slug: str, new_slug: str) -> None:
    Page = apps.get_model("wagtailcore", "Page")
    SubscriptionPage = apps.get_model("underground_email", "SubscriptionPage")
    for page in SubscriptionPage.objects.all():
        if page.title == old_title:
            page.title = new_title
            page.draft_title = new_title
            if page.seo_title == old_title:
                page.seo_title = new_title
        if page.slug == old_slug:
            parent = Page.objects.get(path=page.path[:-PATH_STEP_LENGTH])
            sibling_has_slug = (
                Page.objects.filter(depth=page.depth, path__startswith=parent.path, slug=new_slug)
                .exclude(pk=page.pk)
                .exists()
            )
            if not sibling_has_slug:
                page.slug = new_slug
                page.url_path = f"{parent.url_path}{new_slug}/"
        page.save()


def rename_pages_forwards(apps, schema_editor) -> None:
    _rename_pages(apps, OLD_TITLE, NEW_TITLE, OLD_SLUG, NEW_SLUG)


def rename_pages_backwards(apps, schema_editor) -> None:
    _rename_pages(apps, NEW_TITLE, OLD_TITLE, NEW_SLUG, OLD_SLUG)


class Migration(migrations.Migration):

    dependencies = [
        ("underground_email", "0018_create_unsubscription_pages"),
    ]

    operations = [
        migrations.RenameModel(old_name="UnsubscriptionPage", new_name="SubscriptionPage"),
        migrations.AlterModelOptions(
            name="subscriptionpage",
            options={"verbose_name": "Subscription Page"},
        ),
        migrations.RunPython(rename_pages_forwards, rename_pages_backwards),
    ]
