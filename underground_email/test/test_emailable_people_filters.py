"""
Tests for the "Emailable addresses" and "Emailable living supporters" filters,
which a migration seeds on every installation.
"""

import datetime
import importlib

import django.test
from django.contrib.auth import get_user_model
from django.utils import timezone

from underground_crm.management.commands import import_people_csv
from underground_crm.models import PeopleFilter

seed_migration = importlib.import_module(
    "underground_email.migrations.0013_seed_emailable_people_filters"
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

        self.supporter = Person.objects.create_user(
            email="charlie.supporter@example.com", password="password", is_supporter=True
        )
        self.non_supporter = Person.objects.create_user(
            email="robin.bystander@example.com", password="password", is_supporter=False
        )
        self.unsubscriber = Person.objects.create_user(
            email="jamie.unsubscribed@example.com",
            password="password",
            unsubscribed_at=timezone.now() - datetime.timedelta(days=30),
        )
        self.inactive_person = Person.objects.create_user(
            email="casey.tombstoned@example.com", password="password", is_active=False
        )
        self.person_without_email = Person.objects.create_user(
            email=import_people_csv._placeholder_email(LEGACY_ID_WITHOUT_EMAIL),
            password="password",
        )
        self.deceased_supporter = Person.objects.create_user(
            email="frankie.late@example.com",
            password="password",
            is_deceased=True,
            is_supporter=True,
        )

    def _matched_emails(self, people_filter: PeopleFilter) -> set[str]:
        return set(people_filter.apply(Person.objects.all()).values_list("email", flat=True))

    def test_emailable_addresses(self):
        self.assertEqual(
            self._matched_emails(self.emailable_addresses),
            {self.supporter.email, self.non_supporter.email, self.deceased_supporter.email},
            "Only these people are active, still subscribed and have a real email "
            "address. Being deceased or not being a supporter is left to the other filter.",
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
        blank_email_person = Person.objects.create(email="", first_name="Kai")

        self.assertNotIn(
            blank_email_person.pk,
            set(self.emailable_addresses.apply(Person.objects.all()).values_list("pk", flat=True)),
        )
