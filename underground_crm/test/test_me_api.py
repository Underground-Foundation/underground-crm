from django.urls import reverse
from rest_framework.test import APITestCase

from underground_crm.models import Person

# Fields the donation and subscription islands read from /api/me/ and treat as strings,
# calling .trim() on them before submitting the form.
STRING_FIELDS = ("first_name", "preferred_name", "last_name", "middle_name", "phone")


class MeBillingContextTest(APITestCase):
    """What /api/me/?context=billing tells the donation form about a signed-in donor."""

    def setUp(self) -> None:
        # Somebody who signed up with only their email address and surname, as many
        # imported supporters did: every other name field and both numbers are null.
        self.donor = Person.objects.create_user(
            email="r.nguyen@example.org", password="correct-horse-battery"
        )
        self.donor.last_name = "Nguyen"
        self.donor.save()
        self.client.force_authenticate(user=self.donor)

    def test_missing_details_are_sent_as_empty_strings(self) -> None:
        response = self.client.get(reverse("me"), {"context": "billing"})
        data = response.json()
        for field in STRING_FIELDS:
            with self.subTest(field=field):
                self.assertIsInstance(
                    data[field],
                    str,
                    f"The donation form calls .trim() on {field}, so null would crash it.",
                )
        self.assertEqual(
            data["phone"],
            "",
            "A donor with no number must not have their phone field prefilled with 'None'.",
        )
        self.assertEqual(data["last_name"], self.donor.last_name)
