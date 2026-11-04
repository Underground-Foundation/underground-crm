import django.test
from django.contrib.auth import get_user_model

from underground_crm.models import PeopleFilter, Tag
from underground_crm.models.filter import LOGIC_ALL, LOGIC_ANY

Person = get_user_model()


def _tag_rule(tag: Tag) -> dict:
    return {"field": "tags__name", "operator": "exact", "value": tag.name}


class MultipleTagsFilterTest(django.test.TestCase):
    """
    Verifies conditions on several tags in one People Filter.

    Conditions on a many-valued relation must each be free to match a different
    related row. If they shared one join, "tagged Volunteer and tagged Donor" would
    demand a single tag row with both names, and would match nobody.
    """

    def setUp(self):
        self.volunteer_tag, _ = Tag.objects.get_or_create(name="Volunteer")
        self.campaigner_tag, _ = Tag.objects.get_or_create(name="Campaign organizer")

        self.untagged = self._person("harper.untagged@example.com")
        self.volunteer = self._person("quinn.volunteer@example.com", self.volunteer_tag)
        self.campaigner = self._person("rowan.campaigner@example.com", self.campaigner_tag)
        self.volunteering_campaigner = self._person(
            "ellis.both@example.com", self.volunteer_tag, self.campaigner_tag
        )

    @staticmethod
    def _person(email: str, *tags: Tag) -> Person:
        person = Person.objects.create_user(email=email, password="password")
        person.tags.add(*tags)
        return person

    @staticmethod
    def _matched_emails(logic: str, *rules: dict) -> list[str]:
        """Return a list rather than a set, so that a duplicated person remains visible."""
        people_filter = PeopleFilter.objects.create(
            name="Tagged people under test", criteria={"logic": logic, "rules": list(rules)}
        )
        return list(people_filter.apply(Person.objects.all()).values_list("email", flat=True))

    def test_all_of_two_tags_matches_the_person_with_both(self):
        self.assertEqual(
            self._matched_emails(
                LOGIC_ALL, _tag_rule(self.volunteer_tag), _tag_rule(self.campaigner_tag)
            ),
            [self.volunteering_campaigner.email],
        )

    def test_all_of_two_operators_may_match_different_tags(self):
        self.assertEqual(
            self._matched_emails(
                LOGIC_ALL,
                _tag_rule(self.volunteer_tag),
                {
                    "field": "tags__name",
                    "operator": "istartswith",
                    "value": self.campaigner_tag.name.split()[0],
                },
            ),
            [self.volunteering_campaigner.email],
        )

    def test_any_of_two_tags_lists_the_person_with_both_only_once(self):
        matched = self._matched_emails(
            LOGIC_ANY, _tag_rule(self.volunteer_tag), _tag_rule(self.campaigner_tag)
        )

        self.assertCountEqual(
            matched,
            [self.volunteer.email, self.campaigner.email, self.volunteering_campaigner.email],
            "A person with both tags matches through two tag rows, but is still one "
            "person, and would otherwise receive a bulk email twice over.",
        )

    def test_tag_conditions_combine_with_other_fields(self):
        self.volunteering_campaigner.do_not_call = True
        self.volunteering_campaigner.save()
        self.volunteer.do_not_call = True
        self.volunteer.save()

        self.assertEqual(
            self._matched_emails(
                LOGIC_ALL,
                {"field": "do_not_call", "operator": "true"},
                {
                    "logic": LOGIC_ALL,
                    "rules": [_tag_rule(self.volunteer_tag), _tag_rule(self.campaigner_tag)],
                },
            ),
            [self.volunteering_campaigner.email],
        )

    def test_is_empty_matches_only_the_person_with_no_tags(self):
        self.assertEqual(
            self._matched_emails(LOGIC_ALL, {"field": "tags__name", "operator": "isnull"}),
            [self.untagged.email],
        )

    def test_is_not_empty_lists_each_tagged_person_once(self):
        self.assertCountEqual(
            self._matched_emails(LOGIC_ALL, {"field": "tags__name", "operator": "not_isnull"}),
            [self.volunteer.email, self.campaigner.email, self.volunteering_campaigner.email],
        )
