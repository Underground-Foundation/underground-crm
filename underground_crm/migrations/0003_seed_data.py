from django.db import migrations
from django.utils.text import slugify

VOLUNTEER_TAG_NAME = "Volunteer"

IS_VOLUNTEER_FILTER_NAME = "Is Volunteer"

# Matches Person records tagged "Volunteer" — see create_volunteer_tag below,
# which seeds that Tag.
IS_VOLUNTEER_CRITERIA = {
    "logic": "AND",
    "rules": [{"field": "tags__name", "operator": "exact", "value": VOLUNTEER_TAG_NAME}],
}


def create_volunteer_tag(apps, schema_editor):
    Tag = apps.get_model("underground_crm", "Tag")
    Tag.objects.get_or_create(
        name=VOLUNTEER_TAG_NAME,
        defaults={
            "slug": slugify(VOLUNTEER_TAG_NAME, allow_unicode=True),
            "is_protected": True,
        },
    )


def delete_volunteer_tag(apps, schema_editor):
    Tag = apps.get_model("underground_crm", "Tag")
    Tag.objects.filter(name=VOLUNTEER_TAG_NAME).delete()


def create_is_volunteer_filter(apps, schema_editor):
    PeopleFilter = apps.get_model("underground_crm", "PeopleFilter")
    PeopleFilter.objects.get_or_create(
        name=IS_VOLUNTEER_FILTER_NAME,
        defaults={
            "description": 'People tagged "Volunteer".',
            "criteria": IS_VOLUNTEER_CRITERIA,
        },
    )


def delete_is_volunteer_filter(apps, schema_editor):
    PeopleFilter = apps.get_model("underground_crm", "PeopleFilter")
    PeopleFilter.objects.filter(name=IS_VOLUNTEER_FILTER_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("underground_crm", "0002_pages"),
    ]

    operations = [
        # There is no is_volunteer flag on Person — volunteering is expressed by
        # tagging a Person with this Tag instead. Every Underground CRM install
        # gets it, seeded protected so it can only be deleted by an admin
        # through the console (see Tag.is_protected and the pre_delete signal
        # in signals.py).
        migrations.RunPython(create_volunteer_tag, delete_volunteer_tag),
        migrations.RunPython(create_is_volunteer_filter, delete_is_volunteer_filter),
    ]
