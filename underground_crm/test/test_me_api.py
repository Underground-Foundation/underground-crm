"""
Tests for the phone number that the /api/me/ endpoint gives the donation form, which
fills in its billing details from this endpoint when the donor is logged in.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

BILLING_CONTEXT = {"context": "billing"}
# From the range the Australian Communications and Media Authority reserves for fiction.
MOBILE_NUMBER_E164 = "+61491570156"


class MeBillingPhoneTests(TestCase):
    def setUp(self) -> None:
        self.donor = get_user_model().objects.create_user(
            email="kerryn.abbott@example.org", first_name="Kerryn", last_name="Abbott"
        )
        self.client.force_login(self.donor)

    def test_a_person_without_a_phone_number_gets_no_phone(self) -> None:
        response = self.client.get(reverse("me"), BILLING_CONTEXT)

        self.assertIsNone(
            response.json()["phone"],
            'The form would otherwise show the literal text "None" in the phone field.',
        )

    def test_a_person_with_a_mobile_number_gets_it(self) -> None:
        self.donor.mobile_number = MOBILE_NUMBER_E164
        self.donor.save()

        response = self.client.get(reverse("me"), BILLING_CONTEXT)

        self.donor.refresh_from_db()
        self.assertEqual(response.json()["phone"], str(self.donor.mobile_number))
