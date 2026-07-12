from typing import Any

from django import forms
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _
from wagtail.blocks.stream_block import StreamValue

from ..models.address import Address
from ..widgets import AddressAutocompleteInput, SameAsHomeAddressCheckbox
from .form_submission import FormSubmissionForm, _field_name

Person = get_user_model()

# The Person field the "same as home address" checkboxes copy from.
HOME_ADDRESS_FIELD = "home_address"


def _companion_name(input_block: StreamValue.StreamChild) -> str:
    """Form-field name of the "same as home address" checkbox that accompanies
    an address block, keyed by the block's UUID like the address field itself."""
    return f"same_as_home_{input_block.id}"


def _is_address_person_field(person_field_name: str) -> bool:
    """True if the named Person field links to an Address record."""
    model_field = Person._meta.get_field(person_field_name)
    return bool(model_field.is_relation) and model_field.related_model is Address


class RegistrationForm(FormSubmissionForm):
    """
    The form served by a RegistrationPage: one field per Person-field block in
    the page body, plus the identity fields (email/first_name/last_name)
    inherited from FormSubmissionForm for anonymous visitors.

    Person fields that link to an Address record (home_address,
    mailing_address, ...) are collected as one line of free text through the
    Addressr-backed autocomplete, and the submitted string is resolved to an
    Address record on save. When the page carries a home_address block, every
    other address block also gets a "same as home address" checkbox, ticked by
    default: while it stays ticked the address input is collapsed (see
    same_as_home_address.js) and the field is linked to the same record as the
    home address.

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
                self.fields[companion] = forms.BooleanField(
                    required=False,
                    initial=self._same_as_home_initially(person_field),
                    label=_("Same as home address"),
                    widget=SameAsHomeAddressCheckbox(
                        controlled_field_id=f"id_{_field_name(input_block)}"
                    ),
                )
                field_order.append(companion)
            field_order.append(_field_name(input_block))
        self.order_fields(field_order)

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
        linking to Address instead render the Addressr-backed autocomplete
        input, collecting the address as a single line of text.
        """
        person_field_name: str = input_block.value["field"]
        model_field = Person._meta.get_field(person_field_name)

        if _is_address_person_field(person_field_name):
            field: forms.Field = forms.CharField(widget=AddressAutocompleteInput())
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

        field.required = False
        label_override = input_block.value.get("label_override")
        if label_override:
            field.label = label_override
        if self.request.user.is_authenticated:
            current_value = getattr(self.request.user, person_field_name)
            if isinstance(current_value, Address):
                current_value = current_value.one_line
            field.initial = current_value
        return field

    def clean(self) -> dict[str, Any]:
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

    def _resolve_address(self, existing: Address | None, one_line: str) -> Address:
        """
        The Address record a submitted one-line address should link to: the
        existing record when it already describes the same location (so
        re-submitting an unchanged profile creates no duplicate records),
        otherwise a newly saved one. Never a record belonging to someone
        else — each address role links one-to-one to its own Address record.
        """
        candidate = Address.from_one_line(one_line)
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
            for input_block in self.page.inputs:
                person_field_name = input_block.value["field"]
                if _is_address_person_field(person_field_name):
                    if person_field_name != HOME_ADDRESS_FIELD:
                        self._save_other_address(person, input_block, home_address)
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
        return person
