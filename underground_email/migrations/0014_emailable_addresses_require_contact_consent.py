from django.db import migrations

EMAILABLE_ADDRESSES_FILTER_NAME = "Emailable addresses"

EMAIL_OPT_IN_RULE = {"field": "email_opt_in", "operator": "true"}
NOT_DO_NOT_CONTACT_RULE = {"field": "do_not_contact", "operator": "false"}
CONTACT_CONSENT_RULES = [EMAIL_OPT_IN_RULE, NOT_DO_NOT_CONTACT_RULE]

PREVIOUS_DESCRIPTION = (
    "People with a real email address who have not unsubscribed and whose account is active."
)
DESCRIPTION = (
    "People with a real email address who have opted into email, have not unsubscribed, "
    "are not marked as “do not contact” and whose account is active."
)

LOGIC_ALL = "AND"


def require_contact_consent(apps, schema_editor) -> None:
    """
    Add the rules to the filter as it stands, because administrators can edit a seeded
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
        missing_rules = [rule for rule in CONTACT_CONSENT_RULES if rule not in rules]
        criteria = {**criteria, "rules": [*rules, *missing_rules]}
    else:
        # In an "OR" or "NONE" group the new rules would widen the filter or invert its
        # meaning, so the edited group is nested beneath a new "AND" group.
        criteria = {"logic": LOGIC_ALL, "rules": [criteria, *CONTACT_CONSENT_RULES]}
    emailable_addresses.criteria = criteria

    if emailable_addresses.description == PREVIOUS_DESCRIPTION:
        emailable_addresses.description = DESCRIPTION
    emailable_addresses.save()


def stop_requiring_contact_consent(apps, schema_editor) -> None:
    PeopleFilter = apps.get_model("underground_crm", "PeopleFilter")
    emailable_addresses = PeopleFilter.objects.filter(name=EMAILABLE_ADDRESSES_FILTER_NAME).first()
    if emailable_addresses is None:
        return

    criteria = emailable_addresses.criteria
    emailable_addresses.criteria = {
        **criteria,
        "rules": [rule for rule in criteria.get("rules", []) if rule not in CONTACT_CONSENT_RULES],
    }

    if emailable_addresses.description == DESCRIPTION:
        emailable_addresses.description = PREVIOUS_DESCRIPTION
    emailable_addresses.save()


class Migration(migrations.Migration):

    dependencies = [
        ("underground_email", "0013_seed_emailable_people_filters"),
    ]

    operations = [
        migrations.RunPython(require_contact_consent, stop_requiring_contact_consent),
    ]
