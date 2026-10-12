from django.db import migrations, models


def copy_feed_flag_to_basic_page(apps, schema_editor) -> None:
    FeedPage = apps.get_model("underground_crm", "FeedPage")
    BasicPage = apps.get_model("underground_crm", "BasicPage")
    for pk, value in FeedPage.objects.values_list("pk", "legacy_show_slug_as_breadcrumb"):
        BasicPage.objects.filter(pk=pk).update(show_slug_as_breadcrumb=value)


def copy_basic_page_flag_to_feed(apps, schema_editor) -> None:
    FeedPage = apps.get_model("underground_crm", "FeedPage")
    BasicPage = apps.get_model("underground_crm", "BasicPage")
    for pk, value in BasicPage.objects.filter(pk__in=FeedPage.objects.values("pk")).values_list(
        "pk", "show_slug_as_breadcrumb"
    ):
        FeedPage.objects.filter(pk=pk).update(legacy_show_slug_as_breadcrumb=value)


def show_slug_of_site_roots(apps, schema_editor) -> None:
    """A site root's slug is never in a URL, so its children can't be
    addressed beneath its parent (the tree root, outside the site); see
    BasicPage.save(). Pages already saved haven't been through that rule."""
    BasicPage = apps.get_model("underground_crm", "BasicPage")
    Site = apps.get_model("wagtailcore", "Site")
    BasicPage.objects.filter(pk__in=Site.objects.values("root_page_id")).update(
        show_slug_as_breadcrumb=True
    )


class Migration(migrations.Migration):
    """
    Moves show_slug_as_breadcrumb from FeedPage up to BasicPage, which FeedPage
    inherits from. The two columns can't coexist under one name, so the
    FeedPage one is renamed out of the way while its values are copied.
    """

    dependencies = [
        ("underground_crm", "0022_alter_eventpage_end_time_alter_eventpage_start_time"),
        ("wagtailcore", "0094_alter_page_locale"),
    ]

    operations = [
        migrations.RenameField(
            model_name="feedpage",
            old_name="show_slug_as_breadcrumb",
            new_name="legacy_show_slug_as_breadcrumb",
        ),
        migrations.AddField(
            model_name="basicpage",
            name="show_slug_as_breadcrumb",
            field=models.BooleanField(
                default=False,
                help_text="If disabled (the default), the pages beneath this one are addressed directly beneath this page's own parent, so their URLs omit this page's slug — the way the legacy CMS lays out its pages. If enabled, their URLs are nested beneath this page's own URL as usual.",
                verbose_name="Show slug as breadcrumb",
            ),
        ),
        migrations.RunPython(copy_feed_flag_to_basic_page, copy_basic_page_flag_to_feed),
        migrations.RemoveField(
            model_name="feedpage",
            name="legacy_show_slug_as_breadcrumb",
        ),
        migrations.RunPython(show_slug_of_site_roots, migrations.RunPython.noop),
    ]
