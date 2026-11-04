"""
Tests for the "Emailable addresses" and "Emailable living supporters" filters,
which a migration seeds on every installation.
"""

import datetime
import importlib

import django.apps
import django.test
from django.contrib.auth import get_user_model
from django.utils import timezone

from underground_crm.management.commands import import_people_csv
from underground_crm.models import PeopleFilter

seed_migration = importlib.import_module(
    "underground_email.migrations.0013_seed_emailable_people_filters"
)
consent_migration = importlib.import_module(
    "underground_email.migrations.0014_emailable_addresses_require_contact_consent"
)
bad_address_migration = importlib.import_module(
    "underground_email.migrations.0015_emailable_addresses_exclude_bad_addresses"
)

Person = get_user_model()

LEGACY_ID_WITHOUT_EMAIL = 48213


class EmailablePeopleFiltersTest(django.test.TestCase):
    def setUp(self):
        self.emailable_addresses = PeopleFilter.objects.get(
            name=seed_migration.EMAILABLE_ADDRESSES_FILTER_NAME
        )
        self.emailable_living_supporters = PeopleFilter.objects.get(
            name=seed_migration.EMAILABLE_LIVING_SUPPORTERS_FILTER_NAME
        )

        # Everybody here has opted in unless that is what the person is here to test, so
        # that each person is excluded for the one reason that their name describes.
        self.supporter = Person.objects.create_user(
            email="charlie.supporter@example.com",
            password="password",
            is_supporter=True,
            email_opt_in=True,
        )
        self.non_supporter = Person.objects.create_user(
            email="robin.bystander@example.com",
            password="password",
            is_supporter=False,
            email_opt_in=True,
        )
        self.unsubscriber = Person.objects.create_user(
            email="jamie.unsubscribed@example.com",
            password="password",
            unsubscribed_at=timezone.now() - datetime.timedelta(days=30),
            email_opt_in=True,
        )
        self.inactive_person = Person.objects.create_user(
            email="casey.tombstoned@example.com",
            password="password",
            is_active=False,
            email_opt_in=True,
        )
        self.person_without_email = Person.objects.create_user(
            email=import_people_csv._placeholder_email(LEGACY_ID_WITHOUT_EMAIL),
            password="password",
            email_opt_in=True,
        )
        self.deceased_supporter = Person.objects.create_user(
            email="frankie.late@example.com",
            password="password",
            is_deceased=True,
            is_supporter=True,
            email_opt_in=True,
        )
        self.supporter_without_opt_in = Person.objects.create_user(
            email="alex.undecided@example.com",
            password="password",
            is_supporter=True,
            email_opt_in=False,
        )
        self.supporter_marked_do_not_contact = Person.objects.create_user(
            email="morgan.leavemealone@example.com",
            password="password",
            is_supporter=True,
            email_opt_in=True,
            do_not_contact=True,
        )
        self.supporter_with_bad_address = Person.objects.create_user(
            email="taylor.bounced@example.com",
            password="password",
            is_supporter=True,
            email_opt_in=True,
            email_is_bad=True,
        )

    def _matched_emails(self, people_filter: PeopleFilter) -> set[str]:
        return set(people_filter.apply(Person.objects.all()).values_list("email", flat=True))

    def test_emailable_addresses(self):
        self.assertEqual(
            self._matched_emails(self.emailable_addresses),
            {self.supporter.email, self.non_supporter.email, self.deceased_supporter.email},
            "Only these people are active, opted in, not marked as do-not-contact, still "
            "subscribed and have a real address that is not known to be bad. Being deceased "
            "or not being a supporter is left to the other filter.",
        )

    def test_address_confirmed_as_working_is_emailable(self):
        self.assertIsNone(
            self.supporter.email_is_bad,
            "A person's address has not been assessed until something says otherwise.",
        )
        self.supporter_with_bad_address.email_is_bad = False
        self.supporter_with_bad_address.save()

        self.assertEqual(
            self._matched_emails(self.emailable_living_supporters),
            {self.supporter.email, self.supporter_with_bad_address.email},
            "Both an address that was never assessed and one that was found to work can be "
            "emailed; only an address that is positively marked as bad is excluded.",
        )

    def test_bad_address_exclusion_keeps_an_administrators_edits(self):
        tag_rule = {"field": "tags__name", "operator": "exact", "value": "Volunteer"}
        self.emailable_addresses.criteria = {"logic": "AND", "rules": [tag_rule]}
        self.emailable_addresses.save()

        bad_address_migration.exclude_bad_addresses(django.apps.apps, None)

        self.emailable_addresses.refresh_from_db()
        self.assertEqual(
            self.emailable_addresses.criteria["rules"],
            [tag_rule, bad_address_migration.EXCLUDE_BAD_ADDRESSES_GROUP],
        )

    def test_bad_address_exclusion_still_narrows_a_filter_edited_to_match_any_rule(self):
        self.emailable_addresses.criteria = {
            "logic": "OR",
            "rules": [
                {"field": "email", "operator": "exact", "value": person.email}
                for person in (self.supporter, self.supporter_with_bad_address)
            ],
        }
        self.emailable_addresses.save()

        bad_address_migration.exclude_bad_addresses(django.apps.apps, None)

        self.emailable_addresses.refresh_from_db()
        self.assertEqual(
            self._matched_emails(self.emailable_addresses),
            {self.supporter.email},
            "Both people match the administrator's rules, but only this one's address is "
            "not known to be bad.",
        )

    def test_reversing_removes_only_the_bad_address_exclusion(self):
        group = bad_address_migration.EXCLUDE_BAD_ADDRESSES_GROUP
        criteria_with_group = self.emailable_addresses.criteria
        self.assertIn(
            group,
            criteria_with_group["rules"],
            "The migrations have already run on the test database, so the group is present.",
        )
        expected_rules = [rule for rule in criteria_with_group["rules"] if rule != group]

        bad_address_migration.stop_excluding_bad_addresses(django.apps.apps, None)

        self.emailable_addresses.refresh_from_db()
        self.assertEqual(self.emailable_addresses.criteria["rules"], expected_rules)
        self.assertEqual(
            self.emailable_addresses.description, bad_address_migration.PREVIOUS_DESCRIPTION
        )

    def test_consent_rules_keep_an_administrators_edits(self):
        tag_rule = {"field": "tags__name", "operator": "exact", "value": "Volunteer"}
        self.emailable_addresses.criteria = {"logic": "AND", "rules": [tag_rule]}
        self.emailable_addresses.save()

        consent_migration.require_contact_consent(django.apps.apps, None)

        self.emailable_addresses.refresh_from_db()
        self.assertEqual(
            self.emailable_addresses.criteria["rules"],
            [tag_rule, *consent_migration.CONTACT_CONSENT_RULES],
        )

    def test_consent_rules_still_narrow_a_filter_edited_to_match_any_rule(self):
        self.emailable_addresses.criteria = {
            "logic": "OR",
            "rules": [
                {"field": "email", "operator": "exact", "value": person.email}
                for person in (
                    self.supporter,
                    self.supporter_without_opt_in,
                    self.supporter_marked_do_not_contact,
                )
            ],
        }
        self.emailable_addresses.save()

        consent_migration.require_contact_consent(django.apps.apps, None)

        self.emailable_addresses.refresh_from_db()
        self.assertEqual(
            self._matched_emails(self.emailable_addresses),
            {self.supporter.email},
            "All three people match the administrator's rules, but only this one has opted "
            "in without being marked as do-not-contact.",
        )

    def test_reversing_removes_only_the_consent_rules(self):
        # Django reverses migrations newest first, so the later migration has been undone
        # by the time this one is, which restores the description that this one expects.
        bad_address_migration.stop_excluding_bad_addresses(django.apps.apps, None)
        self.emailable_addresses.refresh_from_db()

        criteria_with_rules = self.emailable_addresses.criteria
        expected_rules = [
            rule
            for rule in criteria_with_rules["rules"]
            if rule not in consent_migration.CONTACT_CONSENT_RULES
        ]
        for rule in consent_migration.CONTACT_CONSENT_RULES:
            self.assertIn(
                rule,
                criteria_with_rules["rules"],
                "The migrations have already run on the test database, so the rule is present.",
            )

        consent_migration.stop_requiring_contact_consent(django.apps.apps, None)

        self.emailable_addresses.refresh_from_db()
        self.assertEqual(self.emailable_addresses.criteria["rules"], expected_rules)
        self.assertEqual(
            self.emailable_addresses.description, consent_migration.PREVIOUS_DESCRIPTION
        )

    def test_emailable_living_supporters(self):
        self.assertEqual(
            self._matched_emails(self.emailable_living_supporters),
            {self.supporter.email},
            "Of the people with an emailable address, only this one is both alive and a "
            "supporter.",
        )

    def test_blank_email_is_not_emailable(self):
        # Email is required, so a blank one can only be saved by bypassing validation,
        # as an import might.
        blank_email_person = Person.objects.create(email="", first_name="Kai", email_opt_in=True)

        self.assertNotIn(
            blank_email_person.pk,
            set(self.emailable_addresses.apply(Person.objects.all()).values_list("pk", flat=True)),
        )
