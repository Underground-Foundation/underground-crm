import copy

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from colorfield.widgets import ColorWidget
from wagtail.blocks import CharBlock, ChoiceBlock, FieldBlock, StructBlock
from wagtail.snippets.blocks import SnippetChooserBlock


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


class InputBlock(SnippetChooserBlock):
    """A chooser referencing an InputField snippet, for use in FormPage's body StreamField."""

    def __init__(self, **kwargs):
        from underground_crm.models.input_field import InputField

        super().__init__(InputField, **kwargs)

    def bulk_to_python(self, values):
        # ChooserBlock.bulk_to_python() keys its lookup dict by QuerySet.in_bulk(),
        # whose keys are uuid.UUID instances for a UUIDField PK — but `values` here
        # are the raw strings straight out of the stream's JSON, so a plain dict.get()
        # never matches and every input field silently resolves to None.
        # InputField's PK (like every model in this library) is a UUID, so
        # normalise both sides to str.
        objects = {str(pk): obj for pk, obj in self.model_class.objects.in_bulk(values).items()}
        seen_ids = set()
        result = []
        for id in values:
            obj = objects.get(str(id))
            if obj is not None and id in seen_ids:
                obj = copy.copy(obj)
            result.append(obj)
            seen_ids.add(id)
        return result

    class Meta:
        icon = "tasks"
        label = "Input"
        # Rendered as an actual <input> by FormSubmissionForm/form.as_p instead —
        # this template intentionally stays blank so the field's question text
        # doesn't also print as plain, non-interactive text inline in the body.
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
