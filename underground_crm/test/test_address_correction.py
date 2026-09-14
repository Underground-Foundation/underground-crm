import django.test

from underground_crm import addressr
from underground_crm.address_correction import (
    MINIMUM_SPELLING_SIMILARITY,
    correct_minor_misspellings,
    spelling_similarity,
)
from underground_crm.models.address import Address

# The detail responses below are real G-NAF records as Addressr returns them
# (with the geocoding section left out), and the matches are what
# addressr._extract_structured_address() builds from them.

# G-NAF's street type table gives the full word as the code and the
# abbreviation as the name, whereas its street suffix table does the reverse.
STREET = addressr.GnafCode(code="STREET", name="ST")
NORTH = addressr.GnafCode(code="N", name="NORTH")

CARNARVON_ST_DETAIL = {
    "pid": "GAVIC419796003",
    "sla": "19 CARNARVON ST, BRUNSWICK VIC 3056",
    "mla": ["19 CARNARVON ST", "BRUNSWICK VIC 3056"],
    "structured": {
        "number": {"number": 19},
        "street": {
            "name": "CARNARVON",
            "type": {"code": "STREET", "name": "ST"},
            "class": {"code": "C", "name": "CONFIRMED"},
        },
        "confidence": 2,
        "locality": {"name": "BRUNSWICK", "class": {"code": "G", "name": "GAZETTED LOCALITY"}},
        "postcode": "3056",
        "state": {"name": "VICTORIA", "abbreviation": "VIC"},
    },
}
CARNARVON_ST_MATCH = addressr.StructuredAddress(
    line1="19 CARNARVON ST",
    city="BRUNSWICK",
    state="VIC",
    postcode="3056",
    street_number="19",
    street_name="CARNARVON",
    street_type=STREET,
)

# A street with a suffix, and a building name printed before the street line.
PETER_ST_NORTH_DETAIL = {
    "sla": "PETER STREET NORTH PARK, 16 PETER ST NORTH, EVERTON HILLS QLD 4053",
    "mla": ["PETER STREET NORTH PARK", "16 PETER ST NORTH", "EVERTON HILLS QLD 4053"],
    "structured": {
        "number": {"number": 16},
        "street": {
            "name": "PETER",
            "type": {"code": "STREET", "name": "ST"},
            "suffix": {"code": "N", "name": "NORTH"},
        },
        "locality": {"name": "EVERTON HILLS"},
        "postcode": "4053",
        "state": {"name": "QUEENSLAND", "abbreviation": "QLD"},
    },
}
PETER_ST_NORTH_MATCH = addressr.StructuredAddress(
    line1="16 PETER ST NORTH",
    city="EVERTON HILLS",
    state="QLD",
    postcode="4053",
    street_number="16",
    street_name="PETER",
    street_type=STREET,
    street_suffix=NORTH,
)

# A street number that is a range.
COLLINS_ST_RANGE_DETAIL = {
    "mla": ["COLLINS STREET TOWER", "480-490 COLLINS ST", "MELBOURNE VIC 3000"],
    "structured": {
        "number": {"number": 480, "last": {"number": 490}},
        "street": {"name": "COLLINS", "type": {"code": "STREET", "name": "ST"}},
        "locality": {"name": "MELBOURNE"},
        "postcode": "3000",
        "state": {"name": "VICTORIA", "abbreviation": "VIC"},
    },
}
COLLINS_ST_RANGE_MATCH = addressr.StructuredAddress(
    line1="480-490 COLLINS ST",
    city="MELBOURNE",
    state="VIC",
    postcode="3000",
    street_number="480-490",
    street_name="COLLINS",
    street_type=STREET,
)


class StructuredAddressExtractionTest(django.test.SimpleTestCase):
    def test_street_parts_are_read_from_the_structured_breakdown(self):
        self.assertEqual(
            addressr._extract_structured_address(CARNARVON_ST_DETAIL), CARNARVON_ST_MATCH
        )

    def test_street_suffix_is_read_alongside_the_street_type(self):
        self.assertEqual(
            addressr._extract_structured_address(PETER_ST_NORTH_DETAIL), PETER_ST_NORTH_MATCH
        )

    def test_street_number_range_is_printed_as_g_naf_prints_it(self):
        self.assertEqual(
            addressr._extract_structured_address(COLLINS_ST_RANGE_DETAIL), COLLINS_ST_RANGE_MATCH
        )


class CorrectMinorMisspellingsTest(django.test.SimpleTestCase):
    def test_lower_case_street_and_suburb_are_capitalized(self):
        address = Address(line1="19 carnarvon St", city="brunswick", state="Vic", postcode="3056")

        changed = correct_minor_misspellings(address, CARNARVON_ST_MATCH)

        self.assertCountEqual(changed, ["line1", "city"])
        self.assertEqual(address.line1, "19 Carnarvon St")
        self.assertEqual(address.city, "Brunswick")
        self.assertEqual(
            address.state,
            "Vic",
            "Only the street name and suburb are corrected; the state is left as submitted.",
        )

    def test_misspelled_street_name_and_suburb_take_addressr_spelling(self):
        submitted_street_name = "Carnavon"
        self.assertGreaterEqual(
            spelling_similarity(submitted_street_name, CARNARVON_ST_MATCH.street_name),
            MINIMUM_SPELLING_SIMILARITY,
            "Precondition: a single dropped letter must count as a minor misspelling.",
        )
        address = Address(
            line1=f"19 {submitted_street_name} Street",
            city="Brunswik",
            state="VIC",
            postcode="3056",
        )

        changed = correct_minor_misspellings(address, CARNARVON_ST_MATCH)

        self.assertCountEqual(changed, ["line1", "city"])
        self.assertEqual(
            address.line1,
            "19 Carnarvon Street",
            "The street name takes Addressr's spelling, while 'Street' is kept as "
            "submitted, being the code of the street type whose name is 'ST'.",
        )
        self.assertEqual(address.city, "Brunswick")

    def test_street_suffix_in_either_form_is_kept_as_submitted(self):
        address = Address(line1="16 petre Street N", city="everton hills", postcode="4053")

        changed = correct_minor_misspellings(address, PETER_ST_NORTH_MATCH)

        self.assertCountEqual(changed, ["line1", "city"])
        self.assertEqual(
            address.line1,
            "16 Peter Street N",
            "'N' is the code of the suffix whose name is 'NORTH', so it is kept "
            "as submitted while the street name is corrected.",
        )
        self.assertEqual(address.city, "Everton Hills")

    def test_missing_street_suffix_blocks_every_correction(self):
        address = Address(line1="16 petre St", city="everton hills", postcode="4053")

        changed = correct_minor_misspellings(address, PETER_ST_NORTH_MATCH)

        self.assertEqual(
            changed,
            [],
            "Peter St and Peter St North can be different streets, so a match on "
            "the one cannot correct the other.",
        )
        self.assertEqual(address.line1, "16 petre St")

    def test_different_street_type_blocks_every_correction(self):
        address = Address(line1="19 carnavon Rd", city="brunswick", postcode="3056")

        changed = correct_minor_misspellings(address, CARNARVON_ST_MATCH)

        self.assertEqual(
            changed,
            [],
            "Carnarvon Rd and Carnarvon St can be different streets in the same "
            "suburb, so a match on the one cannot correct the other.",
        )
        self.assertEqual(address.line1, "19 carnavon Rd")
        self.assertEqual(address.city, "brunswick")

    def test_unit_prefix_and_number_are_kept_as_submitted(self):
        address = Address(line1="Unit 2, 19 carnavon St", city="Brunswick", postcode="3056")

        changed = correct_minor_misspellings(address, CARNARVON_ST_MATCH)

        self.assertEqual(changed, ["line1"])
        self.assertEqual(
            address.line1,
            "Unit 2, 19 Carnarvon St",
            "Only the street name may change; the unit prefix, which Addressr's "
            "street line omits, must survive.",
        )

    def test_street_number_range_agrees_with_its_match(self):
        address = Address(line1="480-490 Colins St", city="Melbourne", postcode="3000")

        changed = correct_minor_misspellings(address, COLLINS_ST_RANGE_MATCH)

        self.assertEqual(changed, ["line1"])
        self.assertEqual(address.line1, "480-490 Collins St")

    def test_different_street_number_blocks_every_correction(self):
        address = Address(line1="19A carnavon St", city="brunswick", postcode="3056")

        changed = correct_minor_misspellings(address, CARNARVON_ST_MATCH)

        self.assertEqual(
            changed,
            [],
            "A match with a different street number may be a neighboring "
            "property, so none of its spelling can be trusted.",
        )
        self.assertEqual(address.line1, "19A carnavon St")
        self.assertEqual(address.city, "brunswick")

    def test_dissimilar_street_name_blocks_every_correction(self):
        submitted_street_name = "Albion"
        self.assertLess(
            spelling_similarity(submitted_street_name, CARNARVON_ST_MATCH.street_name),
            MINIMUM_SPELLING_SIMILARITY,
            "Precondition: 'Albion' must be too unlike 'Carnarvon' to be a misspelling of it.",
        )
        address = Address(line1=f"19 {submitted_street_name} St", city="brunswick", postcode="3056")

        changed = correct_minor_misspellings(address, CARNARVON_ST_MATCH)

        self.assertEqual(changed, [])
        self.assertEqual(address.line1, f"19 {submitted_street_name} St")
        self.assertEqual(
            address.city,
            "brunswick",
            "A match on a different street must not correct even the suburb, "
            "which would otherwise be a valid capitalization fix.",
        )

    def test_different_postcode_blocks_every_correction(self):
        brunswick_east_postcode = "3057"
        address = Address(
            line1="19 carnavon St", city="brunswick", postcode=brunswick_east_postcode
        )

        changed = correct_minor_misspellings(address, CARNARVON_ST_MATCH)

        self.assertEqual(changed, [])
        self.assertEqual(address.line1, "19 carnavon St")

    def test_suburb_with_different_word_count_blocks_every_correction(self):
        address = Address(line1="19 carnavon St", city="Brunswick East", postcode="3056")

        changed = correct_minor_misspellings(address, CARNARVON_ST_MATCH)

        self.assertEqual(
            changed,
            [],
            "'Brunswick East' is a different suburb from 'Brunswick', not a misspelling of it.",
        )
        self.assertEqual(address.city, "Brunswick East")

    def test_deliberate_capitalization_is_kept(self):
        address = Address(line1="12 McDonald St", city="Brunswick", postcode="3056")
        matched = CARNARVON_ST_MATCH._replace(
            line1="12 MCDONALD ST", street_number="12", street_name="MCDONALD"
        )

        changed = correct_minor_misspellings(address, matched)

        self.assertEqual(
            changed,
            [],
            "A name that agrees with Addressr apart from capitalization, and was "
            "not submitted in lower case, is already right and must not be "
            "flattened into 'Mcdonald'.",
        )
        self.assertEqual(address.line1, "12 McDonald St")

    def test_address_submitted_in_capitals_is_corrected_in_capitals(self):
        address = Address(line1="19 CARNAVON ST", city="BRUNSWIK", postcode="3056")

        correct_minor_misspellings(address, CARNARVON_ST_MATCH)

        self.assertEqual(address.line1, "19 CARNARVON ST")
        self.assertEqual(address.city, "BRUNSWICK")
