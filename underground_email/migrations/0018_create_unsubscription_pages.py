"""
Give every site an UnsubscriptionPage, beneath its root page.

Every installation needs one, because the link at the foot of each campaign
email leads to it (at /unsubscribe). We have such a page, rather than
unsubscribing people as soon as they follow that link, to get around
over-eager email clients and security scanners, which follow the links in a
message before its recipient has read it.

Migrations see historical models, which lack treebeard's tree-editing methods,
so the new page's materialized path is worked out here the way treebeard's
MP_Node does it: each level of the tree adds four base-36 digits to the path.
"""

from django.db import migrations
from django.utils import timezone

# The constants of treebeard's MP_Node, as Wagtail's Page uses them.
PATH_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PATH_STEP_LENGTH = 4

PAGE_TITLE = "Unsubscribe"
PAGE_SLUG = "unsubscribe"


def _encode_path_step(number: int) -> str:
    digits = ""
    while number:
        number, remainder = divmod(number, len(PATH_ALPHABET))
        digits = PATH_ALPHABET[remainder] + digits
    return digits.rjust(PATH_STEP_LENGTH, PATH_ALPHABET[0])


def _decode_path_step(step: str) -> int:
    return int(step, len(PATH_ALPHABET))


def _next_child_path(Page, parent) -> str:
    """The path of a new last child of `parent`, as MP_Node.add_child would choose it."""
    last_child = (
        Page.objects.filter(depth=parent.depth + 1, path__startswith=parent.path)
        .order_by("-path")
        .first()
    )
    if last_child is None:
        position = 1
    else:
        position = _decode_path_step(last_child.path[-PATH_STEP_LENGTH:]) + 1
    return parent.path + _encode_path_step(position)


def _free_slug(Page, parent) -> str:
    """PAGE_SLUG, or PAGE_SLUG with a number after it if a sibling has it already."""
    sibling_slugs = set(
        Page.objects.filter(depth=parent.depth + 1, path__startswith=parent.path).values_list(
            "slug", flat=True
        )
    )
    slug = PAGE_SLUG
    suffix = 2
    while slug in sibling_slugs:
        slug = f"{PAGE_SLUG}-{suffix}"
        suffix += 1
    return slug


def create_unsubscription_pages(apps, schema_editor) -> None:
    ContentType = apps.get_model("contenttypes", "ContentType")
    Page = apps.get_model("wagtailcore", "Page")
    Site = apps.get_model("wagtailcore", "Site")
    UnsubscriptionPage = apps.get_model("underground_email", "UnsubscriptionPage")

    content_type, _created = ContentType.objects.get_or_create(
        app_label="underground_email", model="unsubscriptionpage"
    )
    now = timezone.now()
    for site in Site.objects.select_related("root_page"):
        root = site.root_page
        already_has_one = Page.objects.filter(
            path__startswith=root.path, content_type=content_type
        ).exists()
        if already_has_one:
            continue
        slug = _free_slug(Page, root)
        UnsubscriptionPage.objects.create(
            title=PAGE_TITLE,
            draft_title=PAGE_TITLE,
            seo_title=PAGE_TITLE,
            slug=slug,
            content_type=content_type,
            path=_next_child_path(Page, root),
            depth=root.depth + 1,
            numchild=0,
            url_path=f"{root.url_path}{slug}/",
            locale_id=root.locale_id,
            live=True,
            has_unpublished_changes=False,
            first_published_at=now,
            last_published_at=now,
            body=[],
        )
        Page.objects.filter(pk=root.pk).update(numchild=root.numchild + 1)


def delete_unsubscription_pages(apps, schema_editor) -> None:
    Page = apps.get_model("wagtailcore", "Page")
    UnsubscriptionPage = apps.get_model("underground_email", "UnsubscriptionPage")
    for page in UnsubscriptionPage.objects.all():
        parent_path = page.path[:-PATH_STEP_LENGTH]
        # Deleting the page deletes the rows of its FormPage and Page ancestors too.
        page.delete()
        parent = Page.objects.get(path=parent_path)
        Page.objects.filter(pk=parent.pk).update(numchild=parent.numchild - 1)


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        # The default site and its root page are created by 0002; pages have
        # had a required locale since 0057.
        ("wagtailcore", "0057_page_locale_fields_notnull"),
        ("underground_email", "0017_unsubscription_page"),
    ]

    operations = [
        migrations.RunPython(create_unsubscription_pages, delete_unsubscription_pages),
    ]
