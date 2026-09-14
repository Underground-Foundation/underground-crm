"""
Integration test for address geocoding.

These test call the real Addressr API and require the service to be running
and seeded with GNAF data. They are skipped automatically when Addressr is not
reachable, e.g. in CI without the Docker stack.

Start the stack with: ./start_containers.sh
Load GNAF data (first run only): docker compose exec addressr npx @mountain-pass/addressr-load
"""

import logging
import unittest
import urllib.error
import urllib.request
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import TestCase

from underground_crm import addressr as addressr_client
from underground_crm.models.address import Address
from underground_crm.tasks import geocode_address

logger = logging.getLogger(__name__)

# 19 Carnarvon Street, Brunswick, as typed carelessly into a form, and as
# G-NAF spells it. The G-NAF ID is the real one, but the coordinates of the
# canned match are illustrative values, not looked up from G-NAF.
CARNARVON_ST_TYPED = {
    "line1": "19 carnarvon St",
    "city": "brunswick",
    "state": "Vic",
    "postcode": "3056",
}
CARNARVON_ST_CORRECTED_LINE1 = "19 Carnarvon St"
CARNARVON_ST_CORRECTED_CITY = "Brunswick"
CARNARVON_ST_GEOCODE = addressr_client.Geocode(
    latitude=Decimal("-37.769420"),
    longitude=Decimal("144.958710"),
    reliability=2,
    confidence=0,
    address=addressr_client.StructuredAddress(
        line1="19 CARNARVON ST",
        city="BRUNSWICK",
        state="VIC",
        postcode="3056",
        street_number="19",
        street_name="CARNARVON",
        street_type=addressr_client.GnafCode(code="STREET", name="ST"),
    ),
    gnaf_id="GAVIC419796003",
    sla="19 CARNARVON ST, BRUNSWICK VIC 3056",
)


def _unqueued_address(**components: str) -> Address:
    """Save an Address without the post_save signal queuing a geocode, so
    each test runs the task itself."""
    address = Address(**components)
    address._skip_geocoding = True
    address.save()
    return address


def _addressr_reachable() -> bool:
    url = settings.ADDRESSR_BASE_URL.rstrip("/") + "/"
    logger.info("Checking if Addressr is accessible at %s", url)
    try:
        urllib.request.urlopen(url, timeout=2)
    except (urllib.error.URLError, OSError):
        logger.info("Addressr is not accessible at %s", url)
        return False
    # A 200 root response only means the process is up; GNAF data may not be loaded yet.
    # Verify by running a known search — if it returns nothing, skip the tests.
    results = addressr_client.search("1 Cook Road Lindfield NSW")
    if not results:
        logger.info("Addressr is running but GNAF data is not loaded — skipping geocoding tests")
        return False
    logger.info("Addressr is available and returning results")
    return True


ADDRESSR_AVAILABLE = _addressr_reachable()
skip_without_addressr = unittest.skipUnless(ADDRESSR_AVAILABLE, "Addressr service is not running")


@skip_without_addressr
class GeocodingClientTest(unittest.TestCase):
    """Tests against the Addressr search and detail endpoints directly."""

    def test_valid_australian_unit_address_geocodes(self):
        sample_address = Address(
            line1="1 Cook Road", city="Lindfield", state="NSW", postcode="2070", country_code="AU"
        )
        logger.info("Geocoding %s", sample_address)
        result = addressr_client.geocode(str(sample_address))
        self.assertIsNotNone(result, "Expected a geocode result for a known Australian address.")
        self.assertAlmostEqual(float(result.latitude), -33.78, delta=0.5)
        self.assertAlmostEqual(float(result.longitude), 151.16, delta=0.5)
        # Reliability 1–2 means the geocode is on or at the address parcel.
        self.assertLessEqual(
            result.reliability,
            3,
            msg=f"Expected reliability ≤ 3 for a known address, got {result.reliability}.",
        )
        print(f"Geocoded {sample_address} as {result}")

    def test_fictitious_overseas_address_returns_none(self):
        # GNAF covers Australia only; a made-up US address should produce no match.
        result = addressr_client.geocode("123 Maryham Boulevard East Prussia 12345 USA")
        self.assertIsNone(result, "Expected None for a fictitious non-Australian address.")

    def test_search_returns_suggestions_for_partial_address(self):
        suggestions = addressr_client.search("5 Ovens Street Brunswick")
        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0, "Expected at least one suggestion.")


@skip_without_addressr
class GeocodingTaskTest(TestCase):

    def test_task_handles_nonexistent_pk_gracefully(self):
        # Should log a warning and return without raising.
        geocode_address("00000000-0000-0000-0000-000000000000")

    def test_live_task_corrects_capitalization_of_street_and_suburb(self):
        address = _unqueued_address(**CARNARVON_ST_TYPED)
        if addressr_client.geocode(str(address)) is None:
            self.skipTest("The seeded G-NAF data does not contain 19 Carnarvon St, Brunswick.")

        geocode_address(str(address.pk))
        address.refresh_from_db()

        self.assertIsNotNone(address.latitude)
        self.assertEqual(address.line1, CARNARVON_ST_CORRECTED_LINE1)
        self.assertEqual(address.city, CARNARVON_ST_CORRECTED_CITY)


class GeocodingTaskCorrectionTest(TestCase):
    """The task's handling of an Addressr match, with Addressr (an external
    service) substituted by a canned result; the live counterpart above runs
    against the real container when it is up."""

    def test_task_stores_corrected_street_and_suburb(self):
        address = _unqueued_address(**CARNARVON_ST_TYPED)

        with mock.patch.object(addressr_client, "geocode", return_value=CARNARVON_ST_GEOCODE):
            geocode_address(str(address.pk))
        address.refresh_from_db()

        self.assertEqual(address.latitude, CARNARVON_ST_GEOCODE.latitude)
        self.assertEqual(address.gnaf_id, CARNARVON_ST_GEOCODE.gnaf_id)
        self.assertEqual(address.line1, CARNARVON_ST_CORRECTED_LINE1)
        self.assertEqual(address.city, CARNARVON_ST_CORRECTED_CITY)
        self.assertEqual(
            address.postcode,
            CARNARVON_ST_TYPED["postcode"],
            "Fields outside the street and suburb are never rewritten.",
        )

    def test_task_does_not_overwrite_an_edit_saved_during_the_lookup(self):
        address = _unqueued_address(**CARNARVON_ST_TYPED)
        edited_line1 = "21 Carnarvon St"

        def edit_then_answer(query: str) -> addressr_client.Geocode:
            # Someone saves a different street number while Addressr is
            # still answering the query for the old one.
            Address.objects.filter(pk=address.pk).update(line1=edited_line1)
            return CARNARVON_ST_GEOCODE

        with mock.patch.object(addressr_client, "geocode", side_effect=edit_then_answer):
            geocode_address(str(address.pk))
        address.refresh_from_db()

        self.assertEqual(
            address.line1,
            edited_line1,
            "A correction computed from the old text must not clobber a newer edit.",
        )
        self.assertIsNone(
            address.latitude,
            "The old text's coordinates must not be attached to the edited address.",
        )
