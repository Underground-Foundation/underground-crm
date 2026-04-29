import unittest
from decimal import Decimal

from underground_crm.numbers import parse_localized_number


class NumbersTest(unittest.TestCase):
    def test_parse_localized_number_de_DE(self):
        result = parse_localized_number("12.345,67", locale="de-DE")
        self.assertTrue(result)
        self.assertGreater(result, Decimal(12345))
        self.assertLess(result, Decimal(12346))

    def test_parse_localized_number_fr_FR(self):
        result = parse_localized_number("12 345,67", locale="fr-FR")
        self.assertTrue(result)
        self.assertGreater(result, Decimal(12345))
        self.assertLess(result, Decimal(12346))

    def test_parse_localized_number_en_AU(self):
        result = parse_localized_number("12,345.67", locale="en-AU")
        self.assertTrue(result)
        self.assertGreater(result, Decimal(12345))
        self.assertLess(result, Decimal(12346))
