import logging
import unittest
from datetime import datetime, timedelta
from string import whitespace, punctuation
from zoneinfo import ZoneInfo

import django.test
from django.utils import timezone

from underground_crm.models import Person
from underground_crm.models.person import PARTIAL_EMAIL_ADDRESS_LENGTH

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
        # en-US is listed first with no explicit q-value, so it takes the implicit
        # q=1.0 and outranks every other tag in the header.
        browser_language_header = "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"
        highest_priority_tag = browser_language_header.split(",")[0].split(";")[0]
        self.assertEqual(
            self._person(browser_language_header).preferred_language,
            highest_priority_tag,
            msg=f"'{browser_language_header}' has no explicit q-value on its first tag, "
            f"so that tag ('{highest_priority_tag}') carries the implicit q=1.0 and "
            "should be preferred over every other tag in the header.",
        )

    def test_preferred_language_respects_q_values(self):
        # zh-TW is listed second but has an implicit q=1.0, so it beats en;q=0.9.
        browser_language_header = "en;q=0.9,zh-TW"
        highest_priority_tag = "zh-TW"
        self.assertEqual(
            self._person(browser_language_header).preferred_language,
            highest_priority_tag,
            msg=f"In '{browser_language_header}', '{highest_priority_tag}' carries the "
            "implicit q=1.0 and should outrank the explicit q=0.9 on the other tag, "
            "regardless of listing order.",
        )

    def test_preferred_language_single_tag(self):
        only_tag = "fr-FR"
        self.assertEqual(
            self._person(only_tag).preferred_language,
            only_tag,
            msg=f"With only one language tag ('{only_tag}') present, that tag is "
            "necessarily the preferred one.",
        )

    def test_preferred_language_defaults_to_en_au_when_unset(self):
        default_language = "en-AU"
        self.assertEqual(
            self._person(None).preferred_language,
            default_language,
            msg=f"With no language_preferences recorded at all, preferred_language "
            f"should fall back to '{default_language}', the CRM's default locale "
            "(Australia/Melbourne).",
        )

    def test_preferred_language_defaults_to_en_au_when_empty(self):
        default_language = "en-AU"
        self.assertEqual(
            self._person("").preferred_language,
            default_language,
            msg=f"An empty language_preferences string carries no usable tag, so "
            f"preferred_language should fall back to '{default_language}' just as it "
            "does when language_preferences is unset entirely.",
        )

    def test_language_count_from_browser_string(self):
        # The count should reflect however many comma-separated tags the header
        # actually lists, not a number re-typed by hand.
        browser_language_header = "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"
        expected_count = len(browser_language_header.split(","))
        self.assertEqual(
            self._person(browser_language_header).language_count(),
            expected_count,
            msg=f"'{browser_language_header}' lists {expected_count} comma-separated "
            "language tags, so language_count() should report that many.",
        )

    def test_language_count_single_language(self):
        browser_language_header = "ja"
        expected_count = len(browser_language_header.split(","))
        self.assertEqual(
            self._person(browser_language_header).language_count(),
            expected_count,
            msg=f"'{browser_language_header}' lists a single language tag, so "
            f"language_count() should report {expected_count}.",
        )

    def test_language_count_when_unset(self):
        self.assertEqual(
            self._person(None).language_count(),
            0,
            msg="With no language_preferences recorded at all, there are no tags to "
            "count, so language_count() should report 0.",
        )

    def test_language_count_when_empty(self):
        self.assertEqual(
            self._person("").language_count(),
            0,
            msg="An empty language_preferences string contains no tags, so "
            "language_count() should report 0.",
        )


class PersonPartialEmailAddressTest(unittest.TestCase):
    def test_masks_local_part_beyond_the_visible_length(self):
        # A real-looking address whose local part is longer than the visible length,
        # so the masking behaviour is actually exercised.
        email_address = "owen9825@gmail.com"
        local_part = email_address.split("@")[0]
        self.assertGreater(
            len(local_part),
            PARTIAL_EMAIL_ADDRESS_LENGTH,
            msg=f"'{email_address}' was chosen to exercise the masking behaviour, so its "
            f"local part must be longer than PARTIAL_EMAIL_ADDRESS_LENGTH "
            f"({PARTIAL_EMAIL_ADDRESS_LENGTH}); otherwise this test would not be "
            "checking what it claims to.",
        )
        person = Person(email=email_address)

        expected_prefix = local_part[:PARTIAL_EMAIL_ADDRESS_LENGTH]
        visible_prefix, _, rest = person.partial_email_address.partition("…")
        self.assertEqual(
            visible_prefix,
            expected_prefix,
            msg=f"The first PARTIAL_EMAIL_ADDRESS_LENGTH characters of the local part "
            f"should be kept in full, so '{email_address}' should become "
            f"'{expected_prefix}…@gmail.com'; got '{person.partial_email_address}'.",
        )
        self.assertTrue(
            rest.startswith("@"),
            msg=f"The domain should follow the ellipsis unchanged; got "
            f"'{person.partial_email_address}'.",
        )

    def test_local_part_at_the_visible_length_is_shown_in_full(self):
        # A realistic local part cut down to exactly PARTIAL_EMAIL_ADDRESS_LENGTH
        # characters, so there is nothing left to hide and no ellipsis should appear.
        given_name = "hamish"
        self.assertGreaterEqual(
            len(given_name),
            PARTIAL_EMAIL_ADDRESS_LENGTH,
            msg=f"'{given_name}' must be at least PARTIAL_EMAIL_ADDRESS_LENGTH "
            f"({PARTIAL_EMAIL_ADDRESS_LENGTH}) characters long, or slicing it below would "
            "not actually reach the boundary this test is meant to exercise.",
        )
        local_part = given_name[:PARTIAL_EMAIL_ADDRESS_LENGTH]
        email_address = f"{local_part}@example.com"
        person = Person(email=email_address)

        self.assertEqual(
            person.partial_email_address,
            email_address,
            msg=f"A local part of exactly PARTIAL_EMAIL_ADDRESS_LENGTH "
            f"({PARTIAL_EMAIL_ADDRESS_LENGTH}) characters leaves nothing beyond the "
            f"visible portion, so '{email_address}' should be returned unchanged rather "
            "than gaining a pointless ellipsis.",
        )

    def test_short_local_part_is_shown_in_full(self):
        # A local part shorter than PARTIAL_EMAIL_ADDRESS_LENGTH, e.g. a real short
        # given name.
        email_address = "amy@example.com"
        local_part = email_address.split("@")[0]
        self.assertLess(
            len(local_part),
            PARTIAL_EMAIL_ADDRESS_LENGTH,
            msg=f"'{email_address}' was chosen to exercise the short-local-part case, so "
            f"its local part must be shorter than PARTIAL_EMAIL_ADDRESS_LENGTH "
            f"({PARTIAL_EMAIL_ADDRESS_LENGTH}); otherwise this test would not be "
            "checking what it claims to.",
        )
        person = Person(email=email_address)

        self.assertEqual(
            person.partial_email_address,
            email_address,
            msg=f"'{email_address}' has a local part shorter than "
            f"PARTIAL_EMAIL_ADDRESS_LENGTH ({PARTIAL_EMAIL_ADDRESS_LENGTH}), so it should "
            "be returned unchanged rather than being masked.",
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
