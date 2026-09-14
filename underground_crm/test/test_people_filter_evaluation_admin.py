import re

import django.test
from django.contrib.auth import get_user_model
from django.urls import reverse

from underground_crm.models import PeopleFilter

Person = get_user_model()


class PeopleFilterEvaluationAdminEmailDisplayTest(django.test.TestCase):
    """
    Covers the "Partial email addresses" sidebar setting on the saved-filter
    evaluation admin screen: on by default (showing the masked address), and
    dropping the email column entirely — not just blanking its value — when
    turned off.
    """

    EMAIL_COLUMN_HEADER = "<th>Email</th>"

    def setUp(self):
        self.staff_user = Person.objects.create_user(
            email="evaluator@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff_user)

        self.matched_email = "owen9825@gmail.com"
        self.matched_person = Person.objects.create_user(
            email=self.matched_email, password="password"
        )

        self.people_filter = PeopleFilter.objects.create(
            name="Everyone",
            criteria={
                "logic": "AND",
                "rules": [{"field": "email", "operator": "not_exact", "value": ""}],
            },
        )
        self.evaluation_url = reverse(
            "admin:underground_crm_peoplefilter_evaluate",
            kwargs={"pk": self.people_filter.pk},
        )

    def test_defaults_to_showing_the_partial_email_address_column(self):
        response = self.client.get(self.evaluation_url)

        self.assertContains(
            response,
            self.EMAIL_COLUMN_HEADER,
            msg_prefix="With no query parameters set, the evaluation screen should "
            "default the 'Partial email addresses' setting to on and render the Email "
            "column",
        )
        self.assertContains(
            response,
            self.matched_person.partial_email_address,
            msg_prefix="With no query parameters set, the evaluation screen should "
            "default the 'Partial email addresses' setting to on and show the masked "
            "address",
        )
        self.assertNotContains(
            response,
            self.matched_email,
            msg_prefix="The full, unmasked email address must never be rendered while "
            "the default partial-address setting is in effect",
        )

    def test_turning_the_setting_off_removes_the_email_column_entirely(self):
        response = self.client.get(self.evaluation_url, {"partial-email-addresses": "false"})

        self.assertNotContains(
            response,
            self.EMAIL_COLUMN_HEADER,
            msg_prefix="Turning 'Partial email addresses' off should drop the Email "
            "column header, not just blank out its values",
        )
        self.assertNotContains(
            response,
            self.matched_person.partial_email_address,
            msg_prefix="Turning 'Partial email addresses' off should stop the masked "
            "address from being shown",
        )
        self.assertNotContains(
            response,
            self.matched_email,
            msg_prefix="Turning 'Partial email addresses' off must hide the address "
            "entirely rather than revealing the full, unmasked one",
        )

    def _placeholder_row_colspan(self, response) -> int:
        match = re.search(rb'colspan="(\d+)"', response.content)
        self.assertIsNotNone(
            match,
            msg="Expected the 'No people match this filter' placeholder row to carry "
            "a colspan attribute, since it is the only cell in the row and must span "
            "every visible column.",
        )
        return int(match.group(1))

    def test_hiding_the_email_column_shrinks_the_placeholder_rows_colspan_by_one(self):
        # A filter built to match nobody, so the response renders the "No people match
        # this filter" placeholder row whose colspan must track the visible column count.
        unmatched_filter = PeopleFilter.objects.create(
            name="Nobody matches this filter",
            criteria={
                "logic": "AND",
                "rules": [
                    {
                        "field": "email",
                        "operator": "exact",
                        "value": "nobody-matches-this-filter@example.com",
                    }
                ],
            },
        )
        url = reverse(
            "admin:underground_crm_peoplefilter_evaluate",
            kwargs={"pk": unmatched_filter.pk},
        )

        colspan_with_email_column = self._placeholder_row_colspan(self.client.get(url))
        colspan_without_email_column = self._placeholder_row_colspan(
            self.client.get(url, {"partial-email-addresses": "false"})
        )

        self.assertEqual(
            colspan_with_email_column - colspan_without_email_column,
            1,
            msg=f"Removing the Email column should shrink the placeholder row's colspan "
            f"by exactly one column, so it still spans the full table width without a "
            f"phantom column; got {colspan_with_email_column} with the column shown and "
            f"{colspan_without_email_column} with it hidden.",
        )
