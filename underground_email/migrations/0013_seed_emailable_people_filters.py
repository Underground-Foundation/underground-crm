from django.db import migrations

# Copied from underground_crm/management/commands/import_people_csv.py rather than
# imported, so this migration keeps working however that command changes. The CSV
# import gives this suffix to the synthetic address of anybody who had no email
# address, and nothing sent there can be delivered.
_PLACEHOLDER_EMAIL_SUFFIX = "@import.invalid"

EMAILABLE_ADDRESSES_FILTER_NAME = "Emailable addresses"
EMAILABLE_LIVING_PEOPLE_FILTER_NAME = "Emailable living people"

# A "NONE" group matches the people who match none of its rules, which is how a
# filter says "does not contain".
EMAILABLE_ADDRESSES_CRITERIA = {
    "logic": "AND",
    "rules": [
        {"field": "unsubscribed_at", "operator": "isnull"},
        {"field": "is_active", "operator": "true"},
        {"field": "email", "operator": "not_isnull"},
        {
            "logic": "NONE",
            "rules": [
                {
                    "field": "email",
                    "operator": "icontains",
                    "value": _PLACEHOLDER_EMAIL_SUFFIX,
                },
            ],
        },
    ],
}


def emailable_living_people_criteria(emailable_addresses_filter_id: str) -> dict:
    return {
        "logic": "AND",
        "rules": [
            {
                "field": "__filter__",
                "operator": "matches",
                "value": emailable_addresses_filter_id,
            },
            {"field": "is_deceased", "operator": "false"},
        ],
    }


def create_emailable_filters(apps, schema_editor) -> None:
    PeopleFilter = apps.get_model("underground_crm", "PeopleFilter")
    emailable_addresses, _created = PeopleFilter.objects.get_or_create(
        name=EMAILABLE_ADDRESSES_FILTER_NAME,
        defaults={
            "description": (
                "People with a real email address who have not unsubscribed and "
                "whose account is active."
            ),
            "criteria": EMAILABLE_ADDRESSES_CRITERIA,
        },
    )
    PeopleFilter.objects.get_or_create(
        name=EMAILABLE_LIVING_PEOPLE_FILTER_NAME,
        defaults={
            "description": "People matching “Emailable addresses” who are not deceased.",
            "criteria": emailable_living_people_criteria(str(emailable_addresses.pk)),
        },
    )


def delete_emailable_filters(apps, schema_editor) -> None:
    PeopleFilter = apps.get_model("underground_crm", "PeopleFilter")
    PeopleFilter.objects.filter(
        name__in=[EMAILABLE_ADDRESSES_FILTER_NAME, EMAILABLE_LIVING_PEOPLE_FILTER_NAME]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("underground_email", "0012_emailcampaign_site_alter_emailcampaign_body"),
        ("underground_crm", "0027_alter_peoplefilter_criteria_help_text"),
    ]

    operations = [
        migrations.RunPython(create_emailable_filters, delete_emailable_filters),
    ]
