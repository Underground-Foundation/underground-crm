from decimal import Decimal
from unittest import mock

import django.test
from django.test import override_settings

from underground_crm import addressr
from underground_crm.models.address import Address

# A real G-NAF address — its component values, single-line form, and Address
# Detail PID (matching the example response documented on addressr.search()) —
# so the live tests below can resolve it through a seeded Addressr instance.
COOK_RD = {
    "line1": "1 COOK RD",
    "city": "LINDFIELD",
    "state": "NSW",
    "postcode": "2070",
}
COOK_RD_SLA = "1 COOK RD, LINDFIELD NSW 2070"
COOK_RD_GNAF_ID = "GANSW705239062"

# A second real G-NAF address, for exercising what happens when a G-NAF ID
# and the submitted components disagree.
DOWNING_ST = {
    "line1": "UNIT 1, 10 DOWNING ST",
    "city": "BLACKBURN",
    "state": "VIC",
    "postcode": "3130",
}
DOWNING_ST_SLA = "UNIT 1, 10 DOWNING ST, BLACKBURN VIC 3130"
DOWNING_ST_GNAF_ID = "GAVIC423505693"


def _canned_match(components: dict, sla: str, gnaf_id: str) -> addressr.Geocode:
    """An addressr.geocode_by_id() result in its documented shape, for tests
    that must not depend on the external Addressr service."""
    return addressr.Geocode(
        latitude=Decimal("-33.775939"),
        longitude=Decimal("151.160319"),
        reliability=2,
        confidence=0,
        address=addressr.StructuredAddress(
            line1=components["line1"],
            city=components["city"],
            state=components["state"],
            postcode=components["postcode"],
        ),
        gnaf_id=gnaf_id,
        sla=sla,
    )


class AddressFromComponentsTest(django.test.SimpleTestCase):
    def test_manual_entry_is_stored_as_typed_and_unverified(self):
        # No G-NAF ID means the visitor typed the address in by hand (or
        # edited a picked suggestion); nothing is looked up synchronously.
        address = Address.from_components(**COOK_RD)
        self.assertEqual(address.line1, COOK_RD["line1"])
        self.assertEqual(address.city, COOK_RD["city"])
        self.assertEqual(address.state, COOK_RD["state"])
        self.assertEqual(address.postcode, COOK_RD["postcode"])
        self.assertIsNone(
            address.latitude,
            "A manual entry must stay unverified (no geocode): the background "
            "geocoding flow verifies it after saving.",
        )
        self.assertIsNone(
            address.gnaf_id,
            "No suggestion was picked, so no address identity may be claimed.",
        )

    def test_verified_gnaf_id_brings_the_geocode_along(self):
        # Addressr is an external service, so this test substitutes a canned
        # lookup result; the live counterpart below exercises the real
        # container when it is up.
        with mock.patch.object(
            addressr,
            "geocode_by_id",
            return_value=_canned_match(COOK_RD, COOK_RD_SLA, COOK_RD_GNAF_ID),
        ):
            address = Address.from_components(**COOK_RD, gnaf_id=COOK_RD_GNAF_ID)
        self.assertEqual(
            address.gnaf_id,
            COOK_RD_GNAF_ID,
            "An ID that agrees with the submitted components must be recorded, "
            "identifying the exact address the visitor picked.",
        )
        self.assertIsNotNone(
            address.latitude,
            "A verified address arrives already geocoded, so it never joins "
            "the geocode_addresses backlog.",
        )
        self.assertEqual(
            address.line1,
            COOK_RD["line1"],
            "The submitted component text is kept even on a verified record — "
            "it can carry unit/flat detail that Addressr's structured "
            "breakdown omits.",
        )

    def test_extra_lines_keep_the_geocode_but_not_the_id(self):
        # Lines 2 and 3 hold supplementary delivery detail (a unit, a "c/-"
        # line) that does not move the property, so they are excluded from
        # the verification: the picked suggestion's coordinates survive them.
        line2 = "Unit 2"
        line3 = "c/- P. Sharma"
        with mock.patch.object(
            addressr,
            "geocode_by_id",
            return_value=_canned_match(COOK_RD, COOK_RD_SLA, COOK_RD_GNAF_ID),
        ):
            address = Address.from_components(
                **COOK_RD, line2=line2, line3=line3, gnaf_id=COOK_RD_GNAF_ID
            )
        self.assertEqual(address.line2, line2)
        self.assertEqual(address.line3, line3)
        self.assertIsNotNone(
            address.latitude,
            "Extra delivery lines must not cost the record the coordinates "
            "of the address the visitor picked.",
        )
        self.assertIsNone(
            address.gnaf_id,
            "With extra lines the record no longer names exactly the address "
            "the ID identifies, so storing the ID would over-claim.",
        )

    def test_stale_gnaf_id_is_ignored_when_components_disagree(self):
        # A stale ID can accompany edited components when the page's script
        # never ran (the hidden input keeps its pre-filled value while the
        # visitor types a new address); honoring it would silently resurrect
        # the old address.
        with mock.patch.object(
            addressr,
            "geocode_by_id",
            return_value=_canned_match(COOK_RD, COOK_RD_SLA, COOK_RD_GNAF_ID),
        ):
            address = Address.from_components(**DOWNING_ST, gnaf_id=COOK_RD_GNAF_ID)
        self.assertEqual(
            address.line1,
            DOWNING_ST["line1"],
            "The components the visitor actually submitted must win over the "
            "address the stale ID points at.",
        )
        self.assertIsNone(
            address.gnaf_id,
            "An ID that no longer describes the submitted components must be "
            "dropped, leaving the record unverified for the background "
            "geocoding flow.",
        )


# A closed port stands in for an absent Addressr service — no mock needed, the
# client's own connection failure handling is exercised.
@override_settings(ADDRESSR_BASE_URL="http://127.0.0.1:1")
class AddressFromComponentsFallbackTest(django.test.SimpleTestCase):
    def test_unverifiable_gnaf_id_is_not_stored(self):
        address = Address.from_components(**COOK_RD, gnaf_id=COOK_RD_GNAF_ID)
        self.assertEqual(
            address.line1,
            COOK_RD["line1"],
            "With Addressr down, the submitted components must still be "
            "preserved verbatim so nothing the visitor typed is lost.",
        )
        self.assertIsNone(
            address.gnaf_id,
            "A G-NAF ID that could not be verified against Addressr must not "
            "be recorded as though it had been — the record stays unverified "
            "for the geocode_addresses command to repair.",
        )


class AddressEquivalenceTest(django.test.SimpleTestCase):
    def test_matching_gnaf_ids_are_equivalent_despite_differing_text(self):
        stored = Address(line1="1 COOK RD", city="LINDFIELD", gnaf_id=COOK_RD_GNAF_ID)
        resubmitted = Address(line1="1 Cook Road", city="Lindfield", gnaf_id=COOK_RD_GNAF_ID)
        self.assertTrue(
            stored.is_equivalent(resubmitted),
            "Two records carrying the same G-NAF ID describe the same physical "
            "address by definition; text differences are just formatting.",
        )

    def test_differing_gnaf_ids_are_not_equivalent(self):
        stored = Address(line1="1 COOK RD", city="LINDFIELD", gnaf_id=COOK_RD_GNAF_ID)
        other = Address(line1="1 COOK RD", city="LINDFIELD", gnaf_id=DOWNING_ST_GNAF_ID)
        self.assertFalse(
            stored.is_equivalent(other),
            "When both records carry a G-NAF ID, the IDs alone decide: "
            "identical text with different IDs means two different addresses.",
        )


class AddressFromComponentsLiveTest(django.test.SimpleTestCase):
    """Resolution against the real Addressr service, when it is running."""

    def setUp(self):
        if not addressr.search(COOK_RD_SLA):
            self.skipTest("Addressr is unavailable or unseeded at ADDRESSR_BASE_URL.")

    def test_live_gnaf_id_verifies_the_submitted_components(self):
        address = Address.from_components(**COOK_RD, gnaf_id=COOK_RD_GNAF_ID)
        self.assertEqual(address.gnaf_id, COOK_RD_GNAF_ID)
        self.assertIsNotNone(
            address.latitude,
            "A verified address arrives already geocoded, so it never joins "
            "the geocode_addresses backlog.",
        )

    def test_live_extra_lines_keep_the_geocode_but_not_the_id(self):
        address = Address.from_components(**COOK_RD, line2="c/- P. Sharma", gnaf_id=COOK_RD_GNAF_ID)
        self.assertIsNotNone(
            address.latitude,
            "A delivery line must not cost the record the coordinates of the "
            "address the visitor picked.",
        )
        self.assertIsNone(
            address.gnaf_id,
            "With a delivery line the record no longer names exactly the "
            "address the ID identifies, so the ID must not be stored.",
        )

    def test_live_stale_gnaf_id_is_ignored(self):
        if not addressr.geocode_by_id(DOWNING_ST_GNAF_ID):
            self.skipTest(f"The seeded G-NAF data does not contain {DOWNING_ST_GNAF_ID}.")
        address = Address.from_components(**COOK_RD, gnaf_id=DOWNING_ST_GNAF_ID)
        self.assertIsNone(
            address.gnaf_id,
            "An ID describing a different address than the submitted "
            "components must be dropped rather than stored.",
        )
        self.assertEqual(
            address.line1,
            COOK_RD["line1"],
            "The components the visitor actually submitted must be kept.",
        )
