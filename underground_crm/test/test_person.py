import logging
import unittest
from datetime import datetime, timedelta
from string import whitespace, punctuation
from zoneinfo import ZoneInfo

import django.test
from django.utils import timezone

from underground_crm.models import Person

logger = logging.getLogger(__name__)


class PersonTest(unittest.TestCase):
    def test_admin_requires_staff(self):
        from django.core.exceptions import ValidationError
        from underground_crm.models import Person

        person = Person(email="admin.notstaff@example.com", is_admin=True, is_staff=False)
        thrown = False
        try:
            person.clean()
        except ValidationError as e:
            thrown = True
            words = str(e).lower().strip(whitespace + punctuation).split(" ")
            logger.info("A validation error was thrown (as expected): %s", words)
            self.assertTrue(
                ("admin" in words) or ("admins" in words),
                msg=f"It was expected that the problem here would be the inconsistent use of is_admin and is_staff, but actually this error seems to be due to something else: {e}",
            )
            self.assertTrue(
                ("staff" in words) or ("is_staff" in words),
                msg=f"It was expected that the problem here would be the inconsistent use of is_admin and is_staff, but actually this error seems to be due to something else: {e}",
            )
        self.assertTrue(
            thrown,
            msg="It was expected that the inconsistency of is_admin and is_staff would cause an exception to be thrown",
        )


class PersonLanguageTest(unittest.TestCase):
    def _person(self, language_preferences=None):
        from underground_crm.models import Person

        return Person(email="lang@example.com", language_preferences=language_preferences)

    def test_preferred_language_from_browser_string(self):
        self.assertEqual(
            self._person("en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7").preferred_language,
            "en-US",
        )

    def test_preferred_language_respects_q_values(self):
        # zh-TW is listed second but has an implicit q=1.0, so it beats en;q=0.9
        self.assertEqual(
            self._person("en;q=0.9,zh-TW").preferred_language,
            "zh-TW",
        )

    def test_preferred_language_single_tag(self):
        self.assertEqual(self._person("fr-FR").preferred_language, "fr-FR")

    def test_preferred_language_defaults_to_en_au_when_unset(self):
        self.assertEqual(self._person(None).preferred_language, "en-AU")

    def test_preferred_language_defaults_to_en_au_when_empty(self):
        self.assertEqual(self._person("").preferred_language, "en-AU")

    def test_language_count_from_browser_string(self):
        self.assertEqual(
            self._person("en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7").language_count(),
            4,
        )

    def test_language_count_single_language(self):
        self.assertEqual(self._person("ja").language_count(), 1)

    def test_language_count_when_unset(self):
        self.assertEqual(self._person(None).language_count(), 0)

    def test_language_count_when_empty(self):
        self.assertEqual(self._person("").language_count(), 0)


class PersonPartialEmailAddressTest(unittest.TestCase):
    # The number of local-part characters partial_email_address keeps before the
    # ellipsis, per the spec: the ellipsis stands in for the 6th letter before "@"
    # onwards, so exactly 5 letters are shown.
    VISIBLE_LOCAL_PART_LENGTH = 5

    def test_masks_local_part_beyond_the_visible_length(self):
        # A real-looking address whose local part is longer than the visible length,
        # so the masking behaviour is actually exercised.
        email = "owen9825@gmail.com"
        person = Person(email=email)

        self.assertEqual(
            person.partial_email_address,
            "owen9…@gmail.com",
            msg=f"The first {self.VISIBLE_LOCAL_PART_LENGTH} characters of the local part "
            f"('owen9825') should be kept, with an ellipsis standing in for the rest, so "
            f"'{email}' was expected to become 'owen9…@gmail.com'.",
        )

    def test_local_part_at_the_visible_length_is_shown_in_full(self):
        # Local part is exactly VISIBLE_LOCAL_PART_LENGTH characters long, so there is
        # nothing left to hide and no ellipsis should appear.
        email = "abcde@example.com"
        person = Person(email=email)

        self.assertEqual(
            person.partial_email_address,
            email,
            msg=f"A local part of exactly {self.VISIBLE_LOCAL_PART_LENGTH} characters "
            f"leaves nothing beyond the visible portion, so '{email}' should be returned "
            "unchanged rather than gaining a pointless ellipsis.",
        )

    def test_short_local_part_is_shown_in_full(self):
        # A local part shorter than the visible length, e.g. a real short given name.
        email = "amy@example.com"
        person = Person(email=email)

        self.assertEqual(
            person.partial_email_address,
            email,
            msg=f"'{email}' has a local part shorter than the "
            f"{self.VISIBLE_LOCAL_PART_LENGTH}-character visible portion, so it should be "
            "returned unchanged rather than being masked.",
        )


class PersonLocationTest(django.test.TestCase):
    def _person(self, email="person@example.com", **kwargs):
        from underground_crm.models import Person

        return Person.objects.create(email=email, **kwargs)

    def _address(self, **kwargs):
        from underground_crm.models.address import Address

        return Address.objects.create(**kwargs)

    def test_electoral_districts_stored_correctly(self):
        person = self._person(
            federal_district="Melbourne",
            state_lower_district="Prahran",
            state_upper_district="Southern Metropolitan",
            council_district="City of Stonnington",
            ward="Toorak",
        )
        person.refresh_from_db()
        self.assertEqual(person.federal_district, "Melbourne")
        self.assertEqual(person.state_lower_district, "Prahran")
        self.assertEqual(person.state_upper_district, "Southern Metropolitan")
        self.assertEqual(person.council_district, "City of Stonnington")
        self.assertEqual(person.ward, "Toorak")

    def test_home_and_mailing_address_can_differ(self):
        home = self._address(line1="1 Home St", city="Fitzroy", state="VIC", postcode="3065")
        mailing = self._address(line1="PO Box 42", city="Melbourne", state="VIC", postcode="3000")
        person = self._person(home_address=home, mailing_address=mailing)
        person.refresh_from_db()
        self.assertEqual(person.home_address.line1, "1 Home St")
        self.assertEqual(person.mailing_address.line1, "PO Box 42")

    def test_address_roles_are_independent(self):
        home = self._address(line1="1 Home St", city="Fitzroy")
        billing = self._address(line1="99 Billing Rd", city="Richmond")
        person = self._person(home_address=home, billing_address=billing)
        self.assertNotEqual(person.home_address_id, person.billing_address_id)
        self.assertFalse(home.is_equivalent(billing))

    def test_one_to_one_prevents_sharing_address_between_persons(self):
        from django.db import IntegrityError

        home = self._address(line1="1 Shared St", city="Brunswick")
        self._person(email="first@example.com", home_address=home)
        with self.assertRaises(IntegrityError):
            self._person(email="second@example.com", home_address=home)


class PersonCreatedAtTest(django.test.TestCase):
    """Person.created_at defaults to the moment of creation, like an ordinary
    signup, but — unlike an auto_now_add field — an explicit value assigned
    before save() is kept rather than being overwritten. import_people_csv
    relies on this to record a member's real legacy join date in the same
    save() that writes every other imported field."""

    # How close a created_at left to its default must land to the moment the
    # test itself observed, to allow for the time save() takes to run without
    # letting a genuine bug (e.g. a stale or null timestamp) pass unnoticed.
    CLOCK_TOLERANCE = timedelta(seconds=5)

    def test_defaults_to_now_when_not_supplied(self):
        before_save = timezone.now()
        person = Person.objects.create(email="new.signup@example.com")
        after_save = timezone.now()

        self.assertTrue(
            before_save - self.CLOCK_TOLERANCE
            <= person.created_at
            <= after_save + self.CLOCK_TOLERANCE,
            msg=f"A Person created with no created_at must be stamped with the current "
            f"time (between {before_save} and {after_save}, allowing {self.CLOCK_TOLERANCE} "
            f"of slack), the same as an ordinary website signup; got {person.created_at}.",
        )

    def test_an_explicit_value_survives_save(self):
        # A plausible legacy join date, well outside the CLOCK_TOLERANCE window
        # around "now" used above, so the two tests cannot pass for the same
        # accidental reason.
        legacy_join_date = datetime(2016, 4, 14, 15, 41, tzinfo=ZoneInfo("Australia/Melbourne"))

        person = Person(email="migrated.member@example.com", created_at=legacy_join_date)
        person.save()
        person.refresh_from_db()

        self.assertEqual(
            person.created_at,
            legacy_join_date,
            msg="created_at must not be an auto_now_add field: import_people_csv sets it "
            "explicitly to the legacy CRM's own join date before the single save() call "
            "that also writes the rest of the row, and that value must survive.",
        )
