import django.test
from django.test import override_settings

from underground_crm import addressr
from underground_crm.models.address import Address

# A real G-NAF address, so the live test below can resolve it through a seeded
# Addressr instance.
ONE_LINE_ADDRESS = "1 COOK RD, LINDFIELD NSW 2070"


# A closed port stands in for an absent Addressr service — no mock needed, the
# client's own connection failure handling is exercised.
@override_settings(ADDRESSR_BASE_URL="http://127.0.0.1:1")
class AddressFromOneLineFallbackTest(django.test.SimpleTestCase):
    def test_unresolvable_address_is_kept_raw_in_line1(self):
        address = Address.from_one_line(ONE_LINE_ADDRESS)
        self.assertEqual(
            address.line1,
            ONE_LINE_ADDRESS,
            "When Addressr cannot resolve the string, the raw submission must "
            "be preserved verbatim so nothing the visitor typed is lost.",
        )
        self.assertIsNone(
            address.latitude,
            "An unresolved address must stay unverified (no geocode), leaving "
            "it for the geocode_addresses command to repair.",
        )


class AddressFromOneLineLiveTest(django.test.SimpleTestCase):
    """Resolution against the real Addressr service, when it is running."""

    def setUp(self):
        if not addressr.search(ONE_LINE_ADDRESS):
            self.skipTest("Addressr is unavailable or unseeded at ADDRESSR_BASE_URL.")

    def test_one_line_address_resolves_to_structured_record(self):
        address = Address.from_one_line(ONE_LINE_ADDRESS)
        self.assertEqual(address.city, "LINDFIELD")
        self.assertEqual(address.state, "NSW")
        self.assertEqual(address.postcode, "2070")
        self.assertIsNotNone(
            address.latitude,
            "A resolved address arrives already geocoded, so it never joins "
            "the geocode_addresses backlog.",
        )
