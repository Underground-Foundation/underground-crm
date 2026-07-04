import logging

import django.test
from django.contrib.auth import get_user_model

from underground_crm.models import PeopleFilter, Tag

logger = logging.getLogger(__name__)

Person = get_user_model()


class IsVolunteerFilterTest(django.test.TestCase):
    def setUp(self):
        self.filter_name = "Is Volunteer"
        self.volunteer_tag = Tag.objects.get(name="Volunteer")

    def test_filter_is_seeded_by_migration(self):
        self.assertTrue(PeopleFilter.objects.filter(name=self.filter_name).exists())

    def test_matches_people_tagged_volunteer(self):
        tagged_person = Person.objects.create_user(
            email="volunteer@example.com", password="password"
        )
        tagged_person.tags.add(self.volunteer_tag)

        untagged_person = Person.objects.create_user(
            email="not-a-volunteer@example.com", password="password"
        )

        people_filter = PeopleFilter.objects.get(name=self.filter_name)
        matched_emails = set(
            people_filter.apply(Person.objects.all()).values_list("email", flat=True)
        )

        self.assertIn(tagged_person.email, matched_emails)
        self.assertNotIn(untagged_person.email, matched_emails)
