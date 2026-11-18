import csv
import datetime
import logging
import tempfile
from pathlib import Path

import django.test
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import timezone
from wagtail.models import Page

from underground_crm.forms.form_submission import (
    FormSubmissionForm,
    find_identity_conflict,
    get_or_create_person,
)
from underground_crm.models.pages import FormPage
from underground_crm.models.person import Tag
from underground_crm.models.form_submission import FormSubmission
from underground_crm.blocks import CHOICE_SEPARATOR
from underground_crm.test.form_inputs import checkbox_input, choice_input, picker_input, typed_input

logger = logging.getLogger(__name__)

Person = get_user_model()

CALLING_MEMBERS_QUESTION = "I can phone members to remind them about events"
VIDEOGRAPHY_QUESTION = "I can film speeches and rallies"
AVAILABILITY_QUESTION = "When are you usually available?"


class FormSubmissionValidationTest(django.test.TestCase):
    def setUp(self):
        root = Page.objects.get(id=1)
        self.page = FormPage(title="Get involved", slug="get-involved")
        root.add_child(instance=self.page)

    def test_authenticated_submission_without_person_is_invalid(self):
        submission = FormSubmission(is_authenticated=True, page=self.page)
        with self.assertRaises(ValidationError):
            submission.clean()

    def test_unauthenticated_submission_without_email_is_invalid(self):
        submission = FormSubmission(is_authenticated=False, page=self.page)
        with self.assertRaises(ValidationError):
            submission.clean()

    def test_authenticated_submission_with_person_is_valid(self):
        member = Person.objects.create_user(email="valid@example.com", password="password")
        submission = FormSubmission(is_authenticated=True, page=self.page, person=member)
        submission.clean()  # should not raise


class FormSubmissionFormTest(django.test.TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        root = Page.objects.get(id=1)

        self.caller_tag = Tag.objects.create(name="Caller")
        self.videographer_tag = Tag.objects.create(name="Videographer")

        self.page = FormPage(
            title="Get involved",
            slug="get-involved",
            body=[
                ("checkbox", checkbox_input(CALLING_MEMBERS_QUESTION)),
                ("checkbox", checkbox_input(VIDEOGRAPHY_QUESTION)),
                ("text", typed_input(AVAILABILITY_QUESTION)),
            ],
        )
        root.add_child(instance=self.page)
        self.page.tags_to_apply.set([self.caller_tag, self.videographer_tag])
        # Reload so the body's stream children carry their persisted UUIDs —
        # the ids that both the form's field names and SubmittedField.block_id
        # are keyed by.
        self.page.refresh_from_db()
        self.calling_members, self.videography, self.availability = self.page.inputs

    def _field_name(self, input_block) -> str:
        return f"input_{input_block.id}"

    def _post_request(self, data, user):
        request = self.factory.post("/get-involved/", data)
        request.user = user
        return request

    def test_authenticated_submission_records_inputs_and_applies_tags(self):
        member = Person.objects.create_user(email="member@example.com", password="password")
        self.assertEqual(
            member.tags.count(),
            0,
            msg="Sanity check: a freshly created Person should start with no tags",
        )

        request = self._post_request(
            {
                self._field_name(self.calling_members): "on",
                self._field_name(self.availability): "Weekday evenings",
            },
            user=member,
        )
        form = FormSubmissionForm(request.POST, request=request, page=self.page)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        submission = form.save()

        self.assertTrue(submission.is_authenticated)
        self.assertEqual(submission.person_id, member.pk)
        self.assertIsNone(submission.email_address)
        self.assertIsNone(submission.ip_address)

        submitted = {row.block_id: row.has_value for row in submission.submitted_fields.all()}
        self.assertEqual(
            submitted,
            {
                str(self.calling_members.id): True,
                str(self.videography.id): False,
                str(self.availability.id): True,
            },
            msg="Every input block on the page should get a row, even the ones left unchecked/blank",
        )

        availability_row = submission.submitted_fields.get(block_id=str(self.availability.id))
        self.assertEqual(availability_row.value, "Weekday evenings")
        self.assertEqual(
            (availability_row.name, availability_row.label),
            ("text", AVAILABILITY_QUESTION),
            msg="SubmittedField should snapshot the block's type name and the question the editor wrote",
        )

        member_tag_names = set(member.tags.values_list("name", flat=True))
        self.assertEqual(
            member_tag_names,
            {self.caller_tag.name, self.videographer_tag.name},
            msg="The page's tags_to_apply should be added to an authenticated submitter",
        )

    def test_anonymous_submission_requires_email(self):
        request = self._post_request(
            {self._field_name(self.calling_members): "on"}, user=AnonymousUser()
        )
        form = FormSubmissionForm(request.POST, request=request, page=self.page)
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_anonymous_submission_creates_placeholder_person_but_leaves_fk_unset(self):
        starting_person_count = Person.objects.count()
        anonymous_email = "curious-visitor@example.com"

        request = self._post_request(
            {
                self._field_name(self.calling_members): "on",
                "email": anonymous_email,
                "first_name": "Curious",
                "last_name": "Visitor",
            },
            user=AnonymousUser(),
        )
        form = FormSubmissionForm(request.POST, request=request, page=self.page)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        submission = form.save()

        self.assertFalse(submission.is_authenticated)
        self.assertIsNone(
            submission.person,
            msg="FormSubmission.person is only ever set for a real logged-in session",
        )
        self.assertEqual(submission.email_address, anonymous_email)
        self.assertEqual(submission.ip_address, "127.0.0.1")

        self.assertEqual(
            Person.objects.count(),
            starting_person_count + 1,
            msg="A placeholder Person should be created for a brand-new anonymous email",
        )
        placeholder = Person.objects.get(email=anonymous_email)
        self.assertEqual(placeholder.first_name, "Curious")
        self.assertEqual(placeholder.last_name, "Visitor")
        self.assertEqual(
            placeholder.password,
            "",
            msg="A placeholder Person gets an empty password, per spec — note this isn't the "
            "same as Django's own has_usable_password()==False marker (set_unusable_password() "
            "writes a non-empty '!'-prefixed string); a literal empty string was asked for here",
        )
        self.assertIsNone(placeholder.last_login)
        self.assertTrue(placeholder.is_active)

        placeholder_tag_names = set(placeholder.tags.values_list("name", flat=True))
        self.assertEqual(
            placeholder_tag_names,
            {self.caller_tag.name, self.videographer_tag.name},
            msg="Tags still apply to the placeholder Person even though the submission is anonymous",
        )

    def test_anonymous_submission_matching_existing_member_name_is_allowed_but_stays_anonymous(
        self,
    ):
        existing_member = Person.objects.create_user(
            email="verified@example.com",
            password="password",
            first_name="Real",
            last_name="Name",
        )
        starting_person_count = Person.objects.count()

        request = self._post_request(
            {
                self._field_name(self.calling_members): "on",
                "email": existing_member.email,
                "first_name": "real",  # matching is case-insensitive
                "last_name": "NAME",
            },
            user=AnonymousUser(),
        )
        form = FormSubmissionForm(request.POST, request=request, page=self.page)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        submission = form.save()

        self.assertFalse(submission.is_authenticated)
        self.assertIsNone(
            submission.person,
            msg="Even when the name matches, the submission is still stored the unauthenticated way",
        )
        self.assertEqual(
            Person.objects.count(),
            starting_person_count,
            msg="Matching an existing Person must not create a duplicate",
        )

        existing_member.refresh_from_db()
        member_tag_names = set(existing_member.tags.values_list("name", flat=True))
        self.assertEqual(
            member_tag_names,
            {self.caller_tag.name, self.videographer_tag.name},
            msg="Tags still apply to the matched existing Person",
        )

    def test_anonymous_submission_with_mismatched_name_is_rejected(self):
        """
        Core regression test: an anonymous visitor who types in an existing
        member's email address, but not their real name, must be rejected
        rather than being allowed to tag that member's account.
        """
        verified_member = Person.objects.create_user(
            email="verified@example.com",
            password="password",
            first_name="Real",
            last_name="Name",
        )

        request = self._post_request(
            {
                self._field_name(self.calling_members): "on",
                "email": verified_member.email,
                "first_name": "Someone",
                "last_name": "Else",
            },
            user=AnonymousUser(),
        )
        form = FormSubmissionForm(request.POST, request=request, page=self.page)
        self.assertFalse(
            form.is_valid(),
            msg="A name mismatch against an existing account must reject the submission",
        )

        verified_member.refresh_from_db()
        self.assertEqual(verified_member.first_name, "Real")
        self.assertEqual(
            verified_member.tags.count(),
            0,
            msg="A rejected submission must not tag the existing member's account",
        )


class FormInputLabelTest(django.test.TestCase):
    """
    Each input block carries the question the editor wrote for it, plus
    optional help text and whether it must be answered, and the visitor's
    form field takes all three from the block, along with a typed input's
    placeholder text.
    """

    consent_question = "I agree to be contacted about volunteering"
    consent_help_text = "We will only contact you about the activities you tick on this form."
    suburb_question = "Which suburb do you live in?"
    suburb_placeholder = "e.g. Brunswick"

    def setUp(self):
        self.factory = RequestFactory()
        root = Page.objects.get(id=1)
        self.page = FormPage(
            title="Volunteer with us",
            slug="volunteer-with-us",
            body=[
                (
                    "checkbox",
                    checkbox_input(
                        self.consent_question,
                        help_text=self.consent_help_text,
                        required=True,
                    ),
                ),
                ("text", typed_input(self.suburb_question, placeholder=self.suburb_placeholder)),
            ],
        )
        root.add_child(instance=self.page)
        self.page.refresh_from_db()
        self.consent, self.suburb = self.page.inputs
        self.member = Person.objects.create_user(email="volunteer@example.com", password="password")

    def _form(self, data=None) -> FormSubmissionForm:
        request = self.factory.post("/volunteer-with-us/", data or {})
        request.user = self.member
        return FormSubmissionForm(data, request=request, page=self.page)

    def test_visitor_sees_the_question_and_help_text_the_editor_wrote(self):
        rendered = self._form().as_p()
        self.assertIn(
            self.consent_question,
            rendered,
            msg="The checkbox must be captioned with the editor's question, not its type name",
        )
        self.assertIn(self.consent_help_text, rendered)
        self.assertIn(self.suburb_question, rendered)

    def test_placeholder_is_shown_but_never_submitted(self):
        suburb_field = self._form()[f"input_{self.suburb.id}"]
        self.assertIn(f'placeholder="{self.suburb_placeholder}"', str(suburb_field))
        self.assertIsNone(
            suburb_field.value(),
            msg="A placeholder is only an example, so the input itself must start empty",
        )

    def test_required_checkbox_must_be_ticked(self):
        unticked = self._form({})
        self.assertFalse(unticked.is_valid())
        self.assertIn(
            f"input_{self.consent.id}",
            unticked.errors,
            msg="The editor marked the consent checkbox as required, so leaving it unticked is an error",
        )

        ticked = self._form({f"input_{self.consent.id}": "on"})
        self.assertTrue(ticked.is_valid(), msg=f"Form errors: {ticked.errors}")

    def test_optional_input_may_be_left_blank(self):
        form = self._form({f"input_{self.consent.id}": "on", f"input_{self.suburb.id}": ""})
        self.assertTrue(
            form.is_valid(),
            msg=f"The suburb question is not marked as required, so a blank answer is accepted: {form.errors}",
        )


class ChoiceInputTest(django.test.TestCase):
    """
    The radio_buttons, dropdown and checkboxes blocks offer the choices an
    editor lists one per line, and SubmittedField records the answer in the
    words of the choice the visitor picked.
    """

    electorate_question = "Which electorate do you live in?"
    electorates = ["Wills", "Cooper", "Melbourne", "Macnamara"]
    contact_question = "How should we contact you?"
    contact_methods = ["Phone call", "Text message", "Email"]
    activities_question = "What could you help with on election day?"
    activities = ["Handing out how-to-vote cards", "Scrutineering", "Driving voters to booths"]

    def setUp(self):
        self.factory = RequestFactory()
        root = Page.objects.get(id=1)
        self.page = FormPage(
            title="Election day volunteers",
            slug="election-day",
            body=[
                ("dropdown", choice_input(self.electorate_question, self.electorates)),
                (
                    "radio_buttons",
                    choice_input(self.contact_question, self.contact_methods, required=True),
                ),
                ("checkboxes", choice_input(self.activities_question, self.activities)),
            ],
        )
        root.add_child(instance=self.page)
        self.page.refresh_from_db()
        self.electorate, self.contact, self.activity = self.page.inputs
        self.member = Person.objects.create_user(
            email="booth-captain@example.com", password="password"
        )

    def _form(self, data=None) -> FormSubmissionForm:
        request = self.factory.post("/election-day/", data or {})
        request.user = self.member
        return FormSubmissionForm(data, request=request, page=self.page)

    def _submit(self, data) -> dict[str, tuple[bool, str]]:
        """Submits the form, returning each input's (has_value, value) by label."""
        form = self._form(data)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        submission = form.save()
        return {row.label: (row.has_value, row.value) for row in submission.submitted_fields.all()}

    def test_every_choice_is_offered_in_the_order_listed(self):
        form = self._form()
        for input_block, choices in [
            (self.contact, self.contact_methods),
            (self.activity, self.activities),
        ]:
            offered = [label for _value, label in form.fields[f"input_{input_block.id}"].choices]
            self.assertEqual(offered, choices)

    def test_dropdown_starts_on_a_blank_prompt(self):
        form = self._form()
        dropdown_choices = form.fields[f"input_{self.electorate.id}"].choices
        self.assertEqual(
            [value for value, _label in dropdown_choices],
            [""] + self.electorates,
            msg="A visitor who skips the drop-down must not be recorded as picking its first choice",
        )

    def test_picked_choices_are_recorded_in_the_visitors_words(self):
        picked_electorate = self.electorates[1]
        picked_contact = self.contact_methods[2]
        picked_activities = [self.activities[0], self.activities[2]]
        answers = self._submit(
            {
                f"input_{self.electorate.id}": picked_electorate,
                f"input_{self.contact.id}": picked_contact,
                f"input_{self.activity.id}": picked_activities,
            }
        )
        self.assertEqual(
            answers,
            {
                self.electorate_question: (True, picked_electorate),
                self.contact_question: (True, picked_contact),
                self.activities_question: (True, CHOICE_SEPARATOR.join(picked_activities)),
            },
        )

    def test_unanswered_optional_choices_are_recorded_as_unanswered(self):
        answers = self._submit({f"input_{self.contact.id}": self.contact_methods[0]})
        self.assertEqual(answers[self.electorate_question], (False, ""))
        self.assertEqual(
            answers[self.activities_question],
            (False, ""),
            msg="Ticking none of the boxes is a row with no value, not a missing row",
        )

    def test_required_radio_buttons_must_be_answered(self):
        form = self._form({})
        self.assertFalse(form.is_valid())
        self.assertIn(f"input_{self.contact.id}", form.errors)

    def test_a_choice_the_editor_did_not_list_is_rejected(self):
        form = self._form(
            {
                f"input_{self.contact.id}": self.contact_methods[0],
                f"input_{self.electorate.id}": "Kooyong",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn(
            f"input_{self.electorate.id}",
            form.errors,
            msg="Kooyong is not among the listed electorates, so a tampered request must not record it",
        )

    def test_choices_render_as_a_captioned_group(self):
        form = self._form()
        for input_block, question, choices in [
            (self.contact, self.contact_question, self.contact_methods),
            (self.activity, self.activities_question, self.activities),
        ]:
            field_html = render_to_string(
                "underground_crm/includes/form_field.html",
                {"field": form[f"input_{input_block.id}"]},
            )
            self.assertIn(
                f'<legend class="col-form-label pt-0">{question}</legend>',
                field_html,
                msg="A group of options is captioned by a legend, since no one <label> fits them all",
            )
            for choice in choices:
                self.assertIn(f">{choice}</label>", field_html)


class DateAndTimeInputTest(django.test.TestCase):
    """
    The date, time and datetime blocks use the browser's own pickers, which
    exchange values with the server in fixed ISO formats whatever they display,
    rather than Wagtail's CMS widgets, whose JavaScript a public page never
    loads.
    """

    start_question = "Which day could you start?"
    arrival_question = "What time could you arrive at the booth?"
    callback_question = "When should we call you back?"
    start_day = datetime.date(2026, 8, 15)
    arrival_time = datetime.time(7, 30)
    callback_time = datetime.datetime(2026, 8, 12, 18, 45)

    def setUp(self):
        self.factory = RequestFactory()
        root = Page.objects.get(id=1)
        self.page = FormPage(
            title="Booth roster",
            slug="booth-roster",
            body=[
                ("date", picker_input(self.start_question)),
                ("time", picker_input(self.arrival_question)),
                ("datetime", picker_input(self.callback_question)),
            ],
        )
        root.add_child(instance=self.page)
        self.page.refresh_from_db()
        self.start, self.arrival, self.callback = self.page.inputs
        self.member = Person.objects.create_user(
            email="booth-worker@example.com", password="password"
        )

    def _form(self, data=None) -> FormSubmissionForm:
        request = self.factory.post("/booth-roster/", data or {})
        request.user = self.member
        return FormSubmissionForm(data, request=request, page=self.page)

    def test_inputs_render_as_native_pickers(self):
        form = self._form()
        for input_block, input_type in [
            (self.start, "date"),
            (self.arrival, "time"),
            (self.callback, "datetime-local"),
        ]:
            input_html = str(form[f"input_{input_block.id}"])
            self.assertIn(f'type="{input_type}"', input_html)
            self.assertNotIn(
                "<script",
                input_html,
                msg="Wagtail's CMS date widgets append a script that fails on a public page",
            )

    def test_values_in_the_pickers_formats_are_accepted(self):
        form = self._form(
            {
                f"input_{self.start.id}": self.start_day.isoformat(),
                # A time picker sends hours and minutes, without seconds.
                f"input_{self.arrival.id}": self.arrival_time.strftime("%H:%M"),
                # A datetime-local picker sends the same, joined by a "T".
                f"input_{self.callback.id}": self.callback_time.strftime("%Y-%m-%dT%H:%M"),
            }
        )
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        self.assertEqual(form.cleaned_data[f"input_{self.start.id}"], self.start_day)
        self.assertEqual(form.cleaned_data[f"input_{self.arrival.id}"], self.arrival_time)
        self.assertEqual(
            form.cleaned_data[f"input_{self.callback.id}"],
            timezone.make_aware(self.callback_time),
            msg="The picker sends local time, which is read in the site's own time zone",
        )


class GetVolunteersWithFieldCommandTest(django.test.TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        root = Page.objects.get(id=1)
        self.page = FormPage(
            title="Get involved",
            slug="get-involved",
            body=[("checkbox", checkbox_input(CALLING_MEMBERS_QUESTION))],
        )
        root.add_child(instance=self.page)
        self.page.refresh_from_db()
        (self.calling_members,) = self.page.inputs

    def _submit(self, user, checked: bool):
        request = self.factory.post(
            "/get-involved/", {f"input_{self.calling_members.id}": "on" if checked else ""}
        )
        request.user = user
        form = FormSubmissionForm(request.POST, request=request, page=self.page)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        form.save()

    def test_exports_only_authenticated_people_who_answered_yes(self):
        caller = Person.objects.create_user(email="caller@example.com", password="password")
        self._submit(caller, checked=True)

        declined = Person.objects.create_user(email="declined@example.com", password="password")
        self._submit(declined, checked=False)

        anonymous_email = "anon@example.com"
        request = self.factory.post(
            "/get-involved/",
            {f"input_{self.calling_members.id}": "on", "email": anonymous_email},
        )
        request.user = AnonymousUser()
        form = FormSubmissionForm(request.POST, request=request, page=self.page)
        self.assertTrue(form.is_valid())
        form.save()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "callers.csv"
            call_command(
                "get_volunteers_with_field",
                field=CALLING_MEMBERS_QUESTION,  # the label snapshotted at submission time
                output=str(output_path),
            )
            with open(output_path, newline="", encoding="utf-8") as csv_file:
                rows = list(csv.DictReader(csv_file))

        emails_exported = {row["email"] for row in rows}
        self.assertEqual(
            emails_exported,
            {caller.email},
            msg="Only the authenticated Person who answered yes should be exported",
        )
        self.assertNotIn(declined.email, emails_exported)
        self.assertNotIn(
            anonymous_email,
            emails_exported,
            msg="Anonymous submissions are never tied to a Person, so they can't be exported",
        )

    def test_unknown_input_field_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command(
                "get_volunteers_with_field", field="Not a real input field", output="/dev/null"
            )


class FindIdentityConflictTest(django.test.TestCase):
    def setUp(self):
        self.member = Person.objects.create_user(
            email="rebecca@example.com",
            password="password",
            first_name="Rebecca",
            last_name="Smith",
        )

    def test_first_name_alone_must_match_first_name(self):
        self.assertIsNone(find_identity_conflict(self.member.email, "rebecca", "SMITH"))
        self.assertIsNotNone(find_identity_conflict(self.member.email, "Bex", "Smith"))

    def test_preferred_name_matches_first_name_when_no_preferred_name_is_recorded(self):
        self.assertIsNone(
            find_identity_conflict(self.member.email, last_name="Smith", preferred_name="Rebecca")
        )
        self.assertIsNotNone(
            find_identity_conflict(self.member.email, last_name="Smith", preferred_name="Bex")
        )

    def test_preferred_name_matches_recorded_preferred_name(self):
        self.member.preferred_name = "Bex"
        self.member.save()
        self.assertIsNone(
            find_identity_conflict(self.member.email, last_name="Smith", preferred_name="bex")
        )
        self.assertIsNotNone(
            find_identity_conflict(self.member.email, last_name="Smith", preferred_name="Rebecca")
        )

    def test_both_names_must_each_match_when_both_are_supplied(self):
        self.member.preferred_name = "Bex"
        self.member.save()
        self.assertIsNone(find_identity_conflict(self.member.email, "Rebecca", "Smith", "Bex"))
        self.assertIsNotNone(find_identity_conflict(self.member.email, "Rebecca", "Smith", "Becky"))

    def test_last_name_must_always_match(self):
        self.assertIsNotNone(
            find_identity_conflict(self.member.email, preferred_name="Rebecca", last_name="Jones")
        )

    def test_unknown_email_never_conflicts(self):
        self.assertIsNone(find_identity_conflict("new@example.com", preferred_name="Bex"))

    def test_get_or_create_person_stores_preferred_name(self):
        person = get_or_create_person("new@example.com", last_name="Jones", preferred_name="Bex")
        self.assertEqual(person.preferred_name, "Bex")
        self.assertIsNone(person.first_name)

    def test_get_or_create_person_creates_a_supporter(self):
        person = get_or_create_person("new@example.com", last_name="Jones", preferred_name="Bex")
        self.assertTrue(
            person.is_supporter,
            "The newsletter signup creates people through this function, and they must "
            "match the “Emailable living supporters” filter.",
        )
