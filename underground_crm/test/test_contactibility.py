import unittest

import dns.resolver
from email_validator import EmailUndeliverableError, EmailSyntaxError

from underground_crm.contactability import (
    get_ambiguous_admin_by_full_name,
    get_full_name_options,
    get_user_by_full_name,
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


class NamingTest(unittest.TestCase):
    def test_get_full_name_options_with_single_word(self):
        pairs = list(get_full_name_options("Madonna"))
        self.assertEqual(len(pairs), 1)
        self.assertEqual([("Madonna", None)], pairs)

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
