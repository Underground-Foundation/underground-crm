"""
Integration test for the geocode_addresses management command.

Like test_tasks.py, this calls the real Addressr API and requires the service
to be running and seeded with GNAF data. It is skipped automatically when
Addressr is not reachable, e.g. in CI without the Docker stack.

Start the stack with: ./start_containers.sh
Load GNAF data (first run only): docker compose exec addressr npx @mountain-pass/addressr-load
"""

import logging
import unittest
import urllib.error
import urllib.request

import django.test
from django.conf import settings
from django.core.management import call_command

from underground_crm import addressr as addressr_client
from underground_crm.management.commands.geocode_addresses import _apply_corrections, _street_number
from underground_crm.models.address import Address

logger = logging.getLogger(__name__)

# A real, well-known address (also used in test_tasks.py) with the street and
# suburb misspelled the way a hand-typed legacy CRM entry often is, but with
# an already-correct state and postcode. Used to verify that
# --correct-address-fields fixes the free-text fields to match Addressr's
# canonical spelling without touching fields that were already right.
KNOWN_ADDRESS_LINE1 = "1 Cook Rd"
KNOWN_ADDRESS_LINE1_MISSPELLED = "1 Cok Rd"
KNOWN_ADDRESS_CITY = "Lindfield"
KNOWN_ADDRESS_CITY_MISSPELLED = "Lindfeld"
KNOWN_ADDRESS_STATE = "NSW"
KNOWN_ADDRESS_POSTCODE = "2070"
EXPECTED_CANONICAL_LINE1 = "1 COOK RD"
EXPECTED_CANONICAL_CITY = "LINDFIELD"


def _addressr_reachable() -> bool:
    url = settings.ADDRESSR_BASE_URL.rstrip("/") + "/"
    try:
        urllib.request.urlopen(url, timeout=2)
    except (urllib.error.URLError, OSError):
        return False
    # Confirm GNAF data is actually loaded, not just that the process is up.
    return bool(addressr_client.search(f"{KNOWN_ADDRESS_LINE1} {KNOWN_ADDRESS_CITY}"))


ADDRESSR_AVAILABLE = _addressr_reachable()
skip_without_addressr = unittest.skipUnless(ADDRESSR_AVAILABLE, "Addressr service is not running")


class StreetNumberSafeguardTest(django.test.SimpleTestCase):
    """
    _apply_corrections must never let a corrected line1 change the street
    number the user actually entered — only the street name/type spelling
    around it. These don't need a live Addressr match: a hand-built
    StructuredAddress is enough to exercise the guard.
    """

    def test_matching_street_number_allows_line1_correction(self):
        address = Address(line1="1 Cok Rd", city="Lindfeld", state="NSW", postcode="2070")
        matched = addressr_client.StructuredAddress(
            line1="1 COOK RD", city="LINDFIELD", state="NSW", postcode="2070"
        )

        changed = _apply_corrections(address, matched)

        self.assertIn(
            "line1",
            changed,
            msg="A match with the same leading street number should be allowed to fix the spelling.",
        )
        self.assertEqual(address.line1, "1 COOK RD")

    def test_different_street_number_blocks_line1_correction(self):
        address = Address(line1="1 Cok Rd", city="Lindfeld", state="NSW", postcode="2070")
        matched = addressr_client.StructuredAddress(
            line1="1A COOK RD", city="LINDFIELD", state="NSW", postcode="2070"
        )

        changed = _apply_corrections(address, matched)

        self.assertNotIn(
            "line1",
            changed,
            msg="A match with a different street number must never overwrite the number the user entered.",
        )
        self.assertEqual(
            address.line1,
            "1 Cok Rd",
            msg="line1 must be left exactly as imported when the matched street number differs.",
        )
        # Fields unrelated to the number mismatch should still be corrected.
        self.assertIn(
            "city", changed, msg="city has no street number, so it should still be corrected."
        )
        self.assertEqual(address.city, "LINDFIELD")

    def test_street_number_extraction(self):
        self.assertEqual(_street_number("1 Cook Rd"), "1")
        self.assertEqual(_street_number("12A Smith St"), "12A")
        self.assertIsNone(
            _street_number("Cook Rd"),
            msg="A line1 with no leading number should yield no street number token.",
        )
        self.assertIsNone(_street_number(None))


class ApplyCorrectionsTest(django.test.SimpleTestCase):
    """General behaviour of _apply_corrections, independent of the street-number guard."""

    def test_corrects_all_fields_when_all_differ_and_number_matches(self):
        address = Address(line1="1 Cok Rd", city="Lindfeld", state="Victoria", postcode="20700")
        matched = addressr_client.StructuredAddress(
            line1="1 COOK RD", city="LINDFIELD", state="NSW", postcode="2070"
        )

        changed = _apply_corrections(address, matched)

        self.assertCountEqual(
            changed,
            ["line1", "city", "state", "postcode"],
            msg="Every field that differs from the Addressr match should be reported as changed.",
        )
        self.assertEqual(address.line1, "1 COOK RD")
        self.assertEqual(address.city, "LINDFIELD")
        self.assertEqual(address.state, "NSW")
        self.assertEqual(address.postcode, "2070")

    def test_no_changes_when_address_already_matches(self):
        address = Address(line1="1 COOK RD", city="LINDFIELD", state="NSW", postcode="2070")
        matched = addressr_client.StructuredAddress(
            line1="1 COOK RD", city="LINDFIELD", state="NSW", postcode="2070"
        )

        changed = _apply_corrections(address, matched)

        self.assertEqual(
            changed,
            [],
            msg="An address that already matches Addressr exactly should report no changes.",
        )

    def test_blank_matched_fields_do_not_overwrite_existing_values(self):
        address = Address(line1="1 Cok Rd", city="Lindfeld", state="NSW", postcode="2070")
        # Addressr didn't return a city or postcode for this match (e.g. a
        # sparsely-populated rural record) — the existing values must survive.
        matched = addressr_client.StructuredAddress(
            line1="1 COOK RD", city=None, state="NSW", postcode=None
        )

        changed = _apply_corrections(address, matched)

        self.assertCountEqual(changed, ["line1"])
        self.assertEqual(
            address.city,
            "Lindfeld",
            msg="A blank matched city must not overwrite the address's existing city.",
        )
        self.assertEqual(
            address.postcode,
            "2070",
            msg="A blank matched postcode must not overwrite the address's existing postcode.",
        )


@skip_without_addressr
class GeocodeAddressesCommandTest(django.test.TestCase):

    def _make_mangled_address(self) -> Address:
        return Address.objects.create(
            line1=KNOWN_ADDRESS_LINE1_MISSPELLED,
            city=KNOWN_ADDRESS_CITY_MISSPELLED,
            state=KNOWN_ADDRESS_STATE,
            postcode=KNOWN_ADDRESS_POSTCODE,
            country_code="AU",
        )

    def test_correct_address_fields_fixes_mangled_street_and_suburb(self):
        address = self._make_mangled_address()

        call_command("geocode_addresses", correct_address_fields=True)
        address.refresh_from_db()

        self.assertEqual(
            address.line1,
            EXPECTED_CANONICAL_LINE1,
            msg=(
                f"Expected --correct-address-fields to replace the misspelled "
                f"line1 {KNOWN_ADDRESS_LINE1_MISSPELLED!r} with Addressr's "
                f"canonical {EXPECTED_CANONICAL_LINE1!r}."
            ),
        )
        self.assertEqual(
            address.city,
            EXPECTED_CANONICAL_CITY,
            msg=(
                f"Expected --correct-address-fields to replace the misspelled "
                f"city {KNOWN_ADDRESS_CITY_MISSPELLED!r} with Addressr's "
                f"canonical {EXPECTED_CANONICAL_CITY!r}."
            ),
        )
        # The state and postcode were already correct, so they must be
        # preserved exactly rather than merely equal-by-coincidence.
        self.assertEqual(address.state, KNOWN_ADDRESS_STATE)
        self.assertEqual(address.postcode, KNOWN_ADDRESS_POSTCODE)
        self.assertIsNotNone(
            address.latitude,
            msg="Geocoding should still populate latitude regardless of the new flag.",
        )

    def test_without_flag_geocodes_but_leaves_address_fields_unchanged(self):
        address = self._make_mangled_address()

        call_command("geocode_addresses")
        address.refresh_from_db()

        self.assertEqual(
            address.line1,
            KNOWN_ADDRESS_LINE1_MISSPELLED,
            msg="Without --correct-address-fields, the misspelled line1 must be left as imported.",
        )
        self.assertEqual(
            address.city,
            KNOWN_ADDRESS_CITY_MISSPELLED,
            msg="Without --correct-address-fields, the misspelled city must be left as imported.",
        )
        self.assertIsNotNone(
            address.latitude,
            msg="Geocoding should populate latitude even without the correction flag.",
        )
