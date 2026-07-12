from typing import Any

import django.test
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from wagtail.models import Page

from underground_crm.blocks import registration_person_field_names
from underground_crm.forms.form_submission import _field_name
from underground_crm.forms.registration import RegistrationForm, _companion_name
from underground_crm.models.address import Address
from underground_crm.models.form_submission import FormSubmission
from underground_crm.models.pages import RegistrationPage, RegistrationPageTag
from underground_crm.models.person import Tag
from underground_crm.widgets import AddressAutocompleteInput

Person = get_user_model()

MOBILE_NUMBER = "+61412345678"
HOME_ADDRESS = "1 COOK RD, LINDFIELD NSW 2070"
MAILING_ADDRESS = "PO BOX 123, CHATSWOOD NSW 2057"
PREFERRED_NAME = "Priya"


# Pointing at a closed port forces Address.from_one_line onto its no-Addressr
# fallback (the raw string kept in line1), so these tests behave the same
# whether or not the Addressr container happens to be running. The resolution
# path against the real service is covered in test_address.py, which skips
# when the service is unavailable.
@override_settings(ADDRESSR_BASE_URL="http://127.0.0.1:1")
class RegistrationPageTest(django.test.TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        root = Page.objects.get(id=1)
        self.page = RegistrationPage(title="Become a member", slug="become-a-member")
        self.page.body = [
            ("person_field", {"field": "preferred_name", "label_override": ""}),
            ("person_field", {"field": "mobile_number", "label_override": ""}),
            ("person_field", {"field": "home_address", "label_override": ""}),
            ("person_field", {"field": "mailing_address", "label_override": ""}),
            ("person_field", {"field": "date_of_birth", "label_override": ""}),
        ]
        root.add_child(instance=self.page)

    def _form(self, data: dict[str, Any] | None = None, user: Any = None) -> RegistrationForm:
        request = self.factory.get("/become-a-member/")
        request.user = user if user is not None else AnonymousUser()
        return RegistrationForm(data, request=request, page=self.page)

    def _block(self, person_field: str) -> Any:
        """The page's input block for the named Person field."""
        return next(block for block in self.page.inputs if block.value["field"] == person_field)

    def _input_data(self, **values_by_person_field: Any) -> dict[str, Any]:
        """Translate Person-field names into the per-block form field names,
        which are keyed by each block's stream-child UUID."""
        data: dict[str, Any] = {}
        for block in self.page.inputs:
            person_field = block.value["field"]
            if person_field in values_by_person_field:
                data[_field_name(block)] = values_by_person_field[person_field]
        return data

    def test_new_page_starts_with_every_whitelisted_field(self):
        # No body is assigned here, unlike setUp's page — this exercises the
        # StreamField default that a staff member sees on the "create" form.
        fresh_page = RegistrationPage(title="Join the movement", slug="join-the-movement")
        self.assertEqual(
            [(block.block_type, block.value["field"]) for block in fresh_page.body],
            [("person_field", name) for name in registration_person_field_names()],
            "A freshly created page must start with one Person-field block per "
            "whitelisted field, so staff delete the details they don't want "
            "rather than assembling the form from nothing.",
        )

    def test_authenticated_submission_updates_own_record(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        form = self._form(
            data=self._input_data(mobile_number=MOBILE_NUMBER, home_address=HOME_ADDRESS),
            user=member,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(str(member.mobile_number), MOBILE_NUMBER)
        self.assertIsNotNone(
            member.home_address,
            "The submitted one-line address must be resolved to an Address "
            "record linked from home_address.",
        )
        self.assertEqual(
            member.home_address.line1,
            HOME_ADDRESS,
            "With Addressr unavailable, the raw string is kept in line1 for "
            "the geocode_addresses command to repair later.",
        )
        self.assertEqual(
            FormSubmission.objects.count(),
            0,
            "A registration submission writes to the Person record instead of "
            "recording a FormSubmission.",
        )

    def test_same_as_home_address_links_the_same_record(self):
        mailing_block = self._block("mailing_address")
        data = {
            "email": "lucas.nguyen@example.com",
            "first_name": "Lucas",
            "last_name": "Nguyen",
            **self._input_data(home_address=HOME_ADDRESS),
            _companion_name(mailing_block): "on",
        }
        form = self._form(data=data)

        field_names = list(form.fields)
        self.assertEqual(
            field_names.index(_companion_name(mailing_block)) + 1,
            field_names.index(_field_name(mailing_block)),
            "The checkbox must render directly above the address input it " "collapses.",
        )

        self.assertTrue(form.is_valid(), form.errors)
        person = form.save()

        self.assertIsNotNone(person.home_address)
        self.assertEqual(
            person.mailing_address_id,
            person.home_address_id,
            '"Same as home address" must link the mailing role to the very '
            "same Address record, not a copy.",
        )
        self.assertEqual(
            Address.objects.count(),
            1,
            "One submitted address shared between two roles must produce a "
            "single Address record.",
        )

    def test_distinct_mailing_address_gets_its_own_record(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        # The companion checkbox is absent from the POST, i.e. unticked.
        form = self._form(
            data=self._input_data(home_address=HOME_ADDRESS, mailing_address=MAILING_ADDRESS),
            user=member,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertIsNotNone(member.mailing_address)
        self.assertNotEqual(
            member.mailing_address_id,
            member.home_address_id,
            "A differing mailing address must get its own Address record.",
        )
        self.assertEqual(member.mailing_address.line1, MAILING_ADDRESS)

    def test_unchanged_address_resubmission_creates_no_duplicate(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        member.home_address = Address.objects.create(line1=HOME_ADDRESS)
        member.save()
        original_address_id = member.home_address_id
        addresses_before = Address.objects.count()

        form = self._form(data=self._input_data(home_address=HOME_ADDRESS), user=member)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(
            member.home_address_id,
            original_address_id,
            "Re-submitting the pre-filled, unchanged address must keep the "
            "existing Address record.",
        )
        self.assertEqual(
            Address.objects.count(),
            addresses_before,
            "Re-submitting an unchanged address must not create a duplicate " "Address record.",
        )

    def test_same_as_home_checkbox_initial_state(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        companion = _companion_name(self._block("mailing_address"))

        form = self._form(user=member)
        self.assertTrue(
            form.fields[companion].initial,
            "With no stored mailing address, the checkbox starts ticked and "
            "the input collapsed.",
        )

        member.home_address = Address.objects.create(line1=HOME_ADDRESS)
        member.mailing_address = Address.objects.create(line1=MAILING_ADDRESS)
        member.save()
        form = self._form(user=member)
        self.assertFalse(
            form.fields[companion].initial,
            "A stored mailing address that differs from home must start "
            "unticked, so the visitor sees the address they hold.",
        )

    def test_no_companion_checkbox_without_home_address_on_page(self):
        root = Page.objects.get(id=1)
        page = RegistrationPage(title="Mailing list signup", slug="mailing-list-signup")
        page.body = [("person_field", {"field": "mailing_address", "label_override": ""})]
        root.add_child(instance=page)

        request = self.factory.get("/mailing-list-signup/")
        request.user = AnonymousUser()
        form = RegistrationForm(request=request, page=page)
        self.assertFalse(
            any(name.startswith("same_as_home_") for name in form.fields),
            'Without a home address on the page there is nothing to be "the '
            'same as", so no checkbox may appear.',
        )

    def test_blank_answer_does_not_erase_existing_value(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com",
            password="correct-horse-battery",
            preferred_name=PREFERRED_NAME,
        )
        form = self._form(
            data=self._input_data(preferred_name="", mobile_number=MOBILE_NUMBER),
            user=member,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(
            member.preferred_name,
            PREFERRED_NAME,
            "An empty answer means the visitor skipped the question, not that "
            "they want their stored value erased.",
        )

    def test_anonymous_registration_creates_person(self):
        data = {
            "email": "lucas.nguyen@example.com",
            "first_name": "Lucas",
            "last_name": "Nguyen",
            **self._input_data(mobile_number=MOBILE_NUMBER),
        }
        form = self._form(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        person = Person.objects.get(email="lucas.nguyen@example.com")
        self.assertEqual(person.first_name, "Lucas")
        self.assertEqual(str(person.mobile_number), MOBILE_NUMBER)
        self.assertEqual(
            person.password,
            "",
            "Registration creates a placeholder Person, the same as anonymous "
            "form submissions do: no password hash is stored, so no credential "
            "can ever authenticate as this record.",
        )

    def test_anonymous_submission_for_existing_email_is_rejected(self):
        existing = Person.objects.create_user(
            email="priya.sharma@example.com",
            password="correct-horse-battery",
            first_name="Priya",
            last_name="Sharma",
        )
        # Even with the matching name that FormSubmissionForm would accept for
        # tagging, a registration must refuse: profile fields are being written.
        data = {
            "email": existing.email,
            "first_name": existing.first_name,
            "last_name": existing.last_name,
            **self._input_data(mobile_number=MOBILE_NUMBER),
        }
        form = self._form(data=data)
        self.assertFalse(
            form.is_valid(),
            "An anonymous submission must never modify an existing Person, "
            "regardless of whether the supplied name matches.",
        )

        existing.refresh_from_db()
        self.assertIsNone(
            existing.mobile_number or None,
            "The rejected submission must leave the existing record untouched.",
        )

    def test_non_whitelisted_field_is_not_exposed_or_written(self):
        # A body block referencing a field outside the whitelist could exist
        # if the whitelist shrinks after a page is published; the whitelist is
        # applied at render time, so such a block must go dead rather than
        # keep working.
        self.page.body = list(self.page.body.raw_data) + [
            {"type": "person_field", "value": {"field": "is_admin", "label_override": ""}}
        ]
        self.page.save()

        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        rogue_block = next(block for block in self.page.body if block.value["field"] == "is_admin")
        form = self._form(data={_field_name(rogue_block): "true"}, user=member)
        self.assertNotIn(
            _field_name(rogue_block),
            form.fields,
            "Blocks for non-whitelisted fields must not become form fields.",
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertFalse(
            member.is_admin,
            "A crafted POST against a non-whitelisted block must not reach the " "Person record.",
        )

    def test_fields_derive_from_the_person_model(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com",
            password="correct-horse-battery",
            preferred_name=PREFERRED_NAME,
        )
        form = self._form(user=member)
        fields_by_person_field = {
            block.value["field"]: form.fields[_field_name(block)] for block in self.page.inputs
        }

        self.assertIsInstance(
            fields_by_person_field["date_of_birth"],
            forms.DateField,
            "The visitor-facing field comes from the model field's own "
            "formfield() mapping, so a model DateField must yield a form DateField.",
        )
        self.assertIsInstance(
            fields_by_person_field["home_address"].widget,
            AddressAutocompleteInput,
            "Address questions must offer the Addressr-backed autocomplete.",
        )
        self.assertEqual(
            fields_by_person_field["preferred_name"].initial,
            PREFERRED_NAME,
            "Authenticated visitors see their current values pre-filled.",
        )

    def test_tags_applied_on_registration(self):
        # get_or_create because a migration already seeds a protected
        # "Volunteer" tag.
        volunteer_tag, _created = Tag.objects.get_or_create(name="Volunteer")
        RegistrationPageTag.objects.create(content_object=self.page, tag=volunteer_tag)

        data = {
            "email": "lucas.nguyen@example.com",
            "first_name": "Lucas",
            "last_name": "Nguyen",
        }
        form = self._form(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        person = form.save()

        self.assertIn(volunteer_tag, person.tags.all())
