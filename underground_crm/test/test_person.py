import logging
import unittest
from string import whitespace, punctuation

import django.test

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
