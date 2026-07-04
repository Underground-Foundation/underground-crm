import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import django.test
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from wagtail.models import Page

from underground_crm.forms.event_guest import EventGuestForm
from underground_crm.models.pages import EventGuest, EventPage
from underground_crm.models.input_field import InputField

logger = logging.getLogger(__name__)

Person = get_user_model()


class EventGuestFormTest(django.test.TestCase):
    """
    EventPage extends FormPage, but its form always asks a baseline
    "how many guests are you bringing" question (a real EventGuest field,
    not a generic InputField) on top of any admin-added inputs — and
    submitting it creates an EventGuest (a FormSubmission subclass), not a
    plain FormSubmission.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.parking_spot = InputField.objects.create(
            description_en="Do you need a parking spot?", input_type=InputField.CHECKBOX
        )

        root = Page.objects.get(id=1)
        event_start = datetime(2026, 8, 15, 18, 0, tzinfo=ZoneInfo("Australia/Melbourne"))
        self.event_page = EventPage(
            title="Fundraising dinner",
            slug="fundraising-dinner",
            start_time=event_start,
            end_time=event_start + timedelta(hours=3),
            body=[("input", self.parking_spot)],
        )
        root.add_child(instance=self.event_page)

    def _field_name(self, input_field: InputField) -> str:
        return f"input_{input_field.name}"

    def test_baseline_extra_guests_field_is_always_present(self):
        request = self.factory.post("/fundraising-dinner/", {})
        request.user = AnonymousUser()
        form = EventGuestForm(request=request, page=self.event_page)
        self.assertIn(
            "extra_guests",
            form.fields,
            msg="extra_guests must be on every event's form, not something staff opt into",
        )

    def test_authenticated_rsvp_creates_event_guest_with_extra_guests(self):
        member = Person.objects.create_user(email="attendee@example.com", password="password")
        request = self.factory.post(
            "/fundraising-dinner/",
            {"extra_guests": "2", self._field_name(self.parking_spot): "on"},
        )
        request.user = member

        form = EventGuestForm(request.POST, request=request, page=self.event_page)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        submission = form.save()

        self.assertIsInstance(submission, EventGuest)
        self.assertTrue(submission.is_authenticated)
        self.assertEqual(submission.person_id, member.pk)
        self.assertEqual(submission.extra_guests, 2)

        # Admin-added inputs still work as ordinary SubmittedField rows against
        # the same EventGuest (which is itself a FormSubmission).
        submitted_field = submission.submitted_fields.get(input_field=self.parking_spot)
        self.assertTrue(submitted_field.has_value)

    def test_anonymous_rsvp_creates_placeholder_person_but_leaves_fk_unset(self):
        starting_person_count = Person.objects.count()
        request = self.factory.post(
            "/fundraising-dinner/",
            {
                "extra_guests": "1",
                "email": "guest@example.com",
                "first_name": "Gia",
                "last_name": "Guest",
            },
        )
        request.user = AnonymousUser()

        form = EventGuestForm(request.POST, request=request, page=self.event_page)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        submission = form.save()

        self.assertFalse(submission.is_authenticated)
        self.assertIsNone(submission.person)
        self.assertEqual(submission.email_address, "guest@example.com")
        self.assertEqual(submission.extra_guests, 1)
        self.assertEqual(Person.objects.count(), starting_person_count + 1)

    def test_extra_guests_defaults_to_zero_when_left_blank(self):
        member = Person.objects.create_user(email="lightweight@example.com", password="password")
        request = self.factory.post("/fundraising-dinner/", {})
        request.user = member

        form = EventGuestForm(request.POST, request=request, page=self.event_page)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        submission = form.save()
        self.assertEqual(submission.extra_guests, 0)


class RecordRsvpEngagementTest(django.test.TestCase):
    def setUp(self):
        root = Page.objects.get(id=1)
        event_start = datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("Australia/Melbourne"))
        self.event_page = EventPage(
            title="Members meetup",
            slug="members-meetup",
            start_time=event_start,
            end_time=event_start + timedelta(hours=2),
        )
        root.add_child(instance=self.event_page)

    def test_authenticated_rsvp_records_engagement_for_that_person(self):
        from underground_crm.models import Engagement
        from underground_crm.tasks import record_rsvp_engagement

        member = Person.objects.create_user(email="attendee2@example.com", password="password")
        event_guest = EventGuest.objects.create(
            is_authenticated=True, person=member, page=self.event_page
        )

        record_rsvp_engagement(str(event_guest.pk))

        engagement = Engagement.objects.get(person=member, action_type=Engagement.RSVP)
        self.assertEqual(engagement.page_title, self.event_page.title)

    def test_anonymous_rsvp_still_records_engagement_for_the_resolved_person(self):
        """
        The signal tags an engagement even for an unauthenticated RSVP — it
        resolves the same placeholder/matched Person that
        FormSubmissionForm.save() already created or confirmed by email.
        """
        from underground_crm.models import Engagement

        placeholder = Person.objects.create(
            email="anon-attendee@example.com", first_name="Anon", is_active=True
        )
        event_guest = EventGuest.objects.create(
            is_authenticated=False,
            email_address=placeholder.email,
            page=self.event_page,
        )

        from underground_crm.tasks import record_rsvp_engagement

        record_rsvp_engagement(str(event_guest.pk))

        engagement = Engagement.objects.get(person=placeholder, action_type=Engagement.RSVP)
        self.assertEqual(engagement.page_title, self.event_page.title)
