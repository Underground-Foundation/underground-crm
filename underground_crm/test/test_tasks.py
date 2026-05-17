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

from django.conf import settings
from django.test import TestCase

from underground_crm import addressr as addressr_client
from underground_crm.models.address import Address
from underground_crm.tasks import geocode_address

logger = logging.getLogger(__name__)


def _addressr_reachable() -> bool:
    url = settings.ADDRESSR_BASE_URL.rstrip("/") + "/"
    logger.info("Checking if Addressr is accessible at %s", url)
    try:
        urllib.request.urlopen(url, timeout=2)
        logger.info("Addressr is indeed available")
        return True
    except (urllib.error.URLError, OSError):
        return False


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
