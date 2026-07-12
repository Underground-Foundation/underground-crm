import logging

from django import forms
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.validators import URLValidator
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _
from colorfield.widgets import ColorWidget
from wagtail.blocks import CharBlock, ChoiceBlock, FieldBlock, StructBlock

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

    def __init__(self, required: bool = True, max_length: int = 255, **kwargs):
        self.field = forms.CharField(
            required=required,
            max_length=max_length,
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

# Some fields are already included thanks to the use of FormPage: email address,
# first_name, last_name.
DEFAULT_REGISTRATION_PERSON_FIELDS: tuple[str, ...] = (
    "prefix",
    "preferred_name",
    "middle_name",
    "suffix",
    "date_of_birth",
    "gender",
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


def registration_person_field_names() -> list[str]:
    """
    The active whitelist of Person fields that registration forms may expose,
    with unusable entries dropped. Relational fields are excluded — their
    default form field would be a model chooser over the related table, which
    makes no sense on a public form — with one exception: links to Address
    (home_address, mailing_address, ...) are allowed, because the registration
    form renders them as free-text Addressr-backed autocomplete inputs and
    resolves the submitted string to an Address record itself (see
    RegistrationForm).
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
    instead render the Addressr-backed autocomplete input, and the submitted
    string is resolved to an Address record when the form is saved.
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
        # Blank for the same reason as the generic input blocks — see
        # _input_block_kwargs() in models/pages.py.
        template = "underground_crm/blocks/input_block.html"


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
        from django.conf import settings

        context = super().get_context(value, parent_context=parent_context)
        palette = getattr(settings, "UNDERGROUND_BUTTON_BACKGROUND_PALETTE", None) or []
        bg_color: str = value.get("background_color", "")
        bg_class = next(
            (
                f"bg-{label.lower()}"
                for hex_val, label in palette
                if hex_val.lower() == bg_color.lower()
            ),
            f"bg-[{bg_color}]",
        )
        context["background_class"] = bg_class
        return context

    class Meta:
        icon = "crosshairs"
        label = "Button"
        template = "underground_crm/blocks/button_block.html"
