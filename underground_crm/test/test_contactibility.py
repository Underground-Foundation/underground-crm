import unittest

import dns.resolver
from email_validator import EmailUndeliverableError, EmailSyntaxError

from underground_crm.contactability import (
    get_ambiguous_admin_by_full_name,
    get_full_name_options,
    get_validated_domain_name,
    get_validated_email_address,
    parse_address,
    parse_verified_phone_number,
    validate_domain_name,
    validate_email_with_deliverability,
)


class EmailValidationTest(unittest.TestCase):

    def test_emailvalidator_with_valid_address(self):
        validate_email_with_deliverability("contact@fusionparty.org.au")

    def test_emailvalidator_with_invalid_address(self):
        thrown = False
        try:
            validate_email_with_deliverability("fusionparty.org.au")
        except EmailSyntaxError:
            thrown = True
        self.assertTrue(thrown)

    def test_emailvalidator_with_invalid_domain(self):
        thrown = False
        try:
            validate_email_with_deliverability("contact@fusionparty")
        except EmailSyntaxError as e:
            thrown = True
        self.assertTrue(thrown)

    def test_emailvalidator_with_unregistered_address(self):
        thrown = False
        try:
            validate_email_with_deliverability(
                "hello@sssssssssssssssssssssssssssssssssstteaaaaaaaapddddddddddddddddd.com.au"
            )
        except EmailUndeliverableError as e:
            thrown = True
        self.assertTrue(thrown)

    def test_get_validated_email_address_returns_address_when_valid(self):
        result = get_validated_email_address("contact@fusionparty.org.au")
        self.assertEqual(result, "contact@fusionparty.org.au")

    def test_get_validated_email_address_returns_none_when_invalid(self):
        result = get_validated_email_address("not-an-email")
        self.assertIsNone(result)

    def test_get_validated_email_address_returns_none_for_empty_string(self):
        result = get_validated_email_address("")
        self.assertIsNone(result)


class DomainValidationTest(unittest.TestCase):

    def test_domain_with_valid_value(self):
        validate_domain_name("owen.engineer")

    def test_domain_with_invalid_value(self):
        thrown = False
        try:
            validate_domain_name("ssssssssssssssssssssssssssss.engineer")
        except dns.resolver.NXDOMAIN:
            thrown = True
        self.assertTrue(thrown)

    def test_get_validated_domain_name_returns_none_when_invalid(self):
        result = get_validated_domain_name("ssssssssssssssssssssssssssss.engineer")
        self.assertIsNone(result)

    def test_get_validated_domain_name_returns_none_for_empty_string(self):
        result = get_validated_domain_name("")
        self.assertIsNone(result)


class PhoneNumberTest(unittest.TestCase):

    def test_parse_verified_phone_number_with_valid_au_mobile(self):
        result = parse_verified_phone_number("0412 345 678")
        self.assertIsNotNone(result)
        self.assertEqual(result.country_code, 61)

    def test_parse_verified_phone_number_with_valid_international(self):
        result = parse_verified_phone_number("+61 412 345 678")
        self.assertIsNotNone(result)
        self.assertEqual(result.country_code, 61)

    def test_parse_verified_phone_number_returns_none_for_non_number(self):
        result = parse_verified_phone_number("not a number")
        self.assertIsNone(result)

    def test_parse_verified_phone_number_returns_none_for_empty_string(self):
        result = parse_verified_phone_number("")
        self.assertIsNone(result)


class NamingTest(unittest.TestCase):
    def test_get_full_name_options_with_single_word(self):
        pairs = list(get_full_name_options("Madonna"))
        self.assertEqual(len(pairs), 1)
        self.assertEqual([("Madonna", None)], pairs)

    def test_get_full_name_options_with_two_words(self):
        pairs = list(get_full_name_options("Tom Smith"))
        self.assertEqual([("Tom", "Smith")], pairs)

    def test_get_full_name_options_with_multi_words(self):
        pairs = list(get_full_name_options("Gregory Charles Windsor Mountbatten"))
        self.assertEqual(
            [
                ("Gregory", "Charles Windsor Mountbatten"),
                ("Gregory Charles", "Windsor Mountbatten"),
                ("Gregory Charles Windsor", "Mountbatten"),
            ],
            pairs,
            msg=f"Unexpected pairs: {pairs}",
        )

    def test_get_ambiguous_admin_by_full_name(self):
        user = get_ambiguous_admin_by_full_name("Owen Miller")
        self.assertTrue(user)


class AddressTest(unittest.TestCase):
    def test_parse_address_with_venue_unit_and_suburb(self):
        # "701/5 Ovens Street" means Unit 701, 5 Ovens Street
        result = parse_address("Hanging Gardens of Brunswick, 701/5 Ovens Street, Brunswick 3056")
        self.assertIsNotNone(result)
        self.assertEqual(result.line1, "Hanging Gardens of Brunswick")
        self.assertEqual(result.line2, "Unit 701")
        self.assertEqual(result.line3, "5 Ovens Street")
        self.assertEqual(result.city, "Brunswick")
        self.assertEqual(result.postcode, "3056")

    def test_parse_address_unit_without_venue_name(self):
        result = parse_address("701/5 Ovens Street, Brunswick 3056")
        self.assertIsNotNone(result)
        self.assertEqual(result.line1, "Unit 701")
        self.assertEqual(result.line2, "5 Ovens Street")
        self.assertEqual(result.line3, "")
        self.assertEqual(result.city, "Brunswick")
        self.assertEqual(result.postcode, "3056")

    def test_parse_address_without_unit_number(self):
        # A plain street address with no unit prefix should not be split
        result = parse_address("Melbourne Town Hall, 90-130 Swanston Street, Melbourne VIC 3000")
        self.assertIsNotNone(result)
        self.assertEqual(result.line1, "Melbourne Town Hall")
        self.assertEqual(result.line2, "90-130 Swanston Street")
        self.assertEqual(result.line3, "")
        self.assertEqual(result.city, "Melbourne")
        self.assertEqual(result.state, "VIC")
        self.assertEqual(result.postcode, "3000")

    def test_parse_address_without_postcode(self):
        result = parse_address("Hanging Gardens of Brunswick, 701/5 Ovens Street, Brunswick")
        self.assertIsNotNone(result)
        self.assertEqual(result.city, "Brunswick")
        self.assertEqual(result.postcode, "")

    def test_parse_address_returns_none_for_empty_string(self):
        result = parse_address("")
        self.assertIsNone(result)
