import datetime
from typing import Any

import django.test
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from django.utils import timezone
from wagtail.models import Page

from underground_crm.blocks import registration_person_field_names
from underground_crm.forms.form_submission import _field_name
from underground_crm.forms.registration import (
    RegistrationForm,
    StructuredAddressField,
    _companion_name,
)
from underground_crm.models.address import Address
from underground_crm.models.form_submission import FormSubmission
from underground_crm.models.membership import Membership, MembershipType
from underground_crm.models.pages import RegistrationPage, RegistrationPageTag
from underground_crm.models.person import Tag
from underground_crm.widgets import ADDRESS_COMPONENTS

Person = get_user_model()

MOBILE_NUMBER = "+61412345678"
NEW_MOBILE_NUMBER = "+61423987654"
# A Melbourne fixed line: libphonenumber reports it as FIXED_LINE, so it belongs
# in Person.phone_number rather than Person.mobile_number.
LANDLINE_NUMBER = "+61398765432"
# A valid Australian number of a type nobody is contactable on (1900 numbers are
# premium-rate services), which the form must therefore refuse.
PREMIUM_RATE_NUMBER = "+611900654321"
HOME_ADDRESS_COMPONENTS = {
    "line1": "1 COOK RD",
    "city": "LINDFIELD",
    "state": "NSW",
    "postcode": "2070",
}
# The G-NAF Address Detail PID of HOME_ADDRESS_COMPONENTS (see test_address.py).
HOME_ADDRESS_GNAF_ID = "GANSW705239062"
MAILING_ADDRESS_COMPONENTS = {
    "line1": "c/- The Secretary",
    "line2": "Fusion Party NSW",
    "line3": "PO BOX 123",
    "city": "CHATSWOOD",
    "state": "NSW",
    "postcode": "2057",
}
PREFERRED_NAME = "Priya"
# An expiry date safely in the past, for building an already-lapsed Membership.
LAPSED_EXPIRY_DATE = datetime.date(2020, 1, 1)

# The names of a structured address widget's subfields, in rendering order:
# the visible components first, then the hidden G-NAF ID input.
ADDRESS_SUBFIELD_NAMES = [component.name for component in ADDRESS_COMPONENTS] + ["gnaf_id"]


class RegistrationFormTestMixin:
    """Building a form for self.page, and translating Person-field names into
    the per-block form-field names the page's blocks actually carry."""

    def _form(self, data: dict[str, Any] | None = None, user: Any = None) -> RegistrationForm:
        request = self.factory.get(f"/{self.page.slug}/")
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


# Pointing at a closed port forces Address.from_components onto its
# no-Addressr behavior (components stored as typed, unverified), so these
# tests behave the same whether or not the Addressr container happens to be
# running. The verification path against the real service is covered in
# test_address.py, which skips when the service is unavailable.
@override_settings(ADDRESSR_BASE_URL="http://127.0.0.1:1")
class RegistrationPageTest(RegistrationFormTestMixin, django.test.TestCase):
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

    def _address_data(self, person_field: str, **values: str) -> dict[str, str]:
        """POST data for one structured address question: the widget submits
        one value per subfield, named by the block's form-field name plus the
        subfield's position in ADDRESS_SUBFIELD_NAMES — exactly how
        MultiWidget names its inputs."""
        block = self._block(person_field)
        return {
            f"{_field_name(block)}_{ADDRESS_SUBFIELD_NAMES.index(name)}": value
            for name, value in values.items()
        }

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

    def test_form_for_an_unsaved_page_gives_every_block_its_own_field(self):
        # The preview panel of the "create" form renders a page that has never
        # been saved, and Wagtail only assigns a stream child its id on save.
        # The form keys its fields by that id, so an id-less block would key the
        # same field as every other one (see models.pages.with_form_field_ids).
        fresh_page = RegistrationPage(title="Join the movement", slug="join-the-movement")
        blocks = fresh_page.inputs
        field_names = [_field_name(block) for block in blocks]

        self.assertEqual(
            len(set(field_names)),
            len(blocks),
            "Every block on an unsaved page must key a form field of its own.",
        )

        request = RequestFactory().get(f"/{fresh_page.slug}/")
        request.user = AnonymousUser()
        # The crash this guards against happened here, while the form was being
        # built — before a single field could be rendered.
        form = RegistrationForm(request=request, page=fresh_page)

        rendered = set(form.fields)
        # Every block gets a field except the second of the two phone blocks,
        # which the merged phone question answers (see PhoneNumberQuestionTest).
        unrendered = [
            block.value["field"] for block in blocks if _field_name(block) not in rendered
        ]
        self.assertEqual(
            unrendered,
            ["mobile_number"],
            "The whole form must survive the preview of an unsaved page: only "
            "the phone block merged into the question above it may be absent.",
        )

    def test_authenticated_submission_updates_own_record(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        form = self._form(
            data={
                **self._input_data(mobile_number=MOBILE_NUMBER),
                **self._address_data("home_address", **HOME_ADDRESS_COMPONENTS),
            },
            user=member,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(str(member.mobile_number), MOBILE_NUMBER)
        self.assertIsNotNone(
            member.home_address,
            "The submitted address components must be resolved to an Address "
            "record linked from home_address.",
        )
        self.assertEqual(
            member.home_address.line1,
            HOME_ADDRESS_COMPONENTS["line1"],
            "The components are stored exactly as typed.",
        )
        self.assertEqual(member.home_address.postcode, HOME_ADDRESS_COMPONENTS["postcode"])
        self.assertIsNone(
            member.home_address.latitude,
            "With no Addressr verification (no suggestion picked, service "
            "down), the record stays unverified for the background geocoding "
            "flow to repair.",
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
            **self._address_data("home_address", **HOME_ADDRESS_COMPONENTS),
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

    def test_same_as_home_checkbox_names_its_address_role(self):
        mailing_block = self._block("mailing_address")
        form = self._form()

        checkbox_widget = form.fields[_companion_name(mailing_block)].widget
        address_label = str(form.fields[_field_name(mailing_block)].label)
        self.assertEqual(
            str(checkbox_widget.address_label),
            address_label,
            "Once ticked, the checkbox collapses the mailing address input "
            "and its own label from view (see same_as_home_address.js), so "
            "the checkbox's widget must carry the address role's label "
            "itself, to render as the heading above the checkbox (see "
            "same_as_home_checkbox.html) — otherwise a visitor sees an "
            "unlabelled 'same as home address' with no indication of which "
            "address that refers to.",
        )

    def test_distinct_mailing_address_gets_its_own_record(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        # The companion checkbox is absent from the POST, i.e. unticked.
        form = self._form(
            data={
                **self._address_data("home_address", **HOME_ADDRESS_COMPONENTS),
                **self._address_data("mailing_address", **MAILING_ADDRESS_COMPONENTS),
            },
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
        self.assertEqual(member.mailing_address.line1, MAILING_ADDRESS_COMPONENTS["line1"])
        self.assertEqual(
            member.mailing_address.line3,
            MAILING_ADDRESS_COMPONENTS["line3"],
            "All three address lines must survive the round trip through the " "structured inputs.",
        )

    def test_unchanged_address_resubmission_creates_no_duplicate(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        member.home_address = Address.objects.create(**HOME_ADDRESS_COMPONENTS)
        member.save()
        original_address_id = member.home_address_id
        addresses_before = Address.objects.count()

        form = self._form(
            data=self._address_data("home_address", **HOME_ADDRESS_COMPONENTS), user=member
        )
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

    def test_address_renders_structured_component_inputs(self):
        form = self._form()
        home_block = self._block("home_address")
        self.assertIsInstance(
            form.fields[_field_name(home_block)],
            StructuredAddressField,
            "Address questions are collected as their components, so a visitor "
            "who ignores the autocomplete can still complete the form.",
        )

        rendered = str(form[_field_name(home_block)])
        self.assertIn(
            "data-structured-address",
            rendered,
            "The component group is marked so address_autocomplete.js can "
            "spread a picked suggestion across the inputs.",
        )
        self.assertIn(
            "data-address-autocomplete",
            rendered,
            "The first line carries the Addressr-backed autocomplete.",
        )
        gnaf_subfield = f"{_field_name(home_block)}_{ADDRESS_SUBFIELD_NAMES.index('gnaf_id')}"
        self.assertIn(
            f'name="{gnaf_subfield}"',
            rendered,
            "The picked suggestion's G-NAF ID travels in a hidden input " "inside the group.",
        )

    def test_stored_address_prefills_the_component_inputs(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        member.home_address = Address.objects.create(
            **HOME_ADDRESS_COMPONENTS, gnaf_id=HOME_ADDRESS_GNAF_ID
        )
        member.save()

        form = self._form(user=member)
        rendered = str(form[_field_name(self._block("home_address"))])
        self.assertIn(
            f'value="{HOME_ADDRESS_COMPONENTS["line1"]}"',
            rendered,
            "An authenticated visitor sees their stored address spread across "
            "the component inputs.",
        )
        self.assertIn(
            f'value="{HOME_ADDRESS_GNAF_ID}"',
            rendered,
            "The stored G-NAF ID accompanies the pre-filled components, so "
            "resubmitting the form untouched keeps the address's verified "
            "identity.",
        )

    def test_unverifiable_gnaf_id_is_not_stored(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        form = self._form(
            data=self._address_data(
                "home_address", **HOME_ADDRESS_COMPONENTS, gnaf_id=HOME_ADDRESS_GNAF_ID
            ),
            user=member,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(
            member.home_address.line1,
            HOME_ADDRESS_COMPONENTS["line1"],
            "The submitted components must be kept even when their G-NAF ID " "cannot be checked.",
        )
        self.assertIsNone(
            member.home_address.gnaf_id,
            "With Addressr unreachable (this class points it at a closed "
            "port), the submitted G-NAF ID cannot be verified against the "
            "submitted components, and an unverified ID must never be "
            "recorded as though it had been.",
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

        member.home_address = Address.objects.create(**HOME_ADDRESS_COMPONENTS)
        member.mailing_address = Address.objects.create(**MAILING_ADDRESS_COMPONENTS)
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
            fields_by_person_field["home_address"],
            StructuredAddressField,
            "Address questions must collect the structured components with "
            "the Addressr-backed autocomplete on the first line.",
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


class MembershipGrantTest(RegistrationFormTestMixin, django.test.TestCase):
    """A RegistrationPage may name a MembershipType to grant on submission —
    see RegistrationForm._grant_membership."""

    def setUp(self):
        self.factory = RequestFactory()
        root = Page.objects.get(id=1)
        self.membership_type = MembershipType.objects.create(name="Fusion Party")
        self.page = RegistrationPage(
            title="Become a member",
            slug="become-a-member",
            membership=self.membership_type,
        )
        self.page.body = []
        root.add_child(instance=self.page)

    def test_membership_granted_on_registration(self):
        data = {
            "email": "lucas.nguyen@example.com",
            "first_name": "Lucas",
            "last_name": "Nguyen",
        }
        form = self._form(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        person = form.save()

        membership = Membership.objects.get(person=person, type=self.membership_type)
        self.assertTrue(
            membership.is_active,
            "A freshly granted membership must start active, with no expiry or "
            "suspension already set against it.",
        )

    def test_page_without_a_membership_configured_grants_nothing(self):
        self.page.membership = None
        self.page.save()

        data = {
            "email": "lucas.nguyen@example.com",
            "first_name": "Lucas",
            "last_name": "Nguyen",
        }
        form = self._form(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        person = form.save()

        self.assertFalse(
            Membership.objects.filter(person=person).exists(),
            "A page with no membership configured must not grant one.",
        )

    def test_existing_active_membership_is_not_duplicated(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        Membership.objects.create(
            person=member, type=self.membership_type, started_at=timezone.now()
        )

        form = self._form(data={}, user=member)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertEqual(
            Membership.objects.filter(person=member, type=self.membership_type).count(),
            1,
            "Resubmitting a registration must not create a second membership "
            "row for a type the person already holds.",
        )

    def test_existing_inactive_membership_is_not_duplicated(self):
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        Membership.objects.create(
            person=member,
            type=self.membership_type,
            started_at=timezone.now(),
            expires_on=LAPSED_EXPIRY_DATE,
        )

        form = self._form(data={}, user=member)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertEqual(
            Membership.objects.filter(person=member, type=self.membership_type).count(),
            1,
            "A lapsed membership of the page's type must still count as "
            "already held — the page grants a fresh row only for a type the "
            "person has never had at all.",
        )

    def test_different_membership_type_already_held_does_not_block_grant(self):
        other_type = MembershipType.objects.create(name="Pirate Party")
        member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        Membership.objects.create(person=member, type=other_type, started_at=timezone.now())

        form = self._form(data={}, user=member)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertTrue(
            Membership.objects.filter(person=member, type=self.membership_type).exists(),
            "Holding a membership of a different type must not stop the page's "
            "own type from being granted.",
        )


class PhoneNumberQuestionTest(RegistrationFormTestMixin, django.test.TestCase):
    """A page carrying both phone blocks asks for a phone number once, and
    sorts the answer into the landline or the mobile column itself."""

    # The merged question is rendered at the first phone block in the page's own
    # block order, which in the body below is the landline block — so that is
    # the block whose form-field name the POST data is keyed by. (The order
    # matches DEFAULT_REGISTRATION_PERSON_FIELDS, which a real page starts from.)
    MERGED_PHONE_BLOCK = "phone_number"

    def setUp(self):
        self.factory = RequestFactory()
        root = Page.objects.get(id=1)
        self.page = RegistrationPage(title="Join us", slug="join-us")
        self.page.body = [
            ("person_field", {"field": "phone_number", "label_override": ""}),
            ("person_field", {"field": "mobile_number", "label_override": ""}),
            ("person_field", {"field": "preferred_name", "label_override": ""}),
        ]
        root.add_child(instance=self.page)

    def _phone_data(self, number: str) -> dict[str, str]:
        return {_field_name(self._block(self.MERGED_PHONE_BLOCK)): number}

    def _member(self, **numbers: str) -> Any:
        return Person.objects.create_user(
            email="priya.sharma@example.com",
            password="correct-horse-battery",
            **numbers,
        )

    def test_both_phone_blocks_render_a_single_question(self):
        form = self._form()
        phone_fields = [
            name
            for name in form.fields
            if name
            in {_field_name(self._block(field)) for field in ("phone_number", "mobile_number")}
        ]
        self.assertEqual(
            phone_fields,
            [_field_name(self._block(self.MERGED_PHONE_BLOCK))],
            "A page carrying both phone blocks must ask for a phone number "
            "once, at the first of the two blocks — not once per column.",
        )
        self.assertEqual(
            str(form.fields[phone_fields[0]].label),
            "Phone number",
            "The merged question accepts either kind of number, so it must not "
            'be labelled with one column\'s name ("Mobile number").',
        )

    def test_mobile_number_is_stored_as_a_mobile(self):
        member = self._member()
        form = self._form(data=self._phone_data(MOBILE_NUMBER), user=member)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(str(member.mobile_number), MOBILE_NUMBER)
        self.assertIsNone(
            member.phone_number or None,
            "A mobile number belongs in the mobile column alone; the landline "
            "column is where the merged question was rendered, not where the "
            "answer is filed.",
        )

    def test_landline_is_stored_as_a_landline(self):
        member = self._member()
        form = self._form(data=self._phone_data(LANDLINE_NUMBER), user=member)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(str(member.phone_number), LANDLINE_NUMBER)
        self.assertIsNone(
            member.mobile_number or None,
            "A fixed-line number is not reachable by SMS, so it must not be " "filed as a mobile.",
        )

    def test_stored_mobile_prefills_the_question(self):
        member = self._member(mobile_number=MOBILE_NUMBER)
        form = self._form(user=member)

        self.assertEqual(
            str(form.fields[_field_name(self._block(self.MERGED_PHONE_BLOCK))].initial),
            MOBILE_NUMBER,
            "The one number a member has on file is shown back to them, "
            "whichever of the two columns it is stored in.",
        )

    def test_a_number_that_changes_column_leaves_nothing_behind(self):
        member = self._member(phone_number=LANDLINE_NUMBER)
        # The member was shown their landline and replaced it with their mobile.
        form = self._form(data=self._phone_data(MOBILE_NUMBER), user=member)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(str(member.mobile_number), MOBILE_NUMBER)
        self.assertIsNone(
            member.phone_number or None,
            "The number the member edited moved to the mobile column, so the "
            "landline they replaced must not linger as a second number.",
        )

    def test_a_number_never_shown_is_left_alone(self):
        member = self._member(mobile_number=MOBILE_NUMBER, phone_number=LANDLINE_NUMBER)
        # A member with both on file is shown their mobile (see
        # test_stored_mobile_prefills_the_question); changing it says nothing
        # about the landline, which this question never asked about.
        form = self._form(data=self._phone_data(NEW_MOBILE_NUMBER), user=member)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(str(member.mobile_number), NEW_MOBILE_NUMBER)
        self.assertEqual(
            str(member.phone_number),
            LANDLINE_NUMBER,
            "The landline was not the number under edit, so it survives.",
        )

    def test_an_uncontactable_number_is_rejected(self):
        member = self._member()
        form = self._form(data=self._phone_data(PREMIUM_RATE_NUMBER), user=member)

        self.assertFalse(
            form.is_valid(),
            f"{PREMIUM_RATE_NUMBER} is a premium-rate number: it is a valid "
            "Australian number, but not one anybody is reachable on, so it "
            "must not be stored as a way of contacting this member.",
        )

    def test_a_mobile_number_already_held_by_an_active_member_is_rejected(self):
        Person.objects.create_user(
            email="raj.patel@example.com",
            password="correct-horse-battery",
            mobile_number=MOBILE_NUMBER,
        )
        applicant = self._member()
        form = self._form(data=self._phone_data(MOBILE_NUMBER), user=applicant)

        self.assertFalse(
            form.is_valid(),
            f"{MOBILE_NUMBER} is already on file for a different active "
            "member, so a second registration giving the same mobile number "
            "must be rejected rather than silently attached to a new person.",
        )
        phone_field = _field_name(self._block(self.MERGED_PHONE_BLOCK))
        self.assertIn(
            phone_field,
            form.errors,
            "The rejection must be attached to the phone question itself, "
            f"so the visitor sees why their submission failed: {form.errors}",
        )

    def test_a_mobile_number_held_only_by_an_inactive_member_is_accepted(self):
        Person.objects.create_user(
            email="raj.patel@example.com",
            password="correct-horse-battery",
            mobile_number=MOBILE_NUMBER,
            is_active=False,
        )
        applicant = self._member()
        form = self._form(data=self._phone_data(MOBILE_NUMBER), user=applicant)

        self.assertTrue(
            form.is_valid(),
            "The only holder of this mobile number is an inactive record "
            f"(a tombstone), so it must not block a new registration: {form.errors}",
        )

    def test_a_members_own_mobile_number_is_not_rejected_as_taken(self):
        member = self._member(mobile_number=MOBILE_NUMBER)
        # Re-submitting the profile without changing the phone question still
        # posts the member's own number back, which must not be treated as a
        # collision with itself.
        form = self._form(data=self._phone_data(MOBILE_NUMBER), user=member)

        self.assertTrue(
            form.is_valid(),
            f"A member's own stored mobile number must not be rejected as "
            f"already taken when they re-submit it themselves: {form.errors}",
        )

    def test_a_blank_answer_erases_neither_column(self):
        member = self._member(mobile_number=MOBILE_NUMBER)
        form = self._form(
            data={**self._phone_data(""), **self._input_data(preferred_name=PREFERRED_NAME)},
            user=member,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        member.refresh_from_db()
        self.assertEqual(
            str(member.mobile_number),
            MOBILE_NUMBER,
            "Skipping the phone question means no answer, not a request to "
            "delete the number on file.",
        )
