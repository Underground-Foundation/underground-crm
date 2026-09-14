import django.test
from django.contrib.auth import get_user_model
from django.urls import reverse

from underground_crm.models import PeopleFilter

Person = get_user_model()


class PeopleFilterEvaluationAdminEmailDisplayTest(django.test.TestCase):
    """
    Covers the "Partial email addresses" sidebar setting on the saved-filter
    evaluation admin screen: on by default (showing the masked address), and
    hiding the address entirely rather than falling back to the full one when
    turned off.
    """

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

    def test_defaults_to_showing_the_partial_email_address(self):
        response = self.client.get(self.evaluation_url)

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

    def test_turning_the_setting_off_hides_the_address_entirely(self):
        response = self.client.get(self.evaluation_url, {"partial-email-addresses": "false"})

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
