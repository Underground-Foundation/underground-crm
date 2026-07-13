import os

# The command module reads its legacy-CRM connection settings at import time.
# These tests only exercise the CSV → Person address mapping, which never
# contacts the legacy CRM, so placeholder values satisfy the import.
for _name, _value in {
    "LEGACY_ADMIN_URL": "https://legacy-crm.example.com",
    "LEGACY_API_TOKEN": "placeholder-token",
    "LEGACY_USER_AGENT": "underground-crm-tests",
    "LEGACY_ADMIN_COOKIE_FILE": "/dev/null",
}.items():
    os.environ.setdefault(_name, _value)

import django.test
from django.contrib.auth import get_user_model

from underground_crm.management.commands.import_people_csv import (
    PRIMARY_ADDRESS_FALLBACK_FIELDS,
    assign_addresses,
)
from underground_crm.models.address import Address

Person = get_user_model()

HOME = {"line1": "36 Flora St", "city": "Kirrawee", "state": "NSW", "postcode": "2232"}
WORK = {"line1": "15 Moore St", "city": "Canberra", "state": "ACT", "postcode": "2601"}
MAILING = {"line1": "PO Box 123", "city": "Chatswood", "state": "NSW", "postcode": "2057"}
REGISTERED = {"line1": "8 Wattle Ave", "city": "Carlton", "state": "VIC", "postcode": "3053"}
BILLING = {"line1": "22 Grote St", "city": "Adelaide", "state": "SA", "postcode": "5000"}
# Distinct from every address above, so it can never pass the equivalence check.
OTHER = {"line1": "4 Salamanca Pl", "city": "Hobart", "state": "TAS", "postcode": "7000"}


def _columns(prefix: str, address: dict[str, str]) -> dict[str, str]:
    """CSV columns for one address, named the way the legacy export names them."""
    return {
        f"{prefix}_address1": address["line1"],
        f"{prefix}_city": address["city"],
        f"{prefix}_state": address["state"],
        f"{prefix}_zip": address["postcode"],
    }


class AssignAddressesTest(django.test.TestCase):
    def setUp(self):
        self.person = Person.objects.create(email="lena.petrova@example.com", legacy_id=4587)

    def test_each_legacy_prefix_maps_to_its_person_field(self):
        row = {
            **_columns("address", HOME),
            **_columns("work", WORK),
            **_columns("mailing", MAILING),
        }
        assign_addresses(self.person, row)

        self.assertEqual(
            self.person.home_address.line1,
            HOME["line1"],
            'The legacy home address is exported under the bare "address" prefix.',
        )
        self.assertEqual(self.person.mailing_address.line1, MAILING["line1"])
        self.assertIsNone(
            self.person.registered_address,
            "Roles absent from the row must stay empty.",
        )
        self.assertIsNone(self.person.billing_address)
        imported_lines = {self.person.home_address.line1, self.person.mailing_address.line1}
        self.assertEqual(
            Address.objects.count(),
            len(imported_lines),
            "Only the mapped roles may create Address records: the legacy work "
            "address is deliberately not imported, since Person carries no "
            "work-address role.",
        )

    def test_duplicate_primary_is_not_placed_again(self):
        # The legacy primary address is a pointer to one of the other
        # addresses, so most rows carry it as an exact duplicate of home.
        row = {**_columns("address", HOME), **_columns("primary", HOME)}
        assign_addresses(self.person, row)

        self.assertEqual(self.person.home_address.line1, HOME["line1"])
        self.assertIsNone(
            self.person.registered_address,
            "A primary that duplicates the home address must not spill into "
            "the next open field.",
        )
        self.assertEqual(
            Address.objects.count(),
            1,
            "The duplicated primary must not create a second Address record.",
        )

    def test_distinct_primary_claims_the_first_open_field(self):
        row = {**_columns("address", HOME), **_columns("primary", OTHER)}
        assign_addresses(self.person, row)

        first_open_field = next(
            field
            for field in PRIMARY_ADDRESS_FALLBACK_FIELDS
            if field != "home_address"  # home was claimed by the "address" prefix
        )
        self.assertEqual(
            getattr(self.person, first_open_field).line1,
            OTHER["line1"],
            "A primary matching no other address must claim the first open "
            "field in the fallback order.",
        )

    def test_distinct_primary_overrides_home_when_every_field_is_taken(self):
        row = {
            **_columns("address", HOME),
            **_columns("registered", REGISTERED),
            **_columns("mailing", MAILING),
            **_columns("billing", BILLING),
            **_columns("primary", OTHER),
        }
        with self.assertLogs(
            "underground_crm.management.commands.import_people_csv", level="WARNING"
        ):
            assign_addresses(self.person, row)

        self.assertEqual(
            self.person.home_address.line1,
            OTHER["line1"],
            "With every fallback field taken, the primary must override the "
            "home address (and say so in the log).",
        )

    def test_unparsed_submitted_address_survives_as_raw_line1(self):
        # The legacy system keeps the raw string the person typed alongside
        # its own parse of it; when that parse failed, only the raw string
        # survives in the export.
        raw_mailing_address = "unit 4, rear of 36 flora street, kirrawee"
        assign_addresses(self.person, {"mailing_submitted_address": raw_mailing_address})

        self.assertIsNotNone(
            self.person.mailing_address,
            "An address the legacy system failed to parse must still survive " "the import.",
        )
        self.assertEqual(
            self.person.mailing_address.line1,
            raw_mailing_address,
            "The raw string is kept verbatim in line1, ready for the "
            "geocoding backlog to repair.",
        )
        self.assertIsNone(
            self.person.mailing_address.latitude,
            "The record must stay unverified so geocode_addresses picks it up.",
        )

    def test_addresses_already_held_are_preserved(self):
        existing_home = Address.objects.create(**HOME)
        self.person.home_address = existing_home
        self.person.save()

        assign_addresses(self.person, {**_columns("address", OTHER)})

        self.assertEqual(
            self.person.home_address_id,
            existing_home.id,
            "Re-importing must never replace an address the person already "
            "holds — the command is idempotent.",
        )
