import csv
import logging
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import django.test
import phonenumbers
from django.core.management import CommandError, call_command

from underground_crm.contactability import (
    InvalidPhoneNumberError,
    get_validated_email_address,
    parse_verified_phone_number,
)
from underground_crm.management.commands.import_people_csv import (
    AmbiguousPersonMatchError,
    _LEGACY_SOURCE_TIMEZONE_ENV_VAR,
    _placeholder_email,
    _resolve_first_and_preferred_name,
    find_existing_person,
    get_legacy_source_timezone,
    parse_legacy_created_at,
    parse_memberships,
)
from underground_crm.models import Membership, MembershipType, Person


def _write_people_csv(rows: list[dict]) -> str:
    """Write ``rows`` as a legacy-export CSV to a temp file and return its path.

    The header is the union of every row's keys, in first-seen order, so each
    test writes only the columns it actually cares about.
    """
    fieldnames = list({key: None for row in rows for key in row})
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


# A legacy CSV row is a plain dict of strings, as produced by csv.DictReader.
# These fields are the only ones _resolve_first_and_preferred_name reads; the
# rest of a real export row is irrelevant here.
COLLOQUIAL_FIRST_NAME = "Bex"
LEGAL_NAME = "Rebecca Thornton"
DISTINCT_PREFERRED_NAME = "Bexy"


def _row(first_name: str = "", legal_name: str = "", preferred_name: str = "") -> dict:
    return {
        "first_name": first_name,
        "legal_name": legal_name,
        "preferred_name": preferred_name,
    }


class ResolveFirstAndPreferredNameTest(unittest.TestCase):
    """
    The legacy CRM exports a person's everyday name as first_name and their
    formal name (if captured at all) as legal_name, with no preferred_name
    column of its own for that case. Person.first_name is documented as the
    electoral-roll name, so when a row carries both a first_name and a
    legal_name but no preferred_name, the two must be swapped on import:
    legal_name becomes first_name, and the original first_name is kept as
    preferred_name rather than being discarded.
    """

    def test_legal_name_without_preferred_name_swaps_first_and_preferred(self):
        row = _row(first_name=COLLOQUIAL_FIRST_NAME, legal_name=LEGAL_NAME)

        first_name, preferred_name = _resolve_first_and_preferred_name(row)

        self.assertEqual(
            first_name,
            LEGAL_NAME,
            "With a legal_name present and no preferred_name of its own, the "
            "legal_name must become Person.first_name, since that field is "
            "documented as holding the electoral-roll name.",
        )
        self.assertEqual(
            preferred_name,
            COLLOQUIAL_FIRST_NAME,
            "The legacy first_name is the person's everyday name, so it must "
            "survive the import as preferred_name rather than being dropped "
            "once legal_name takes over first_name.",
        )

    def test_existing_preferred_name_is_left_untouched(self):
        row = _row(
            first_name=COLLOQUIAL_FIRST_NAME,
            legal_name=LEGAL_NAME,
            preferred_name=DISTINCT_PREFERRED_NAME,
        )

        first_name, preferred_name = _resolve_first_and_preferred_name(row)

        self.assertEqual(
            first_name,
            COLLOQUIAL_FIRST_NAME,
            "A row that already has its own preferred_name has already "
            "distinguished the two names, so first_name must be left as the "
            "legacy system exported it rather than being overwritten by legal_name.",
        )
        self.assertEqual(
            preferred_name,
            DISTINCT_PREFERRED_NAME,
            "The row's own preferred_name must be kept rather than replaced by "
            "the legacy first_name, since it is more specific than a value this "
            "function would infer.",
        )

    def test_missing_legal_name_leaves_first_name_as_is(self):
        row = _row(first_name=COLLOQUIAL_FIRST_NAME)

        first_name, preferred_name = _resolve_first_and_preferred_name(row)

        self.assertEqual(
            first_name,
            COLLOQUIAL_FIRST_NAME,
            "With no legal_name to promote, there is nothing to swap first_name "
            "with, so it must pass through unchanged.",
        )
        self.assertIsNone(
            preferred_name,
            "No preferred_name was supplied and no swap occurred to produce "
            "one, so it must remain unset rather than being invented.",
        )

    def test_missing_first_name_leaves_legal_name_unused(self):
        row = _row(legal_name=LEGAL_NAME)

        first_name, preferred_name = _resolve_first_and_preferred_name(row)

        self.assertIsNone(
            first_name,
            "There is no legacy first_name to move into preferred_name, so "
            "promoting legal_name here would lose the fact that first_name "
            "was never captured for this person.",
        )
        self.assertIsNone(
            preferred_name,
            "With no first_name to preserve, preferred_name must stay unset.",
        )


# Plausible values lifted from a real legacy CSV export, so the parsing logic is
# exercised against the actual format rather than an invented one: a member who
# joined the "Science" party and was carried over into "Fusion" when it renamed.
_SCIENCE_MEMBERSHIP_NAME = "Science"
_SCIENCE_STARTED_AT_RAW = "2013-08-12 00:00:00 +1000"
_FUSION_MEMBERSHIP_NAME = "Fusion"
_FUSION_STARTED_AT_RAW = "2022-02-06 19:46:10 +1100"


class ParseMembershipsTest(django.test.SimpleTestCase):
    def test_aligns_names_with_parallel_date_columns(self):
        row = {
            "membership_names": f"{_SCIENCE_MEMBERSHIP_NAME},{_FUSION_MEMBERSHIP_NAME}",
            "memberships_started_at": f"{_SCIENCE_STARTED_AT_RAW},{_FUSION_STARTED_AT_RAW}",
            "memberships_expires_on": ",",
            "memberships_suspended_at": ",",
        }

        memberships = parse_memberships(row)

        expected_count = 2
        self.assertEqual(
            len(memberships),
            expected_count,
            msg="Both comma-separated membership_names entries should produce a membership",
        )
        science_name, science_started_at, science_expires_on, science_suspended_at = memberships[0]
        self.assertEqual(science_name, _SCIENCE_MEMBERSHIP_NAME)
        self.assertEqual(
            science_started_at,
            datetime(2013, 8, 12, 0, 0, 0, tzinfo=ZoneInfo("Etc/GMT-10")),
            msg="started_at must parse the legacy timestamp's own UTC offset, not assume local time",
        )
        self.assertIsNone(science_expires_on)
        self.assertIsNone(science_suspended_at)
        fusion_name, _fusion_started_at, _fusion_expires_on, _fusion_suspended_at = memberships[1]
        self.assertEqual(fusion_name, _FUSION_MEMBERSHIP_NAME)

    def test_returns_empty_list_when_membership_names_blank(self):
        row = {
            "membership_names": "",
            "memberships_started_at": "",
            "memberships_expires_on": "",
            "memberships_suspended_at": "",
        }

        self.assertEqual(
            parse_memberships(row),
            [],
            msg="A person with no legacy membership history should yield no memberships",
        )

    def test_drops_entry_with_no_parseable_started_at(self):
        row = {
            "membership_names": _SCIENCE_MEMBERSHIP_NAME,
            "memberships_started_at": "",
            "memberships_expires_on": "",
            "memberships_suspended_at": "",
        }

        self.assertEqual(
            parse_memberships(row),
            [],
            msg="Membership.started_at is a required field, so an entry without one can't be created",
        )

    def test_skips_all_entries_on_mismatched_column_lengths(self):
        row = {
            "membership_names": f"{_SCIENCE_MEMBERSHIP_NAME},{_FUSION_MEMBERSHIP_NAME}",
            "memberships_started_at": _SCIENCE_STARTED_AT_RAW,  # only one date for two names
            "memberships_expires_on": ",",
            "memberships_suspended_at": ",",
        }

        with self.assertLogs(
            "underground_crm.management.commands.import_people_csv", level=logging.WARNING
        ):
            memberships = parse_memberships(row)

        self.assertEqual(
            memberships,
            [],
            msg="Positional alignment is broken when the columns disagree in length, "
            "so nothing can be safely attributed to a name",
        )


class ImportPeopleCsvMembershipTest(django.test.TestCase):
    """Exercises the command against a real CSV file and a real test database —
    the legacy API is never contacted here, since --with-interactions/--with-notes
    are not passed, so no mocking is needed to keep this test hermetic."""

    _LEGACY_ID = "48210"
    _EMAIL = "grace.meng@example.org"

    def _write_csv(self, **overrides) -> str:
        row = {
            "nationbuilder_id": self._LEGACY_ID,
            "first_name": "Grace",
            "last_name": "Meng",
            "email": self._EMAIL,
            "membership_names": f"{_SCIENCE_MEMBERSHIP_NAME},{_FUSION_MEMBERSHIP_NAME}",
            "memberships_started_at": f"{_SCIENCE_STARTED_AT_RAW},{_FUSION_STARTED_AT_RAW}",
            "memberships_expires_on": ",",
            "memberships_suspended_at": ",",
        }
        row.update(overrides)
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerow(row)
        self.addCleanup(os.remove, path)
        return path

    def test_seeds_membership_type_and_membership_from_csv(self):
        csv_path = self._write_csv()

        call_command("import_people_csv", csv_path)

        person = Person.objects.get(legacy_id=int(self._LEGACY_ID))
        self.assertTrue(
            MembershipType.objects.filter(name=_SCIENCE_MEMBERSHIP_NAME).exists(),
            msg="The first membership_names entry should seed a MembershipType",
        )
        self.assertTrue(
            MembershipType.objects.filter(name=_FUSION_MEMBERSHIP_NAME).exists(),
            msg="The second membership_names entry should seed a MembershipType",
        )
        expected_membership_count = 2
        self.assertEqual(
            Membership.objects.filter(person=person).count(),
            expected_membership_count,
            msg="One Membership per comma-separated membership_names entry should be created",
        )
        fusion_membership = Membership.objects.get(
            person=person, type__name=_FUSION_MEMBERSHIP_NAME
        )
        self.assertEqual(
            fusion_membership.started_at,
            datetime(2022, 2, 6, 19, 46, 10, tzinfo=ZoneInfo("Etc/GMT-11")),
        )

    def test_running_twice_does_not_duplicate_memberships(self):
        csv_path = self._write_csv()

        call_command("import_people_csv", csv_path)
        call_command("import_people_csv", csv_path)

        person = Person.objects.get(legacy_id=int(self._LEGACY_ID))
        expected_membership_count = 2
        self.assertEqual(
            Membership.objects.filter(person=person).count(),
            expected_membership_count,
            msg="get_or_create on (person, type, started_at) must keep re-imports idempotent",
        )
        self.assertEqual(
            MembershipType.objects.filter(name=_SCIENCE_MEMBERSHIP_NAME).count(),
            1,
            msg="get_or_create on name must not create a second MembershipType on re-import",
        )

    def test_shared_membership_type_is_reused_across_people(self):
        first_csv = self._write_csv()
        second_csv = self._write_csv(
            nationbuilder_id="90142",
            first_name="Halvard",
            last_name="Osei",
            email="halvard.osei@example.org",
            membership_names=_SCIENCE_MEMBERSHIP_NAME,
            memberships_started_at="2014-05-11 00:00:00 +1000",
            memberships_expires_on="",
            memberships_suspended_at="",
        )

        call_command("import_people_csv", first_csv)
        call_command("import_people_csv", second_csv)

        self.assertEqual(
            MembershipType.objects.filter(name=_SCIENCE_MEMBERSHIP_NAME).count(),
            1,
            msg="Two people who both belong to 'Science' should share a single MembershipType row",
        )

    def test_invalid_phone_number_continues(self):
        # "0412 345" parses as a phone number but is too short to be valid.
        phone_number = "0412 345"
        with self.assertRaises(InvalidPhoneNumberError):
            # Our test was relying on this number being invalid
            parse_verified_phone_number(phone_number)
        csv_path = self._write_csv(phone_number=phone_number)
        call_command("import_people_csv", csv_path)
        retrieved = Person.objects.get(legacy_id=int(self._LEGACY_ID))
        self.assertFalse(
            retrieved.phone_number,
            msg="The phone number should have been skipped",
        )

    def test_bare_landline_is_repaired_using_the_row_state(self):
        # An 8-digit subscriber number with no area code is invalid alone...
        phone_number = "5499 3656"
        with self.assertRaises(InvalidPhoneNumberError):
            parse_verified_phone_number(phone_number)
        # ...but the person's state pins it to the Victorian (03) area code.
        csv_path = self._write_csv(phone_number=phone_number, address_state="VIC")
        call_command("import_people_csv", csv_path)
        retrieved = Person.objects.get(legacy_id=int(self._LEGACY_ID))
        self.assertEqual(
            str(retrieved.phone_number),
            "+61354993656",
            msg="The bare landline should have been repaired with the VIC area code",
        )

    def test_bare_landline_without_a_state_is_still_skipped(self):
        # Same number, no state: ambiguous between the 03 and 07 codes, so it
        # is left out rather than guessed.
        csv_path = self._write_csv(phone_number="5499 3656")
        call_command("import_people_csv", csv_path)
        retrieved = Person.objects.get(legacy_id=int(self._LEGACY_ID))
        self.assertFalse(
            retrieved.phone_number,
            msg="An ambiguous bare landline should not be guessed",
        )


class FindMatchingPersonTest(django.test.TestCase):
    """The importer trusts the CSV email address above the legacy numeric ID: a
    row reconnects to whichever existing Person currently holds that email, and
    only falls back to the legacy_id (then the first name) when the row carries
    no usable email of its own. legacy_id is adopted onto a match that lacked
    one, but is never used to override, or stolen from, an already-established
    identity — legacy_id is unique, so two people can never share it."""

    LEGACY_ID = 60421
    CURRENT_EMAIL = "priya.raman@example.org"
    FIRST_NAME = "Priya"

    def test_returns_none_for_a_row_that_matches_nobody(self):
        match = find_existing_person(
            legacy_id=self.LEGACY_ID, email=self.CURRENT_EMAIL, first_name=self.FIRST_NAME
        )

        self.assertIsNone(
            match,
            msg="Neither the legacy_id nor the email address is in use yet, so this row "
            "describes a person who isn't in the CRM at all.",
        )

    def test_adopts_a_signup_matched_by_email_and_assigns_it_the_legacy_id(self):
        signup = Person.objects.create(email=self.CURRENT_EMAIL, first_name=self.FIRST_NAME)

        match = find_existing_person(
            legacy_id=self.LEGACY_ID, email=self.CURRENT_EMAIL, first_name=self.FIRST_NAME
        )

        self.assertEqual(
            match,
            signup,
            msg="email is unique on Person, so a matching address must reconnect the row "
            "to the existing signup rather than create a duplicate.",
        )
        self.assertEqual(
            match.legacy_id,
            self.LEGACY_ID,
            msg="A record that had no legacy_id of its own adopts the row's, so the next "
            "run matches it on legacy_id directly.",
        )

    def test_reimporting_an_already_matched_row_leaves_the_legacy_id_unchanged(self):
        already_matched = Person.objects.create(
            email=self.CURRENT_EMAIL, first_name=self.FIRST_NAME, legacy_id=self.LEGACY_ID
        )

        match = find_existing_person(
            legacy_id=self.LEGACY_ID, email=self.CURRENT_EMAIL, first_name=self.FIRST_NAME
        )

        self.assertEqual(
            match,
            already_matched,
            msg="Re-running the import on a row that was already fully matched must "
            "reconnect to the same person, not create or lose one.",
        )
        self.assertEqual(
            match.legacy_id,
            self.LEGACY_ID,
            msg="An already-correct legacy_id must survive a repeat run unchanged.",
        )

    def test_email_wins_when_it_belongs_to_a_different_person_than_the_legacy_id(self):
        matched_by_legacy_id = Person.objects.create(
            email="priya.raman.2016@example.org", legacy_id=self.LEGACY_ID
        )
        # The same real person, re-registered later under a new address that the
        # legacy system never attached this legacy_id to — this is the identity
        # the row should update.
        matched_by_email = Person.objects.create(email=self.CURRENT_EMAIL)

        match = find_existing_person(
            legacy_id=self.LEGACY_ID, email=self.CURRENT_EMAIL, first_name=self.FIRST_NAME
        )

        self.assertEqual(
            match,
            matched_by_email,
            msg="The email address is trusted above the legacy_id, so the row must update "
            "whoever currently holds that email, not whoever holds the legacy_id.",
        )
        self.assertIsNone(
            match.legacy_id,
            msg="legacy_id is unique and already belongs to a different person, so it "
            "must not be transferred onto the email match.",
        )
        matched_by_legacy_id.refresh_from_db()
        self.assertEqual(
            matched_by_legacy_id.legacy_id,
            self.LEGACY_ID,
            msg="The person originally holding the legacy_id must be left untouched by a "
            "row that was redirected to someone else by email.",
        )

    def test_email_wins_even_when_that_record_already_owns_a_different_legacy_id(self):
        Person.objects.create(email="priya.raman.2016@example.org", legacy_id=self.LEGACY_ID)
        other_legacy_id = self.LEGACY_ID + 1
        matched_by_email = Person.objects.create(
            email=self.CURRENT_EMAIL, legacy_id=other_legacy_id
        )

        match = find_existing_person(
            legacy_id=self.LEGACY_ID, email=self.CURRENT_EMAIL, first_name=self.FIRST_NAME
        )

        self.assertEqual(
            match,
            matched_by_email,
            msg="The email address is trusted above the legacy_id even when the email "
            "match already carries an established legacy_id of its own.",
        )
        self.assertEqual(
            match.legacy_id,
            other_legacy_id,
            msg="An email match's own legacy_id must never be overwritten by a different "
            "row's legacy_id.",
        )

    def test_raises_when_the_email_belongs_to_someone_with_a_different_legacy_id_alone(self):
        established = Person.objects.create(email=self.CURRENT_EMAIL, legacy_id=self.LEGACY_ID)
        conflicting_legacy_id = self.LEGACY_ID + 1

        with self.assertRaises(AmbiguousPersonMatchError):
            find_existing_person(
                legacy_id=conflicting_legacy_id,
                email=self.CURRENT_EMAIL,
                first_name="A Different Person",
            )

        established.refresh_from_db()
        self.assertEqual(
            established.legacy_id,
            self.LEGACY_ID,
            msg="A rejected row must never overwrite the legacy_id of the person it "
            "collided with.",
        )

    def test_raises_when_a_legacy_id_match_has_a_different_stored_email(self):
        established = Person.objects.create(
            email="priya.old.address@example.org", legacy_id=self.LEGACY_ID
        )
        new_address = "priya.new.address@example.org"

        with self.assertRaises(AmbiguousPersonMatchError):
            find_existing_person(
                legacy_id=self.LEGACY_ID, email=new_address, first_name=self.FIRST_NAME
            )

        established.refresh_from_db()
        self.assertEqual(
            established.email,
            "priya.old.address@example.org",
            msg="A rejected row must not silently change the email address of the "
            "person it was trying to update.",
        )

    def test_falls_back_to_first_name_when_the_row_has_no_usable_email(self):
        # An earlier import run created this person with no real email of their own.
        record_without_contact_details = Person.objects.create(
            email=_placeholder_email(self.LEGACY_ID), first_name=self.FIRST_NAME
        )

        match = find_existing_person(
            legacy_id=self.LEGACY_ID,
            email=_placeholder_email(self.LEGACY_ID),
            first_name=self.FIRST_NAME,
        )

        self.assertEqual(
            match,
            record_without_contact_details,
            msg="With only a placeholder email on the row, the first name is the last key "
            "available to confirm the legacy_id match is the same person.",
        )

    def test_a_same_named_record_under_a_different_legacy_id_is_not_adopted(self):
        someone_elses_legacy_id = 99999
        Person.objects.create(
            email="the.other.priya@example.org",
            first_name=self.FIRST_NAME,
            legacy_id=someone_elses_legacy_id,
        )

        match = find_existing_person(
            legacy_id=self.LEGACY_ID,
            email=_placeholder_email(self.LEGACY_ID),
            first_name=self.FIRST_NAME,
        )

        self.assertIsNone(
            match,
            msg="A record that already carries its own legacy_id belongs to a different "
            "legacy person; a bare first-name match must not adopt it, even when the "
            "name matches.",
        )

    def test_raises_when_a_legacy_id_match_has_a_different_first_name(self):
        established = Person.objects.create(
            email=_placeholder_email(self.LEGACY_ID),
            first_name="Priyanka",
            legacy_id=self.LEGACY_ID,
        )

        with self.assertRaises(AmbiguousPersonMatchError):
            find_existing_person(
                legacy_id=self.LEGACY_ID,
                email=_placeholder_email(self.LEGACY_ID),
                first_name=self.FIRST_NAME,
            )

        established.refresh_from_db()
        self.assertEqual(
            established.first_name,
            "Priyanka",
            msg="A rejected row must not overwrite the name of the record it collided " "with.",
        )

    def test_raises_when_a_legacy_id_match_has_no_first_name_to_confirm_identity(self):
        Person.objects.create(email=_placeholder_email(self.LEGACY_ID), legacy_id=self.LEGACY_ID)

        with self.assertRaises(AmbiguousPersonMatchError):
            find_existing_person(
                legacy_id=self.LEGACY_ID,
                email=_placeholder_email(self.LEGACY_ID),
                first_name=None,
            )


class ImportPeopleCsvIdentityMatchTest(django.test.TestCase):
    """The command as a whole, run against a real CSV and test database, must be
    idempotent even when the database already holds some of these people from
    another source (a website signup, an earlier partial import)."""

    # The importer only stores a CSV email address whose domain resolves an MX
    # record (contactability.get_validated_email_address); a mailbox provider is
    # used so the email-match path is genuinely exercised where DNS is available,
    # and skipped — not failed — where it is not.
    DELIVERABLE_DOMAIN = "gmail.com"

    def _require_email_deliverability(self) -> None:
        probe = f"deliverability.probe@{self.DELIVERABLE_DOMAIN}"
        if get_validated_email_address(probe) is None:
            self.skipTest(
                "Validating a CSV email address needs an MX lookup, and DNS is "
                "unavailable here, so the importer would fall back to a placeholder "
                "address and never take the email-match path under test."
            )

    def test_adopts_a_prior_website_signup_by_email_instead_of_duplicating_it(self):
        self._require_email_deliverability()
        address = f"lena.fischer.mp@{self.DELIVERABLE_DOMAIN}"
        surname = "Fischer"
        legacy_id = 71230
        signup = Person.objects.create(email=address, first_name="Lena")

        csv_path = _write_people_csv(
            [
                {
                    "nationbuilder_id": str(legacy_id),
                    "first_name": "Lena",
                    "last_name": surname,
                    "email": address,
                }
            ]
        )
        self.addCleanup(os.remove, csv_path)

        call_command("import_people_csv", csv_path)

        expected_person_count = 1
        self.assertEqual(
            Person.objects.filter(email=address).count(),
            expected_person_count,
            msg="The row matched an existing signup by email, so it must update that "
            "record in place rather than create a second Person for the same address.",
        )
        signup.refresh_from_db()
        self.assertEqual(
            signup.legacy_id,
            legacy_id,
            msg="A record first matched by email must adopt the row's legacy ID so the "
            "next run matches it on legacy_id directly.",
        )
        self.assertEqual(
            signup.last_name,
            surname,
            msg="The mapped CSV fields must still be written onto the adopted record.",
        )

    def test_reimporting_an_email_less_row_is_idempotent(self):
        # The row carries no email column at all, so the importer gives it a
        # placeholder address derived from its own legacy ID. Needs no DNS.
        first_name = "Ngaire"
        legacy_id = 88120
        surname = "Whitlam"

        csv_path = _write_people_csv(
            [
                {
                    "nationbuilder_id": str(legacy_id),
                    "first_name": first_name,
                    "last_name": surname,
                }
            ]
        )
        self.addCleanup(os.remove, csv_path)

        call_command("import_people_csv", csv_path)
        call_command("import_people_csv", csv_path)

        expected_person_count = 1
        self.assertEqual(
            Person.objects.count(),
            expected_person_count,
            msg="Re-running the import on the same email-less row must reconnect to the "
            "person it created the first time — via legacy_id and the placeholder email "
            "together — rather than create a second Person.",
        )
        person = Person.objects.get(legacy_id=legacy_id)
        self.assertEqual(
            person.last_name,
            surname,
            msg="The mapped CSV fields must still be written on the second run.",
        )

    def test_row_whose_email_belongs_to_another_legacy_person_is_skipped(self):
        self._require_email_deliverability()
        shared_address = f"front.desk@{self.DELIVERABLE_DOMAIN}"
        established_legacy_id = 4001
        established_first_name = "Tom"
        established = Person.objects.create(
            email=shared_address,
            legacy_id=established_legacy_id,
            first_name=established_first_name,
        )
        conflicting_legacy_id = 4002

        csv_path = _write_people_csv(
            [
                {
                    "nationbuilder_id": str(conflicting_legacy_id),
                    "first_name": "Tanya",
                    "email": shared_address,
                }
            ]
        )
        self.addCleanup(os.remove, csv_path)

        call_command("import_people_csv", csv_path)

        self.assertFalse(
            Person.objects.filter(legacy_id=conflicting_legacy_id).exists(),
            msg="The row's email already belongs to another legacy person, so the row "
            "must be skipped rather than collide on the unique email or legacy_id.",
        )
        established.refresh_from_db()
        self.assertEqual(
            established.first_name,
            established_first_name,
            msg="A skipped row must leave the record it collided with untouched.",
        )

    def test_a_conflicting_legacy_id_is_ignored_in_favour_of_the_email_match(self):
        self._require_email_deliverability()
        shared_address = f"jordan.blake@{self.DELIVERABLE_DOMAIN}"
        legacy_id = 5001
        record_under_the_legacy_id = Person.objects.create(
            email="jordan.blake.old@example.org", legacy_id=legacy_id, first_name="Jordan"
        )
        # A website signup that predates the legacy import and was never given a
        # legacy_id — this is the identity the CSV row's email actually belongs to.
        signup = Person.objects.create(email=shared_address, first_name="J")

        csv_path = _write_people_csv(
            [
                {
                    "nationbuilder_id": str(legacy_id),
                    "first_name": "Jordan",
                    "last_name": "Blake",
                    "email": shared_address,
                }
            ]
        )
        self.addCleanup(os.remove, csv_path)

        call_command("import_people_csv", csv_path)

        signup.refresh_from_db()
        self.assertEqual(
            signup.last_name,
            "Blake",
            msg="The email address is trusted above the legacy_id, so the row must "
            "update whoever currently holds that email address.",
        )
        self.assertIsNone(
            signup.legacy_id,
            msg="legacy_id is unique and already belongs to a different Person, so it "
            "must not be transferred onto the email match.",
        )
        record_under_the_legacy_id.refresh_from_db()
        self.assertEqual(
            record_under_the_legacy_id.first_name,
            "Jordan",
            msg="The person who already holds the legacy_id must be left untouched by "
            "a row that was redirected to someone else by email.",
        )


# The legacy CRM writes the date a person was first recorded as an American-format
# (month/day/year) local wall-clock time with no offset, and the loader has to
# reconstruct the absolute instant. This is the exact shape seen in a real export,
# down to the two spaces the legacy system puts between the date and the time.
LEGACY_CREATED_AT_CELL = "04/14/2016  3:41 PM"
# The same instant, spelled out in unambiguous parts so a reader can check the
# American-format reading (14 April, not the non-existent "day 4 of month 14")
# and the 12-hour-to-24-hour conversion by eye.
JOIN_YEAR, JOIN_MONTH, JOIN_DAY = 2016, 4, 14
JOIN_HOUR_24, JOIN_MINUTE = 15, 41

PARTY_TIMEZONE_NAME = "Australia/Melbourne"
# A zone whose offset from Melbourne is never zero at any time of year, so a
# timestamp read in it lands on a demonstrably different absolute instant.
OVERSEAS_TIMEZONE_NAME = "America/New_York"


class GetLegacySourceTimezoneTest(unittest.TestCase):
    """The zone that the legacy export's naive timestamps are interpreted in is a
    deployment choice: it defaults to the party's own zone but must be overridable
    for an import of another organisation's data."""

    def test_defaults_to_the_party_timezone_when_the_variable_is_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_LEGACY_SOURCE_TIMEZONE_ENV_VAR, None)
            self.assertEqual(
                get_legacy_source_timezone(),
                ZoneInfo(PARTY_TIMEZONE_NAME),
                msg="With no override set, legacy timestamps are assumed to be in the "
                "party's own timezone.",
            )

    def test_reads_the_timezone_named_in_the_environment_variable(self):
        with mock.patch.dict(os.environ, {_LEGACY_SOURCE_TIMEZONE_ENV_VAR: OVERSEAS_TIMEZONE_NAME}):
            self.assertEqual(
                get_legacy_source_timezone(),
                ZoneInfo(OVERSEAS_TIMEZONE_NAME),
                msg="The environment variable must select the zone so an export from "
                "another region is imported with the right offset.",
            )

    def test_rejects_a_string_that_is_not_a_known_timezone(self):
        not_a_zone = "Australia/Melbroune"  # transposed letters, a plausible typo
        with mock.patch.dict(os.environ, {_LEGACY_SOURCE_TIMEZONE_ENV_VAR: not_a_zone}):
            with self.assertRaises(CommandError):
                get_legacy_source_timezone()


class ParseLegacyCreatedAtTest(unittest.TestCase):
    """The ``created_at`` column of a legacy row becomes an aware datetime."""

    def test_reads_the_american_format_local_time_from_the_created_at_column(self):
        parsed = parse_legacy_created_at({"created_at": LEGACY_CREATED_AT_CELL})

        expected = datetime(
            JOIN_YEAR,
            JOIN_MONTH,
            JOIN_DAY,
            JOIN_HOUR_24,
            JOIN_MINUTE,
            tzinfo=ZoneInfo(PARTY_TIMEZONE_NAME),
        )
        self.assertEqual(
            parsed,
            expected,
            msg=f"{LEGACY_CREATED_AT_CELL!r} is month/day/year at 3:41 PM local time, so "
            f"it must parse to {JOIN_DAY:02d}/{JOIN_MONTH:02d}/{JOIN_YEAR} "
            f"{JOIN_HOUR_24}:{JOIN_MINUTE} in {PARTY_TIMEZONE_NAME}.",
        )

    def test_returns_none_when_the_row_has_no_created_at(self):
        self.assertIsNone(
            parse_legacy_created_at({}),
            msg="A row with no created_at column leaves the value unset rather than "
            "raising, so the import is not aborted for it.",
        )

    def test_interprets_the_same_wall_clock_time_in_the_configured_timezone(self):
        with mock.patch.dict(os.environ, {_LEGACY_SOURCE_TIMEZONE_ENV_VAR: OVERSEAS_TIMEZONE_NAME}):
            parsed = parse_legacy_created_at({"created_at": LEGACY_CREATED_AT_CELL})

        expected = datetime(
            JOIN_YEAR,
            JOIN_MONTH,
            JOIN_DAY,
            JOIN_HOUR_24,
            JOIN_MINUTE,
            tzinfo=ZoneInfo(OVERSEAS_TIMEZONE_NAME),
        )
        self.assertEqual(
            parsed,
            expected,
            msg="With the source timezone overridden, the naive wall-clock time is read "
            "in that zone.",
        )
        melbourne_reading = datetime(
            JOIN_YEAR,
            JOIN_MONTH,
            JOIN_DAY,
            JOIN_HOUR_24,
            JOIN_MINUTE,
            tzinfo=ZoneInfo(PARTY_TIMEZONE_NAME),
        )
        self.assertNotEqual(
            parsed,
            melbourne_reading,
            msg="New York and Melbourne are never at the same offset, so the two "
            "readings of one wall-clock string must be different absolute instants.",
        )


class ImportPeopleCsvCreatedAtTest(django.test.TestCase):
    """End to end: the join date in the CSV lands on the created Person, and a
    later re-run whose row carries no created_at of its own must not reset it —
    Person.created_at is not nullable, so the per-field update loop in handle()
    deliberately excludes it and relies on parse_legacy_created_at() instead."""

    LEGACY_ID = 60421
    FIRST_NAME = "Priya"
    LAST_NAME = "Raman"

    def _expected_join_datetime(self) -> datetime:
        return datetime(
            JOIN_YEAR,
            JOIN_MONTH,
            JOIN_DAY,
            JOIN_HOUR_24,
            JOIN_MINUTE,
            tzinfo=ZoneInfo(PARTY_TIMEZONE_NAME),
        )

    def test_created_person_takes_its_created_at_from_the_csv_row(self):
        csv_path = _write_people_csv(
            [
                {
                    "nationbuilder_id": str(self.LEGACY_ID),
                    "first_name": self.FIRST_NAME,
                    "last_name": self.LAST_NAME,
                    "created_at": LEGACY_CREATED_AT_CELL,
                }
            ]
        )
        self.addCleanup(os.remove, csv_path)

        call_command("import_people_csv", csv_path)

        person = Person.objects.get(legacy_id=self.LEGACY_ID)
        self.assertEqual(
            person.created_at,
            self._expected_join_datetime(),
            msg="The importer must persist the legacy created_at, not stamp the row "
            "with the time the import happened to run.",
        )

    def test_re_running_without_a_created_at_column_does_not_reset_it(self):
        first_csv_path = _write_people_csv(
            [
                {
                    "nationbuilder_id": str(self.LEGACY_ID),
                    "first_name": self.FIRST_NAME,
                    "last_name": self.LAST_NAME,
                    "created_at": LEGACY_CREATED_AT_CELL,
                }
            ]
        )
        self.addCleanup(os.remove, first_csv_path)
        call_command("import_people_csv", first_csv_path)
        join_date_from_first_run = Person.objects.get(legacy_id=self.LEGACY_ID).created_at

        # A later export of the same person that happens not to carry created_at
        # at all — the column is entirely absent, not just blank.
        second_csv_path = _write_people_csv(
            [
                {
                    "nationbuilder_id": str(self.LEGACY_ID),
                    "first_name": self.FIRST_NAME,
                    "last_name": self.LAST_NAME,
                }
            ]
        )
        self.addCleanup(os.remove, second_csv_path)
        call_command("import_people_csv", second_csv_path)

        person = Person.objects.get(legacy_id=self.LEGACY_ID)
        self.assertEqual(
            person.created_at,
            join_date_from_first_run,
            msg="Person.created_at is not nullable, so a re-run whose row has no "
            "created_at column must leave the previously recorded join date alone "
            "rather than null it out (or silently reset it to the second run's own "
            "start time).",
        )
