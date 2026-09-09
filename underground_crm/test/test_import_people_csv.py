import csv
import logging
import os
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import django.test
from django.core.management import call_command
from django.core.management.base import CommandError

from underground_crm.management.commands.import_people_csv import (
    _resolve_first_and_preferred_name,
    parse_memberships,
)
from underground_crm.models import Membership, MembershipType, Person

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

    def test_invalid_phone_number_aborts_and_names_the_row(self):
        # "0412 345" parses as a phone number but is too short to be valid.
        csv_path = self._write_csv(phone_number="0412 345")

        with self.assertRaises(CommandError) as ctx:
            call_command("import_people_csv", csv_path)

        message = str(ctx.exception)
        self.assertIn(self._LEGACY_ID, message)
        self.assertIn("0412 345", message)
        self.assertFalse(
            Person.objects.filter(legacy_id=int(self._LEGACY_ID)).exists(),
            msg="The row must not be half-imported when its phone number is rejected",
        )
