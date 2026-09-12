import logging

import django.test
from django.contrib.auth import get_user_model

from underground_crm.models import PeopleFilter, Tag

logger = logging.getLogger(__name__)

Person = get_user_model()


class ExcludeTagFilterTest(django.test.TestCase):
    """
    Verifies the "does not equal" text operator, which is how a People
    Filter excludes everyone carrying a given tag (e.g. an email opt-in
    list minus everyone tagged "Do not contact").

    A naive ``~Q(tags__name=value)`` would only negate the one joined tag
    row that matched, so a person carrying the excluded tag alongside
    another tag would still slip through via their other tag's join row.
    These tests exercise that distinction directly rather than trusting it
    from the implementation.
    """

    def setUp(self):
        self.excluded_tag_name = "Volunteer"
        self.excluded_tag = Tag.objects.get(name=self.excluded_tag_name)
        self.other_tag, _ = Tag.objects.get_or_create(name="Newsletter subscriber")

        self.people_filter = PeopleFilter.objects.create(
            name="Everyone except volunteers",
            criteria={
                "logic": "AND",
                "rules": [
                    {
                        "field": "tags__name",
                        "operator": "not_exact",
                        "value": self.excluded_tag_name,
                    }
                ],
            },
        )

    def _matched_emails(self) -> set[str]:
        return set(self.people_filter.apply(Person.objects.all()).values_list("email", flat=True))

    def test_excludes_person_with_only_the_excluded_tag(self):
        volunteer = Person.objects.create_user(
            email="dana.volunteer@example.com", password="password"
        )
        volunteer.tags.add(self.excluded_tag)

        self.assertNotIn(
            volunteer.email,
            self._matched_emails(),
            "A person carrying the excluded tag should not match a filter "
            "built to exclude that same tag value.",
        )

    def test_excludes_person_who_also_carries_another_tag(self):
        multi_tagged = Person.objects.create_user(
            email="priya.multitag@example.com", password="password"
        )
        multi_tagged.tags.add(self.excluded_tag, self.other_tag)

        self.assertNotIn(
            multi_tagged.email,
            self._matched_emails(),
            "A person carrying both the excluded tag and another tag must "
            "still be excluded, not slip through via the other tag's join "
            "row.",
        )

    def test_includes_person_without_the_excluded_tag(self):
        untagged = Person.objects.create_user(email="sam.notag@example.com", password="password")
        untagged.tags.add(self.other_tag)

        self.assertIn(
            untagged.email,
            self._matched_emails(),
            "A person who never carries the excluded tag should still " "match the filter.",
        )
