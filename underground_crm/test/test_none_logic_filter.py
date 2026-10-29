import django.test
from django.contrib.auth import get_user_model

from underground_crm.models import PeopleFilter, Tag
from underground_crm.models.filter import LOGIC_ALL, LOGIC_NONE

Person = get_user_model()


class NoneLogicFilterTest(django.test.TestCase):
    """
    Verifies groups whose logic is "none", which match the people who match
    none of the group's conditions: for instance, a mailing list that leaves
    out everyone who marked our emails as spam or asked not to be contacted.

    A naive negation of the group's combined Q would only negate the one
    joined tag row that matched, so a person carrying the spam tag alongside
    another tag would still slip through via the other tag's join row. These
    tests exercise that case directly rather than trusting the implementation.
    """

    def setUp(self):
        self.spam_tag = Tag.objects.create(name="Marked our email as spam")
        self.newsletter_tag = Tag.objects.create(name="Newsletter subscriber")

        self.spam_rule = {
            "field": "tags__name",
            "operator": "exact",
            "value": self.spam_tag.name,
        }
        self.do_not_contact_rule = {"field": "do_not_contact", "operator": "true"}
        self.people_filter = PeopleFilter.objects.create(
            name="Everyone who welcomes our emails",
            criteria={
                "logic": LOGIC_NONE,
                "rules": [self.spam_rule, self.do_not_contact_rule],
            },
        )

    def _matched_emails(self, people_filter: PeopleFilter) -> set[str]:
        return set(people_filter.apply(Person.objects.all()).values_list("email", flat=True))

    def test_excludes_person_matching_one_of_the_conditions(self):
        complainant = Person.objects.create_user(
            email="morgan.complainant@example.com", password="password"
        )
        complainant.tags.add(self.spam_tag)
        withdrawn = Person.objects.create_user(
            email="alex.withdrawn@example.com", password="password", do_not_contact=True
        )

        matched_emails = self._matched_emails(self.people_filter)

        self.assertNotIn(
            complainant.email,
            matched_emails,
            "Someone who marked our email as spam matches the first condition, so "
            "a group requiring none of the conditions should leave them out.",
        )
        self.assertNotIn(
            withdrawn.email,
            matched_emails,
            "Someone who asked not to be contacted matches the second condition, "
            "so a group requiring none of the conditions should leave them out.",
        )

    def test_excludes_person_who_also_carries_another_tag(self):
        subscribed_complainant = Person.objects.create_user(
            email="jordan.subscriber@example.com", password="password"
        )
        subscribed_complainant.tags.add(self.spam_tag, self.newsletter_tag)

        self.assertNotIn(
            subscribed_complainant.email,
            self._matched_emails(self.people_filter),
            "A person carrying both the spam tag and another tag must still be "
            "excluded, not slip through via the other tag's join row.",
        )

    def test_includes_person_matching_none_of_the_conditions(self):
        subscriber = Person.objects.create_user(
            email="sam.subscriber@example.com", password="password"
        )
        subscriber.tags.add(self.newsletter_tag)
        untagged = Person.objects.create_user(
            email="riley.untagged@example.com", password="password"
        )

        matched_emails = self._matched_emails(self.people_filter)

        self.assertIn(
            subscriber.email,
            matched_emails,
            "A subscriber who never marked our email as spam and may be contacted "
            "matches neither condition, so they belong in the result.",
        )
        self.assertIn(
            untagged.email,
            matched_emails,
            "A person with no tags at all matches neither condition, so they "
            "belong in the result.",
        )

    def test_nested_none_group_narrows_its_parent(self):
        subscriber = Person.objects.create_user(
            email="sam.subscriber@example.com", password="password"
        )
        subscriber.tags.add(self.newsletter_tag)
        subscribed_complainant = Person.objects.create_user(
            email="jordan.subscriber@example.com", password="password"
        )
        subscribed_complainant.tags.add(self.spam_tag, self.newsletter_tag)
        non_subscriber = Person.objects.create_user(
            email="riley.untagged@example.com", password="password"
        )
        subscribers_who_welcome_email = PeopleFilter.objects.create(
            name="Newsletter subscribers who welcome our emails",
            criteria={
                "logic": LOGIC_ALL,
                "rules": [
                    {
                        "field": "tags__name",
                        "operator": "exact",
                        "value": self.newsletter_tag.name,
                    },
                    {"logic": LOGIC_NONE, "rules": [self.spam_rule]},
                ],
            },
        )

        self.assertEqual(
            self._matched_emails(subscribers_who_welcome_email),
            {subscriber.email},
            f"Only {subscriber.email} is a subscriber who has not marked our email "
            f"as spam: {subscribed_complainant.email} has, and "
            f"{non_subscriber.email} does not subscribe.",
        )

    def test_empty_none_group_excludes_nobody(self):
        everyone_emails = {
            Person.objects.create_user(email=email, password="password").email
            for email in ("sam.subscriber@example.com", "riley.untagged@example.com")
        }
        empty_group = PeopleFilter.objects.create(
            name="No exclusions yet", criteria={"logic": LOGIC_NONE, "rules": []}
        )

        self.assertTrue(
            everyone_emails <= self._matched_emails(empty_group),
            "A group with no conditions has nothing for anyone to match, so a "
            "'none' group with no conditions should leave everybody in.",
        )
