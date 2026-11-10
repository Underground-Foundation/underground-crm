import django.test
from django.contrib.auth import get_user_model

from underground_crm.models import PeopleFilter
from underground_crm.models.filter import LOGIC_ALL

Person = get_user_model()

# Plausible Australian numbers, as people would type them into the signup form.
LANDLINE = "+61 3 9123 4567"
MOBILE = "+61 412 345 678"


def _matched_emails(rule: dict) -> set[str]:
    people_filter = PeopleFilter(
        name="Phone condition under test", criteria={"logic": LOGIC_ALL, "rules": [rule]}
    )
    return set(people_filter.apply(Person.objects.all()).values_list("email", flat=True))


class PhoneNumberPresenceFilterTest(django.test.TestCase):
    """
    Verifies "is empty" and "is not empty" on the phone number fields, where a
    missing number may be stored either as NULL or as an empty string.
    """

    def setUp(self):
        self.with_both = Person.objects.create_user(
            email="harper.both@example.com",
            password="password",
            phone_number=LANDLINE,
            mobile_number=MOBILE,
        )
        self.with_null_numbers = Person.objects.create_user(
            email="quinn.null@example.com", password="password"
        )
        # Saving through the ORM would turn a blank number into NULL, so this
        # writes the empty strings directly, as an import or raw SQL might.
        self.with_blank_numbers = Person.objects.create_user(
            email="rowan.blank@example.com", password="password"
        )
        Person.objects.filter(pk=self.with_blank_numbers.pk).update(
            phone_number="", mobile_number=""
        )

    def test_empty_matches_both_null_and_blank_numbers(self):
        for field in ("phone_number", "mobile_number"):
            with self.subTest(field=field):
                self.assertEqual(
                    _matched_emails({"field": field, "operator": "isnull"}),
                    {self.with_null_numbers.email, self.with_blank_numbers.email},
                )

    def test_not_empty_matches_only_people_with_a_number(self):
        for field in ("phone_number", "mobile_number"):
            with self.subTest(field=field):
                self.assertEqual(
                    _matched_emails({"field": field, "operator": "not_isnull"}),
                    {self.with_both.email},
                )

    def test_landline_and_mobile_are_tested_separately(self):
        mobile_only = Person.objects.create_user(
            email="ellis.mobileonly@example.com", password="password", mobile_number=MOBILE
        )

        self.assertIn(
            mobile_only.email,
            _matched_emails({"field": "phone_number", "operator": "isnull"}),
            "A person with only a mobile number has no landline number.",
        )
        self.assertIn(
            mobile_only.email,
            _matched_emails({"field": "mobile_number", "operator": "not_isnull"}),
        )


class MobileFlagFilterTest(django.test.TestCase):
    """Verifies the "Mobile opt-in" and "Mobile number is bad" conditions."""

    def setUp(self):
        self.opted_in_and_reachable = Person.objects.create_user(
            email="harper.sms@example.com",
            password="password",
            mobile_number=MOBILE,
            mobile_opt_in=True,
            is_mobile_bad=False,
        )
        self.opted_out_and_unreachable = Person.objects.create_user(
            email="quinn.disconnected@example.com",
            password="password",
            mobile_number=MOBILE,
            mobile_opt_in=False,
            is_mobile_bad=True,
        )

    def test_each_flag_is_true_for_one_person_and_false_for_the_other(self):
        cases = {
            ("mobile_opt_in", "true"): self.opted_in_and_reachable,
            ("mobile_opt_in", "false"): self.opted_out_and_unreachable,
            ("is_mobile_bad", "true"): self.opted_out_and_unreachable,
            ("is_mobile_bad", "false"): self.opted_in_and_reachable,
        }
        for (field, operator), expected in cases.items():
            with self.subTest(field=field, operator=operator):
                self.assertEqual(
                    _matched_emails({"field": field, "operator": operator}), {expected.email}
                )
