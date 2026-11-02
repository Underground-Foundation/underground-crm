import datetime
import zoneinfo

import django.test
from django.contrib.auth import get_user_model

from underground_crm.models import PeopleFilter
from underground_crm.models.filter import LOGIC_ALL

Person = get_user_model()

MELBOURNE = zoneinfo.ZoneInfo("Australia/Melbourne")

# The day on which our early unsubscriber left the mailing list, and the time of
# day at which they did so. It is late in the evening so that the timestamp falls
# on the previous day in UTC, which checks that dates are compared in the site's
# time zone rather than in UTC.
EARLY_UNSUBSCRIPTION_DATE = datetime.date(2026, 3, 15)
LATE_UNSUBSCRIPTION_DATE = datetime.date(2026, 8, 20)
UNSUBSCRIPTION_TIME = datetime.time(8, 30)
EVENING_UNSUBSCRIPTION_TIME = datetime.time(23, 45)


def _single_rule_filter(name: str, rule: dict) -> PeopleFilter:
    return PeopleFilter.objects.create(name=name, criteria={"logic": LOGIC_ALL, "rules": [rule]})


def _matched_emails(people_filter: PeopleFilter) -> set[str]:
    return set(people_filter.apply(Person.objects.all()).values_list("email", flat=True))


class UnsubscribedAtFilterTest(django.test.TestCase):
    """
    Verifies the "Unsubscribed at" condition: it is either not set (the person
    is still subscribed), set at all, or falls before or after a given date.
    """

    def setUp(self):
        self.subscriber = Person.objects.create_user(
            email="harper.subscriber@example.com", password="password"
        )
        self.early_unsubscriber = Person.objects.create_user(
            email="quinn.early@example.com",
            password="password",
            unsubscribed_at=datetime.datetime.combine(
                EARLY_UNSUBSCRIPTION_DATE, EVENING_UNSUBSCRIPTION_TIME, tzinfo=MELBOURNE
            ),
        )
        self.late_unsubscriber = Person.objects.create_user(
            email="rowan.late@example.com",
            password="password",
            unsubscribed_at=datetime.datetime.combine(
                LATE_UNSUBSCRIPTION_DATE, UNSUBSCRIPTION_TIME, tzinfo=MELBOURNE
            ),
        )

    def test_not_set_matches_only_people_still_subscribed(self):
        still_subscribed = _single_rule_filter(
            "Still subscribed", {"field": "unsubscribed_at", "operator": "isnull"}
        )

        self.assertEqual(_matched_emails(still_subscribed), {self.subscriber.email})

    def test_set_matches_only_people_who_unsubscribed(self):
        unsubscribed = _single_rule_filter(
            "Unsubscribed", {"field": "unsubscribed_at", "operator": "not_isnull"}
        )

        self.assertEqual(
            _matched_emails(unsubscribed),
            {self.early_unsubscriber.email, self.late_unsubscriber.email},
        )

    def test_before_a_date_between_the_unsubscriptions(self):
        cutoff = EARLY_UNSUBSCRIPTION_DATE + datetime.timedelta(days=1)
        unsubscribed_before = _single_rule_filter(
            "Unsubscribed before the cutoff",
            {"field": "unsubscribed_at", "operator": "date__lt", "value": cutoff.isoformat()},
        )

        self.assertEqual(
            _matched_emails(unsubscribed_before),
            {self.early_unsubscriber.email},
            f"Only {self.early_unsubscriber.email} unsubscribed before {cutoff}; "
            f"{self.subscriber.email} never unsubscribed at all.",
        )

    def test_after_a_date_between_the_unsubscriptions(self):
        cutoff = EARLY_UNSUBSCRIPTION_DATE + datetime.timedelta(days=1)
        unsubscribed_after = _single_rule_filter(
            "Unsubscribed after the cutoff",
            {"field": "unsubscribed_at", "operator": "date__gt", "value": cutoff.isoformat()},
        )

        self.assertEqual(_matched_emails(unsubscribed_after), {self.late_unsubscriber.email})

    def test_before_and_after_both_exclude_the_given_date(self):
        unsubscription_date = EARLY_UNSUBSCRIPTION_DATE.isoformat()
        before = _single_rule_filter(
            "Unsubscribed before the early date",
            {"field": "unsubscribed_at", "operator": "date__lt", "value": unsubscription_date},
        )
        after = _single_rule_filter(
            "Unsubscribed after the early date",
            {"field": "unsubscribed_at", "operator": "date__gt", "value": unsubscription_date},
        )

        self.assertNotIn(
            self.early_unsubscriber.email,
            _matched_emails(before) | _matched_emails(after),
            "This person unsubscribed late in the evening of the given date in "
            "Melbourne, so they unsubscribed neither before nor after that date "
            "there, even though it was already the previous day in UTC.",
        )


class IsActiveFilterTest(django.test.TestCase):
    def setUp(self):
        self.active_person = Person.objects.create_user(
            email="ellis.active@example.com", password="password"
        )
        self.inactive_person = Person.objects.create_user(
            email="drew.tombstoned@example.com", password="password", is_active=False
        )

    def test_true_matches_active_people(self):
        active = _single_rule_filter("Active", {"field": "is_active", "operator": "true"})

        self.assertEqual(_matched_emails(active), {self.active_person.email})

    def test_false_matches_inactive_people(self):
        inactive = _single_rule_filter("Inactive", {"field": "is_active", "operator": "false"})

        self.assertEqual(_matched_emails(inactive), {self.inactive_person.email})


class TextEmptinessFilterTest(django.test.TestCase):
    """
    Text fields are usually blank as "" rather than NULL, so "is empty" and
    "is not empty" have to treat both as empty.
    """

    def setUp(self):
        self.named_person = Person.objects.create_user(
            email="avery.named@example.com", password="password", first_name="Avery"
        )
        self.unnamed_person = Person.objects.create_user(
            email="unnamed.supporter@example.com", password="password", first_name=""
        )
        self.never_named_person = Person.objects.create_user(
            email="never.named@example.com", password="password", first_name=None
        )

    def test_is_empty_matches_blank_text(self):
        no_first_name = _single_rule_filter(
            "No first name", {"field": "first_name", "operator": "isnull"}
        )

        self.assertEqual(
            _matched_emails(no_first_name),
            {self.unnamed_person.email, self.never_named_person.email},
        )

    def test_is_not_empty_excludes_blank_text(self):
        has_first_name = _single_rule_filter(
            "Has a first name", {"field": "first_name", "operator": "not_isnull"}
        )

        self.assertEqual(_matched_emails(has_first_name), {self.named_person.email})
