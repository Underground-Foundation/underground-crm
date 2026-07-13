from typing import Any, NamedTuple

from django import forms
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _
from wagtail.blocks.stream_block import StreamValue

from ..blocks import PERSON_FIELD_AUTOCOMPLETE, PERSON_NAME_FIELD_COLUMNS
from ..contactability import is_forbidden_phone_number, is_mobile_number
from ..models.address import Address
from ..models.membership import Membership
from ..widgets import (
    ADDRESS_COMPONENTS,
    AddressAutocompleteInput,
    SameAsHomeAddressCheckbox,
    StructuredAddressWidget,
)
from .form_submission import FormSubmissionForm, _field_name

Person = get_user_model()

# The Person field the "same as home address" checkboxes copy from.
HOME_ADDRESS_FIELD = "home_address"

# The two Person fields that hold a personal phone number, in the order they are
# pre-filled from: a visitor has one phone number as far as this form is
# concerned, and which column it lands in is a fact about the number's type
# rather than a question to put to them (see _phone_field_for). Whichever of
# these blocks the page carries, they collapse into the single question.
MOBILE_NUMBER_FIELD = "mobile_number"
LANDLINE_NUMBER_FIELD = "phone_number"
PHONE_PERSON_FIELDS = (MOBILE_NUMBER_FIELD, LANDLINE_NUMBER_FIELD)


def _phone_field_for(number: Any) -> str:
    """The Person field a submitted number belongs in: mobile_number when the
    number can reach a handset, phone_number (the landline column) otherwise."""
    return MOBILE_NUMBER_FIELD if is_mobile_number(number) else LANDLINE_NUMBER_FIELD


class NamingCell(NamedTuple):
    """One cell of the naming grid: a bound form field, and the Bootstrap column
    class setting its share of the row (see PERSON_NAME_FIELD_COLUMNS)."""

    field: forms.BoundField
    column_class: str


def _companion_name(input_block: StreamValue.StreamChild) -> str:
    """Form-field name of the "same as home address" checkbox that accompanies
    an address block, keyed by the block's UUID like the address field itself."""
    return f"same_as_home_{input_block.id}"


def _is_address_person_field(person_field_name: str) -> bool:
    """True if the named Person field links to an Address record."""
    model_field = Person._meta.get_field(person_field_name)
    return bool(model_field.is_relation) and model_field.related_model is Address


class SubmittedAddress(NamedTuple):
    """The visitor's answer to one structured address question: the component
    values as typed (or as filled in from a picked autocomplete suggestion),
    plus the picked suggestion's G-NAF ID — empty when nothing was picked, or
    when the visitor edited the components after picking."""

    line1: str
    line2: str
    line3: str
    city: str
    state: str
    postcode: str
    gnaf_id: str


class StructuredAddressField(forms.MultiValueField):
    """
    An address question collected as its separate components (see
    StructuredAddressWidget), cleaning to a SubmittedAddress — or to None when
    the visitor left every visible component blank, which counts as "no
    answer" just like an untouched text input.
    """

    widget = StructuredAddressWidget

    def __init__(self, **kwargs: Any) -> None:
        # One CharField per visible component, plus the hidden G-NAF ID.
        # Nothing is individually required: the form's questions are all
        # optional, and a partial manual address is still worth recording.
        fields = [forms.CharField(required=False) for _component in ADDRESS_COMPONENTS] + [
            forms.CharField(required=False)
        ]
        super().__init__(fields=fields, require_all_fields=False, required=False, **kwargs)

    def compress(self, data_list: list[str]) -> SubmittedAddress | None:
        if not data_list:
            return None
        submitted = SubmittedAddress(
            *((value or "").strip() for value in data_list),
        )
        if not any(getattr(submitted, component.name) for component in ADDRESS_COMPONENTS):
            # A G-NAF ID without any visible content is not an answer.
            return None
        return submitted


class RegistrationForm(FormSubmissionForm):
    """
    The form served by a RegistrationPage: one field per Person-field block in
    the page body, plus the identity fields (email/first_name/last_name)
    inherited from FormSubmissionForm for anonymous visitors.

    Person fields that link to an Address record (home_address,
    mailing_address, ...) are collected through a StructuredAddressField: one
    input per address component, with an Addressr-backed autocomplete on the
    first line. Picking a suggestion fills the components and records the
    suggestion's G-NAF ID in the group's hidden input (cleared again when the
    first line or locality is hand-edited; lines 2 and 3 leave it alone — see
    address_autocomplete.js), so the resolution lands on that exact address;
    a visitor who ignores the suggestions simply fills the components in by
    hand and is stored unverified for background geocoding.
    When the page carries a home_address block, every other address block also
    gets a "same as home address" checkbox, ticked by default: while it stays
    ticked the address inputs are collapsed (see same_as_home_address.js) and
    the field is linked to the same record as the home address.

    The phone_number and mobile_number blocks collapse into a single "phone
    number" question, wherever the page carries both: a visitor has a phone
    number, and asking them to sort it into a landline box or a mobile box
    makes them do work we can do ourselves. Whichever number they give is
    classified on submission — by the same libphonenumber type check the CSV
    importer uses (see contactability.is_mobile_number) — and stored in
    mobile_number or in phone_number accordingly.

    Unlike the parent form, a valid submission does not record a
    FormSubmission — it writes the submitted values onto a Person record:
    the visitor's own record when authenticated (with current values
    pre-filled), or a newly created one when an anonymous visitor registers
    with an unknown email address. An anonymous submission for an email that
    already has a Person is rejected outright with a prompt to log in: the
    parent form's name-match rule is a weak proof of identity that is
    tolerable for applying tags, but not for writing profile fields.
    """

    def __init__(self, *args: Any, request: HttpRequest, page: Any, **kwargs: Any) -> None:
        super().__init__(*args, request=request, page=page, **kwargs)

        self._phone_source_field = self._stored_phone_field()
        self._phone_block = self._merge_phone_fields()

        # The naming blocks the page actually carries, keyed by form-field name
        # and ordered by PERSON_NAME_FIELD_COLUMNS rather than by the page's
        # block order: the grid reads as a name, so it is laid out in the order
        # a name is written, whatever order the blocks happen to sit in. A page
        # carrying the same Person field twice yields a cell for each, so no
        # block silently disappears from the form.
        self._naming_columns: dict[str, str] = {
            _field_name(block): column_class
            for person_field, column_class in PERSON_NAME_FIELD_COLUMNS.items()
            for block in page.inputs
            if block.value["field"] == person_field
        }

        self._home_address_block = next(
            (block for block in page.inputs if block.value["field"] == HOME_ADDRESS_FIELD),
            None,
        )
        if self._home_address_block is None:
            # Without a home address on the form there is nothing for the
            # other address inputs to be "the same as" — they all render as
            # plain, always-visible address inputs.
            return

        # Insert a "same as home address" checkbox directly before each
        # non-home address input, preserving the page's block order.
        field_order: list[str] = [
            name for name in ("email", "first_name", "last_name") if name in self.fields
        ]
        for input_block in page.inputs:
            person_field = input_block.value["field"]
            if input_block is not self._home_address_block and _is_address_person_field(
                person_field
            ):
                companion = _companion_name(input_block)
                # The address field's own label (its label_override if the
                # editor set one, else the model field's verbose_name) names
                # the role this checkbox governs — without it, the checkbox
                # reads as "Same as home address" with no indication of which
                # address that is, once its collapse hides the address
                # field's own label from view. The widget renders it as a
                # heading above the checkbox (see
                # same_as_home_checkbox.html), so the field itself carries no
                # label of its own.
                address_label = self.fields[_field_name(input_block)].label
                self.fields[companion] = forms.BooleanField(
                    required=False,
                    initial=self._same_as_home_initially(person_field),
                    label="",
                    widget=SameAsHomeAddressCheckbox(
                        # For address fields this is the id of the structured
                        # widget's container element, collapsing the whole
                        # component group (see structured_address.html).
                        controlled_field_id=f"id_{_field_name(input_block)}",
                        address_label=address_label,
                    ),
                )
                field_order.append(companion)
            field_order.append(_field_name(input_block))
        self.order_fields(field_order)

    def _stored_phone_field(self) -> str | None:
        """The Person field the visitor's phone question is pre-filled from:
        their mobile number when they have one on file, else their landline,
        else nothing (an anonymous visitor, or a member with neither). Saving
        needs this as well as the initial value: it names the column the
        number was shown from, which is the only column the submission is
        allowed to clear (see _save_phone)."""
        user = self.request.user
        if not user.is_authenticated:
            return None
        return next(
            (field for field in PHONE_PERSON_FIELDS if getattr(user, field)),
            None,
        )

    def _merge_phone_fields(self) -> StreamValue.StreamChild | None:
        """
        Render the page's phone blocks as one question, and return the block it
        is rendered at — the first of them in the page's own block order, so
        the merged question keeps the position the editor gave it. Returns None
        when the page asks for no phone number at all.
        """
        phone_blocks = [
            block for block in self.page.inputs if block.value["field"] in PHONE_PERSON_FIELDS
        ]
        if not phone_blocks:
            return None

        presented, *redundant = phone_blocks
        for block in redundant:
            del self.fields[_field_name(block)]

        field = self.fields[_field_name(presented)]
        if not presented.value.get("label_override"):
            # Neither model field's own label or help text fits the merged
            # question: they describe the column ("Mobile number", "This should
            # be used for landline phones"), which the visitor is no longer
            # being asked to choose.
            field.label = _("Phone number")
        field.help_text = _("A mobile or a landline number.")

        # Whichever of the two columns the visitor's number is on file in is
        # the one shown back to them, even where the surviving block names the
        # other column.
        field.initial = (
            getattr(self.request.user, self._phone_source_field)
            if self._phone_source_field is not None
            else None
        )
        return presented

    @property
    def naming_fields(self) -> list[NamingCell]:
        """The cells of the naming grid, in the order a name is written (see
        person_naming.html). Empty when the page carries no naming block at
        all, in which case the grid renders nothing."""
        return [
            NamingCell(self[field_name], column_class)
            for field_name, column_class in self._naming_columns.items()
        ]

    @property
    def layout_fields(self) -> list[forms.BoundField]:
        """Everything the naming grid does not already render, laid out one
        field per row by form_page.html."""
        return [
            bound_field
            for bound_field in self.visible_fields()
            if bound_field.name not in self._naming_columns
        ]

    def _same_as_home_initially(self, person_field_name: str) -> bool:
        """
        Whether an address role's checkbox starts ticked (and its input
        collapsed): yes unless the authenticated visitor already holds an
        address there that differs from their home address.
        """
        user = self.request.user
        if not user.is_authenticated:
            return True
        current: Address | None = getattr(user, person_field_name)
        if current is None:
            return True
        home: Address | None = user.home_address
        return home is not None and (home.pk == current.pk or home.is_equivalent(current))

    def _field_for_input(self, input_block: StreamValue.StreamChild) -> forms.Field:
        """
        Derive the visitor-facing form field from the Person model field the
        block names, via Django's own model-to-form mapping — so a DateField
        renders a date input, a BooleanField a checkbox, and so on. Fields
        linking to Address instead render the structured address inputs with
        Addressr-backed autocomplete on the first line.
        """
        person_field_name: str = input_block.value["field"]
        model_field = Person._meta.get_field(person_field_name)

        if _is_address_person_field(person_field_name):
            field: forms.Field = StructuredAddressField()
            field.label = capfirst(model_field.verbose_name)
        elif person_field_name == "submitted_address":
            # The model maps this TextField to a textarea, but a single-line
            # autocomplete input matching the AddressBlock experience is what
            # visitors should see; the raw string lands in submitted_address
            # for the background geocoding flow, exactly as the field's
            # help text describes.
            field = forms.CharField(widget=AddressAutocompleteInput())
            field.label = _("Address")
        else:
            field = model_field.formfield() or forms.CharField()
            if isinstance(field, forms.DateField):
                # DateField.formfield() gives a DateInput whose default
                # template renders <input type="text">; the native HTML5
                # date picker needs the type set explicitly, and the format
                # pinned to ISO 8601 because a type="date" input only
                # accepts/displays that format, regardless of locale.
                field.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
                field.input_formats = ["%Y-%m-%d"]
            autocomplete = PERSON_FIELD_AUTOCOMPLETE.get(person_field_name)
            if autocomplete:
                field.widget.attrs["autocomplete"] = autocomplete

        field.required = False
        label_override = input_block.value.get("label_override")
        if label_override:
            field.label = label_override
        if self.request.user.is_authenticated:
            # For Address values the instance itself is the initial: the
            # structured widget decompresses it into the component inputs.
            field.initial = getattr(self.request.user, person_field_name)
        return field

    def _clean_identity(self) -> dict[str, Any]:
        if self.request.user.is_authenticated:
            return super().clean()

        # Deliberately skip FormSubmissionForm.clean() for anonymous visitors:
        # its name-match rule would let a submission through against an
        # existing Person, which must never happen on a form that writes
        # profile fields.
        cleaned = super(FormSubmissionForm, self).clean()
        email = cleaned.get("email")
        if not email:
            self.add_error("email", _("Email address is required."))
            return cleaned

        if Person.objects.filter(email=Person.objects.normalize_email(email)).exists():
            self.add_error(
                None,
                _(
                    "An account already exists for this email address. "
                    "Please log in to update your details."
                ),
            )
        return cleaned

    def clean(self) -> dict[str, Any]:
        cleaned = self._clean_identity()
        if self._phone_block is not None:
            phone_name = _field_name(self._phone_block)
            number = cleaned.get(phone_name)
            # The form field has already established that the number is a valid
            # one for the region; what it cannot know is that a premium-rate,
            # shared-cost or pager number is nobody's contact number, so it
            # would be stored as a way of reaching someone it cannot reach.
            if number and is_forbidden_phone_number(number):
                self.add_error(
                    phone_name,
                    _("Please give a phone number we can reach you on."),
                )
            elif number and is_mobile_number(number):
                # A mobile number reaches one handset, so two active accounts
                # sharing one is a sign of a duplicate registration rather
                # than a household landline that several people answer.
                existing_owners = Person.objects.filter(mobile_number=number, is_active=True)
                if self.request.user.is_authenticated:
                    existing_owners = existing_owners.exclude(pk=self.request.user.pk)
                if existing_owners.exists():
                    self.add_error(
                        phone_name,
                        _("This phone number is already taken."),
                    )
        return cleaned

    def _resolve_address(self, existing: Address | None, submitted: SubmittedAddress) -> Address:
        """
        The Address record a submitted address should link to: the existing
        record when it already describes the same location (so re-submitting
        an unchanged profile creates no duplicate records), otherwise a newly
        saved one. Never a record belonging to someone else — each address
        role links one-to-one to its own Address record.
        """
        candidate = Address.from_components(
            line1=submitted.line1,
            line2=submitted.line2,
            line3=submitted.line3,
            city=submitted.city,
            state=submitted.state,
            postcode=submitted.postcode,
            gnaf_id=submitted.gnaf_id or None,
        )
        if existing is not None and existing.is_equivalent(candidate):
            return existing
        candidate.save()
        return candidate

    def _save_home_address(self, person: Person) -> Address | None:
        """
        Resolve and assign the home address before any other address role:
        the "same as home address" checkboxes need the record to copy.
        Returns the person's home address as it stands after the submission
        (their previously stored one when the input was left empty).
        """
        if self._home_address_block is not None:
            value = self.cleaned_data.get(_field_name(self._home_address_block))
            if value:
                person.home_address = self._resolve_address(person.home_address, value)
        return person.home_address

    def _save_other_address(
        self,
        person: Person,
        input_block: StreamValue.StreamChild,
        home_address: Address | None,
    ) -> None:
        if self._home_address_block is not None and self.cleaned_data.get(
            _companion_name(input_block)
        ):
            # "Same as home address" links the same record. When no home
            # address is known the field is left unchanged rather than erased,
            # consistent with the empty-answer rule below.
            if home_address is not None:
                setattr(person, input_block.value["field"], home_address)
            return

        value = self.cleaned_data.get(_field_name(input_block))
        if value:
            person_field_name = input_block.value["field"]
            setattr(
                person,
                person_field_name,
                self._resolve_address(getattr(person, person_field_name), value),
            )

    def _save_phone(self, person: Person) -> None:
        """
        Store the one submitted number in the column its type calls for. An
        empty answer erases nothing, as everywhere else on this form.

        A number that changes column (a member replacing the landline we showed
        them with their mobile, say) is cleared from the column it came out of,
        so that it does not linger there as a second, stale number. Any number
        we did not show them — the landline of a member who also has a mobile —
        is left alone: this question was never asking about it.
        """
        if self._phone_block is None:
            return
        number = self.cleaned_data.get(_field_name(self._phone_block))
        if not number:
            return

        stored_field = _phone_field_for(number)
        setattr(person, stored_field, number)
        if self._phone_source_field is not None and self._phone_source_field != stored_field:
            setattr(person, self._phone_source_field, None)

    def _grant_membership(self, person: Person) -> None:
        """
        Create a Membership of the page's configured type, unless the person
        already holds one — active or not, so resubmitting an already-granted
        registration (or one the person later let lapse) never creates a
        second row for the same type.
        """
        membership_type = self.page.membership
        if membership_type is None:
            return
        if person.memberships.filter(type=membership_type).exists():
            return
        Membership.objects.create(
            person=person,
            type=membership_type,
            started_at=timezone.now(),
        )

    def save(self) -> Person:  # type: ignore[override]  # the parent returns a FormSubmission
        user = self.request.user
        if user.is_authenticated:
            person = user
            may_write_fields = True
        else:
            email = Person.objects.normalize_email(self.cleaned_data["email"])
            # clean() rejected already-registered emails, so this only races
            # against a Person created since validation; get_or_create keeps
            # that race from crashing, and only a freshly created record may
            # receive field values — never one that already existed.
            person, may_write_fields = Person.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": self.cleaned_data.get("first_name") or "",
                    "last_name": self.cleaned_data.get("last_name") or "",
                    "is_active": True,
                },
            )

        if may_write_fields:
            home_address = self._save_home_address(person)
            self._save_phone(person)
            for input_block in self.page.inputs:
                person_field_name = input_block.value["field"]
                if _is_address_person_field(person_field_name):
                    if person_field_name != HOME_ADDRESS_FIELD:
                        self._save_other_address(person, input_block, home_address)
                    continue
                if person_field_name in PHONE_PERSON_FIELDS:
                    # Both phone blocks are answered by the one merged question,
                    # which _save_phone has already stored (and which the
                    # redundant block has no form field for at all).
                    continue
                value = self.cleaned_data.get(_field_name(input_block))
                # A checkbox's False is a deliberate answer (the current value
                # was pre-filled, so unticking means opting out), but an empty
                # text/date field just means "no answer" and must not erase
                # whatever the record already holds.
                if isinstance(value, bool) or (value is not None and value != ""):
                    setattr(person, person_field_name, value)
            person.save()

        person.tags.add(*self.page.tags_to_apply.all())
        self._grant_membership(person)
        return person
