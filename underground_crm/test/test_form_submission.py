import csv
import logging
import tempfile
from pathlib import Path

import django.test
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory
from wagtail.models import Page

from underground_crm.forms.form_submission import FormSubmissionForm
from underground_crm.models.pages import FormPage
from underground_crm.models.person import Tag
from underground_crm.models.input_field import FormSubmission, InputField

logger = logging.getLogger(__name__)

Person = get_user_model()


class InputFieldTest(django.test.TestCase):
    def test_name_is_lowercased_with_spaces_as_underscores(self):
        description = "Can help with Calling Members"
        input_field = InputField.objects.create(description_en=description)
        self.assertEqual(
            input_field.name,
            "can_help_with_calling_members",
            msg="Name generation should lowercase the description and join its words with underscores",
        )

    def test_name_replaces_spaces_with_underscores(self):
        input_field = InputField.objects.create(description_en="Writing articles")
        self.assertEqual(input_field.name, "writing_articles")

    def test_name_strips_punctuation(self):
        description = "How would you rate your graphic-design skills?"
        input_field = InputField.objects.create(description_en=description)
        self.assertEqual(
            input_field.name,
            "how_would_you_rate_your_graphicdesign_skills",
            msg=(
                "Punctuation (the hyphen in 'graphic-design' and the trailing "
                "question mark) should be stripped entirely before the remaining "
                "words are lowercased and underscore-joined"
            ),
        )

    def test_explicit_name_overrides_generation(self):
        explicit_name = "calling_members"
        input_field = InputField.objects.create(
            description_en="Can help with Calling Members",
            name=explicit_name,
        )
        self.assertEqual(
            input_field.name,
            explicit_name,
            msg="Staff-supplied names must be kept as-is, not overwritten by auto-generation",
        )


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

        self.calling_members = InputField.objects.create(
            description_en="Calling members", input_type=InputField.CHECKBOX
        )
        self.videography = InputField.objects.create(
            description_en="Videography", input_type=InputField.CHECKBOX
        )
        self.availability = InputField.objects.create(
            description_en="Availability notes", input_type=InputField.TEXT
        )
        self.caller_tag = Tag.objects.create(name="Caller")
        self.videographer_tag = Tag.objects.create(name="Videographer")

        self.page = FormPage(
            title="Get involved",
            slug="get-involved",
            body=[
                ("input", self.calling_members),
                ("input", self.videography),
                ("input", self.availability),
            ],
        )
        root.add_child(instance=self.page)
        self.page.tags_to_apply.set([self.caller_tag, self.videographer_tag])

    def _field_name(self, input_field: InputField) -> str:
        return f"input_{input_field.name}"

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

        submitted = {row.input_field_id: row.has_value for row in submission.submitted_fields.all()}
        self.assertEqual(
            submitted,
            {
                self.calling_members.pk: True,
                self.videography.pk: False,
                self.availability.pk: True,
            },
            msg="Every input field on the page should get a row, even the ones left unchecked/blank",
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


class GetVolunteersWithFieldCommandTest(django.test.TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        root = Page.objects.get(id=1)
        self.calling_members = InputField.objects.create(
            description_en="Calling members", input_type=InputField.CHECKBOX
        )
        self.page = FormPage(
            title="Get involved",
            slug="get-involved",
            body=[("input", self.calling_members)],
        )
        root.add_child(instance=self.page)

    def _submit(self, user, checked: bool):
        request = self.factory.post(
            "/get-involved/", {f"input_{self.calling_members.name}": "on" if checked else ""}
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
            {f"input_{self.calling_members.name}": "on", "email": anonymous_email},
        )
        request.user = AnonymousUser()
        form = FormSubmissionForm(request.POST, request=request, page=self.page)
        self.assertTrue(form.is_valid())
        form.save()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "callers.csv"
            call_command(
                "get_volunteers_with_field",
                field="Calling members",
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
