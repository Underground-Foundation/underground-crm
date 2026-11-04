from django.db import migrations

EMAILABLE_ADDRESSES_FILTER_NAME = "Emailable addresses"

# Person.email_is_bad is NULL for anybody whose address has never been found to be bad,
# and a rule that the field "is false" would not match NULL. A "NONE" group excludes only
# the people who are positively marked as having a bad address.
EXCLUDE_BAD_ADDRESSES_GROUP = {
    "logic": "NONE",
    "rules": [{"field": "email_is_bad", "operator": "true"}],
}

PREVIOUS_DESCRIPTION = (
    "People with a real email address who have opted into email, have not unsubscribed, "
    "are not marked as “do not contact” and whose account is active."
)
DESCRIPTION = (
    "People with a real, working email address who have opted into email, have not "
    "unsubscribed, are not marked as “do not contact” and whose account is active."
)

LOGIC_ALL = "AND"


def exclude_bad_addresses(apps, schema_editor) -> None:
    """
    Add the group to the filter as it stands, because administrators can edit a seeded
    filter and their changes must survive. A filter that an administrator has deleted
    or renamed is no longer the seeded one, so it is left alone.
    """
    PeopleFilter = apps.get_model("underground_crm", "PeopleFilter")
    emailable_addresses = PeopleFilter.objects.filter(name=EMAILABLE_ADDRESSES_FILTER_NAME).first()
    if emailable_addresses is None:
        return

    criteria = emailable_addresses.criteria
    if criteria.get("logic", LOGIC_ALL) == LOGIC_ALL:
        rules = criteria.get("rules", [])
        if EXCLUDE_BAD_ADDRESSES_GROUP not in rules:
            criteria = {**criteria, "rules": [*rules, EXCLUDE_BAD_ADDRESSES_GROUP]}
    else:
        # In an "OR" or "NONE" group the new group would widen the filter or invert its
        # meaning, so the edited group is nested beneath a new "AND" group.
        criteria = {"logic": LOGIC_ALL, "rules": [criteria, EXCLUDE_BAD_ADDRESSES_GROUP]}
    emailable_addresses.criteria = criteria

    if emailable_addresses.description == PREVIOUS_DESCRIPTION:
        emailable_addresses.description = DESCRIPTION
    emailable_addresses.save()


def stop_excluding_bad_addresses(apps, schema_editor) -> None:
    PeopleFilter = apps.get_model("underground_crm", "PeopleFilter")
    emailable_addresses = PeopleFilter.objects.filter(name=EMAILABLE_ADDRESSES_FILTER_NAME).first()
    if emailable_addresses is None:
        return

    criteria = emailable_addresses.criteria
    emailable_addresses.criteria = {
        **criteria,
        "rules": [
            rule for rule in criteria.get("rules", []) if rule != EXCLUDE_BAD_ADDRESSES_GROUP
        ],
    }

    if emailable_addresses.description == DESCRIPTION:
        emailable_addresses.description = PREVIOUS_DESCRIPTION
    emailable_addresses.save()


class Migration(migrations.Migration):

    dependencies = [
        ("underground_email", "0014_emailable_addresses_require_contact_consent"),
    ]

    operations = [
        migrations.RunPython(exclude_bad_addresses, stop_excluding_bad_addresses),
    ]
