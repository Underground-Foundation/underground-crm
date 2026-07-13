# Written by hand: makemigrations cannot detect this as a rename (the
# FeedPage model also gains new fields in the follow-up migration, and
# non-interactive runs would emit a destructive DeleteModel + CreateModel
# pair instead).

from django.db import migrations


def rename_blog_content_type(apps, schema_editor):
    """Point existing Blog pages at the renamed model.

    RenameModel does not update django.contrib.contenttypes, and Wagtail
    resolves each page's specific class through its content_type row — left
    alone, every existing Blog page would break. On a freshly created
    database no content type rows exist yet (they are created after all
    migrations run), so this is a no-op there.
    """
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="underground_crm", model="blog").update(model="feedpage")


def restore_blog_content_type(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="underground_crm", model="feedpage").update(model="blog")


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("underground_crm", "0006_event_index_page"),
    ]

    operations = [
        migrations.RenameModel(old_name="Blog", new_name="FeedPage"),
        migrations.RunPython(rename_blog_content_type, restore_blog_content_type),
    ]
