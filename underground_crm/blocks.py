import logging

from django import forms
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.validators import URLValidator
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _
from colorfield.widgets import ColorWidget
from wagtail.blocks import (
    BooleanBlock,
    CharBlock,
    ChoiceBlock,
    DecimalBlock,
    EmailBlock,
    FieldBlock,
    FloatBlock,
    IntegerBlock,
    StructBlock,
    StructValue,
    TextBlock,
    URLBlock,
)

from underground_crm.widgets import AddressAutocompleteInput

logger = logging.getLogger(__name__)


class ColorBlock(FieldBlock):
    def __init__(
        self,
        default: str = "#000000",
        required: bool = True,
        palette_only: bool = False,
        palette_setting: str = "UNDERGROUND_COLOR_PALETTE",
        **kwargs,
    ):
        from django.conf import settings

        palette = getattr(settings, palette_setting, None)
        attrs: dict = {}
        if palette:
            attrs["swatches"] = [hex_val for hex_val, _ in palette]
            if palette_only:
                attrs["swatches_only"] = True
        self.field = forms.CharField(
            required=required,
            widget=ColorWidget(attrs=attrs),
            initial=default,
        )
        super().__init__(default=default, **kwargs)

    def get_prep_value(self, value: str) -> str:
        return value or ""

    def value_from_form(self, value: str) -> str:
        return value or ""


def _validate_page_url(value: str) -> None:
    """Accept absolute URLs, root-relative paths (e.g. /donate), query strings (e.g. ?tab=2), and fragments (e.g. #section)."""
    if value.startswith("/") or value.startswith("?") or value.startswith("#"):
        return
    try:
        URLValidator()(value)
    except ValidationError:
        raise ValidationError(
            "Enter a valid URL, or a root-relative path starting with /, a query string starting with ?, or a fragment starting with #."
        )


class PageURLBlock(FieldBlock):
    """A URL field that accepts both absolute URLs and root-relative paths."""

    def __init__(self, required: bool = True, **kwargs):
        self.field = forms.CharField(
            required=required,
            validators=[_validate_page_url],
        )
        super().__init__(**kwargs)

    def get_prep_value(self, value: str) -> str:
        return value or ""

    def value_from_form(self, value: str) -> str:
        return value or ""


class AddressBlock(FieldBlock):
    """
    A free-text address input with autocomplete backed by the local Addressr
    container: once the visitor has typed enough characters, matching
    Australian addresses are suggested (see AddressAutocompleteInput).

    The value is stored as the plain single-line address string, exactly as
    Addressr formats it — the block does not create an Address record.
    """

    def __init__(
        self,
        required: bool = True,
        max_length: int = 255,
        help_text: str = "",
        **kwargs,
    ):
        self.field = forms.CharField(
            required=required,
            max_length=max_length,
            help_text=help_text,
            widget=AddressAutocompleteInput(),
        )
        super().__init__(**kwargs)

    def get_prep_value(self, value: str) -> str:
        return value or ""

    def value_from_form(self, value: str) -> str:
        return value or ""


# Person fields that a RegistrationPage may ask visitors to complete. These can be
# overridden as UNDERGROUND_REGISTRATION_PERSON_FIELDS.
#
# Using a whitelist prevents the inadvertent inclusion of new fields on the page.
#
# first_name and last_name are listed here even though FormSubmissionForm also
# declares fields of those names, because it declares them only to identify an
# anonymous visitor and deletes them again for an authenticated one. A
# registration page requires a login, so those identity fields never reach the
# visitors this page actually serves: without a block of its own, a naming field
# would be one that nobody could ever see or edit. email is the exception that
# genuinely does come from FormSubmissionForm — it identifies the visitor rather
# than describing them, and an authenticated visitor's address is already known.
DEFAULT_REGISTRATION_PERSON_FIELDS: tuple[str, ...] = (
    "prefix",
    "first_name",
    "preferred_name",
    "middle_name",
    "last_name",
    "suffix",
    "date_of_birth",
    "phone_number",
    "mobile_number",
    "home_address",
    "mailing_address",
    "registered_address",
    "billing_address",
    "website",
    "bio",
    "is_supporter",
    "do_not_call",
    "do_not_contact",
    "federal_district",
    "state_upper_district",
    "state_lower_district",
    "council_district",
    "ward",
    "email_opt_in",
    "mobile_opt_in",
)


# HTML autocomplete tokens (the WHATWG autofill field names — see
# https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/autocomplete
# for the full, authoritative list) for the Person fields that have a
# well-defined one. first_name/last_name/email are included even though they
# are not in DEFAULT_REGISTRATION_PERSON_FIELDS, because FormSubmissionForm
# renders them directly rather than through a PersonFieldBlock. gender is
# deliberately absent: this model's field is free text for addressing people
# in gendered languages (see its help_text), not the "sex" autofill hint's
# meaning, so filling it from a browser's stored biological-sex value would
# be wrong. Fields with no sensible token (bio, is_supporter, do_not_call,
# the district/ward fields, ...) are also absent, so autocomplete is left
# unset for them.
PERSON_FIELD_AUTOCOMPLETE: dict[str, str] = {
    "first_name": "given-name",
    "last_name": "family-name",
    "email": "email",
    "prefix": "honorific-prefix",
    "preferred_name": "nickname",
    "middle_name": "additional-name",
    "suffix": "honorific-suffix",
    "date_of_birth": "bday",
    "phone_number": "tel",
    "mobile_number": "tel",
    "website": "url",
}


# The Person fields that together name a person, in reading order, each mapped
# to the Bootstrap column class setting its share of the naming grid (see
# templates/underground_crm/includes/person_naming.html). They are laid out as
# one grid rather than one field per row because they are short and read as a
# single unit — stacking them vertically wastes the page and hides how they
# relate to each other.
#
# Membership of this mapping is what makes a field a naming field, so a page
# that carries no block for one of them (an editor may delete any of them)
# simply renders a narrower grid; the widths do not have to add up.
PERSON_NAME_FIELD_COLUMNS: dict[str, str] = {
    "prefix": "col-4 col-md-2",
    "first_name": "col-8 col-md-5",
    "preferred_name": "col-12 col-md-5",
    "middle_name": "col-12 col-md-4",
    "last_name": "col-8 col-md-6",
    "suffix": "col-4 col-md-2",
}


def registration_person_field_names() -> list[str]:
    """
    The active whitelist of Person fields that registration forms may expose,
    with unusable entries dropped. Relational fields are excluded — their
    default form field would be a model chooser over the related table, which
    makes no sense on a public form — with one exception: links to Address
    (home_address, mailing_address, ...) are allowed, because the registration
    form renders them as structured component inputs with an Addressr-backed
    autocomplete on the first line, and resolves the submission to an Address
    record itself (see RegistrationForm).
    """
    from django.conf import settings
    from django.contrib.auth import get_user_model

    from underground_crm.models.address import Address

    person_model = get_user_model()
    configured: tuple[str, ...] = getattr(
        settings, "UNDERGROUND_REGISTRATION_PERSON_FIELDS", DEFAULT_REGISTRATION_PERSON_FIELDS
    )
    usable: list[str] = []
    for name in configured:
        try:
            field = person_model._meta.get_field(name)
        except FieldDoesNotExist:
            logger.warning(
                "UNDERGROUND_REGISTRATION_PERSON_FIELDS names %r, which is not a Person field.",
                name,
            )
            continue
        if field.is_relation and field.related_model is not Address:
            logger.warning(
                "UNDERGROUND_REGISTRATION_PERSON_FIELDS names the relational field %r, "
                "which cannot be exposed on a public form.",
                name,
            )
            continue
        usable.append(name)
    return usable


def registration_person_field_choices() -> list[tuple[str, str]]:
    """ChoiceBlock choices for PersonFieldBlock, labelled by each field's verbose name."""
    from django.contrib.auth import get_user_model

    person_model = get_user_model()
    return [
        (name, capfirst(person_model._meta.get_field(name).verbose_name))
        for name in registration_person_field_names()
    ]


def default_registration_body() -> list[tuple[str, dict[str, str]]]:
    """
    The initial body of a newly created RegistrationPage: one Person-field
    block per whitelisted field. Staff start from the full whitelist and
    delete the blocks they do not want, rather than assembling the form
    field by field.
    """
    return [
        ("person_field", {"field": name, "label_override": ""})
        for name in registration_person_field_names()
    ]


class PersonFieldBlock(StructBlock):
    """
    A form input on a RegistrationPage that reads and writes one field of the
    submitting visitor's Person record. The visitor-facing form field is
    derived from the model field itself (via Django's ModelForm machinery —
    see RegistrationForm._field_for_input), so a DateField renders a date
    input, a BooleanField a checkbox, and so on, without a hand-written
    mapping. Fields linking to Address (home_address, mailing_address, ...)
    instead render the structured address inputs (address lines, suburb,
    state, postcode) with an Addressr-backed autocomplete on the first line,
    and the submission is resolved to an Address record when the form is
    saved.
    """

    field = ChoiceBlock(
        choices=registration_person_field_choices,
        label=_("Person field"),
        help_text=_("Which detail of the visitor's record this input reads and updates."),
    )
    label_override = CharBlock(
        required=False,
        label=_("Label override"),
        help_text=_("Shown to the visitor instead of the field's default label."),
    )

    class Meta:
        icon = "user"
        label = _("Person field")
        group = _("Form inputs")
        # This template is empty, for the same reason as FormInputBlock's
        # (see its Meta): the block's stored value (the chosen Person
        # field and label override) must not print as inert text in the
        # page body — the actual <input> is rendered separately by
        # FormSubmissionForm.
        template = "underground_crm/blocks/input_block.html"


# The longest label SubmittedField can record (see its label field), so that a
# question accepted by the editor can always be snapshotted with its answer.
FORM_INPUT_LABEL_MAX_LENGTH = 255


class FormInputBlock(StructBlock):
    """
    A visitor input on a FormPage: the question the visitor sees (label),
    optional guidance beneath it (help_text) and whether it must be answered
    (required). Each subclass adds whatever else its kind of input needs and
    builds the visitor's form field from the block's value (see
    visitor_field), which is all FormSubmissionForm asks of an input block.
    """

    label = CharBlock(
        max_length=FORM_INPUT_LABEL_MAX_LENGTH,
        label=_("Label"),
        help_text=_("The question shown to the visitor beside this input."),
    )
    help_text = CharBlock(
        required=False,
        label=_("Help text"),
        help_text=_("Optional guidance shown to the visitor beneath the input."),
    )
    required = BooleanBlock(
        required=False,
        label=_("Required"),
        help_text=_("The visitor must answer before the form can be submitted."),
    )

    class Meta:
        group = _("Form inputs")
        # This template is empty. These blocks live in a page's body
        # StreamField, and Wagtail would otherwise render each one's stored
        # value (its label, choices and so on) as plain, non-interactive text
        # wherever that body is displayed. The actual <input> element is
        # rendered separately by FormSubmissionForm, so the StreamField's own
        # rendering must stay empty to avoid printing the question twice.
        template = "underground_crm/blocks/input_block.html"

    def visitor_field(self, value: StructValue) -> forms.Field:
        """The form field a visitor answers this input with."""
        field = self.unlabelled_field(value)
        field.label = value["label"]
        field.help_text = value["help_text"]
        field.required = value["required"]
        return field

    def unlabelled_field(self, value: StructValue) -> forms.Field:
        """The form field for this kind of input, before visitor_field gives it
        the editor's label, help text and required setting."""
        raise NotImplementedError


def required_block(help_text: str) -> BooleanBlock:
    """A replacement for FormInputBlock's "required" child, explaining what
    being required means for one particular kind of input."""
    return BooleanBlock(required=False, label=_("Required"), help_text=help_text)


class CheckboxInputBlock(FormInputBlock):
    """A single box for the visitor to tick, such as a consent to be contacted."""

    required = required_block(_("The visitor must tick the box."))
    default = BooleanBlock(required=False, label=_("Ticked by default"))

    def unlabelled_field(self, value: StructValue) -> forms.Field:
        return forms.BooleanField(initial=value["default"])


class PlaceholderInputBlock(FormInputBlock):
    """
    An input the visitor types into, which may show the editor's example
    answer as placeholder text while it is empty. Each subclass names, as
    field_block_class, the Wagtail FieldBlock whose Django form field (and
    therefore whose validation and widget) the visitor answers with. It is a
    class attribute rather than a child block because it belongs to the kind
    of input, not to any one page's value.
    """

    field_block_class: type[FieldBlock]

    placeholder = CharBlock(
        required=False,
        label=_("Placeholder"),
        help_text=_("Faint example text which disappears upon the user typing."),
    )

    def unlabelled_field(self, value: StructValue) -> forms.Field:
        # A fresh FieldBlock each time, so that one visitor's field (and its
        # widget's attributes) is never shared with another's.
        field = self.field_block_class(required=False).field
        if value["placeholder"]:
            field.widget.attrs["placeholder"] = value["placeholder"]
        return field


class TextInputBlock(PlaceholderInputBlock):
    field_block_class = CharBlock


class MultilineTextInputBlock(PlaceholderInputBlock):
    field_block_class = TextBlock


class EmailInputBlock(PlaceholderInputBlock):
    field_block_class = EmailBlock


class AddressInputBlock(PlaceholderInputBlock):
    field_block_class = AddressBlock


class IntegerInputBlock(PlaceholderInputBlock):
    field_block_class = IntegerBlock


class DecimalInputBlock(PlaceholderInputBlock):
    field_block_class = DecimalBlock


class FloatInputBlock(PlaceholderInputBlock):
    field_block_class = FloatBlock


class URLInputBlock(PlaceholderInputBlock):
    field_block_class = URLBlock


# The formats in which the browser's native pickers (below) exchange values
# with the server, whatever format they display to the visitor.
NATIVE_DATE_FORMAT = "%Y-%m-%d"
NATIVE_TIME_FORMATS = ["%H:%M", "%H:%M:%S"]
NATIVE_DATETIME_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]


# The date and time inputs use the browser's own pickers rather than the
# widgets of Wagtail's DateBlock, TimeBlock and DateTimeBlock. Those are built
# for the CMS editor and depend on its JavaScript bundle and its wagtailConfig
# global, neither of which a public page loads, so a visitor would get a bare
# text box. The native pickers need no JavaScript, suit phones, and display
# dates in the visitor's own locale. Being pickers, they have no placeholder.


class DateInputBlock(FormInputBlock):
    def unlabelled_field(self, value: StructValue) -> forms.Field:
        return forms.DateField(
            input_formats=[NATIVE_DATE_FORMAT],
            widget=forms.DateInput(attrs={"type": "date"}, format=NATIVE_DATE_FORMAT),
        )


class TimeInputBlock(FormInputBlock):
    def unlabelled_field(self, value: StructValue) -> forms.Field:
        return forms.TimeField(
            input_formats=NATIVE_TIME_FORMATS,
            widget=forms.TimeInput(attrs={"type": "time"}, format=NATIVE_TIME_FORMATS[0]),
        )


class DateTimeInputBlock(FormInputBlock):
    def unlabelled_field(self, value: StructValue) -> forms.Field:
        return forms.DateTimeField(
            input_formats=NATIVE_DATETIME_FORMATS,
            widget=forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format=NATIVE_DATETIME_FORMATS[0]
            ),
        )


def choices_in(value: StructValue) -> list[str]:
    """
    The choices an editor listed, one per line: blank lines are dropped,
    and a choice listed twice is offered once. Each choice is its own value
    as well as its caption, so SubmittedField records the answer in the
    words the visitor saw.
    """
    lines = (line.strip() for line in value["choices"].splitlines())
    return list(dict.fromkeys(line for line in lines if line))


# Separates the choices of a multiple-choice answer in SubmittedField.value.
# A choice is one line of the editor's list, so it can never contain one.
CHOICE_SEPARATOR = "\n"


class ChoiceInputBlock(FormInputBlock):
    """An input answered by picking from choices the editor lists."""

    choices = TextBlock(
        label=_("Choices"),
        help_text=_("One choice per line, in the order the visitor should see them."),
    )

    def choice_pairs(self, value: StructValue) -> list[tuple[str, str]]:
        return [(choice, choice) for choice in choices_in(value)]


class RadioButtonsInputBlock(ChoiceInputBlock):
    """Exactly one of the choices, picked from a set of radio buttons."""

    required = required_block(_("The visitor must pick one of the choices."))

    def unlabelled_field(self, value: StructValue) -> forms.Field:
        return forms.ChoiceField(choices=self.choice_pairs(value), widget=forms.RadioSelect)


class DropdownInputBlock(ChoiceInputBlock):
    """Exactly one of the choices, picked from a drop-down list."""

    required = required_block(_("The visitor must pick one of the choices."))

    def unlabelled_field(self, value: StructValue) -> forms.Field:
        # The list starts on a blank prompt rather than on the first choice,
        # so that a visitor who skips the question is not recorded as having
        # picked it.
        return forms.ChoiceField(
            choices=[("", _("Choose one"))] + self.choice_pairs(value),
            widget=forms.Select,
        )


class CheckboxesInputBlock(ChoiceInputBlock):
    """Any number of the choices, each ticked in a checkbox of its own."""

    required = required_block(_("The visitor must tick at least one of the boxes."))

    def unlabelled_field(self, value: StructValue) -> forms.Field:
        return forms.MultipleChoiceField(
            choices=self.choice_pairs(value), widget=forms.CheckboxSelectMultiple
        )


def button_background_class(color: str) -> str:
    """
    The CSS class for a button whose background is `color`: `bg-<label>` for
    the entry of UNDERGROUND_BUTTON_BACKGROUND_PALETTE that has that hex value,
    or an arbitrary-value class `bg-[<color>]` for a color outside the palette.
    """
    from django.conf import settings

    palette = getattr(settings, "UNDERGROUND_BUTTON_BACKGROUND_PALETTE", None) or []
    return next(
        (f"bg-{label.lower()}" for hex_val, label in palette if hex_val.lower() == color.lower()),
        f"bg-[{color}]",
    )


class ButtonBlock(StructBlock):
    text = CharBlock(label="Button text")
    url = PageURLBlock(label="Button URL")

    def __init__(self, local_blocks=None, **kwargs):
        from django.conf import settings

        palette = getattr(settings, "UNDERGROUND_BUTTON_BACKGROUND_PALETTE", None)
        bg_default = palette[0][0] if palette else "#000000"
        super().__init__(
            local_blocks=list(local_blocks or [])
            + [
                (
                    "background_color",
                    ColorBlock(
                        label="Background color",
                        default=bg_default,
                        palette_only=False,
                        palette_setting="UNDERGROUND_BUTTON_BACKGROUND_PALETTE",
                    ),
                ),
                (
                    "width",
                    ChoiceBlock(
                        choices=[
                            ("", "Inherit (default)"),
                            ("w-50", "Half width"),
                            ("w-100", "Full width"),
                        ],
                        default="",
                        required=False,
                        label="Width",
                    ),
                ),
            ],
            **kwargs,
        )

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context=parent_context)
        context["background_class"] = button_background_class(value.get("background_color", ""))
        return context

    class Meta:
        icon = "crosshairs"
        label = "Button"
        template = "underground_crm/blocks/button_block.html"
