import logging
from django import forms
from colorfield.widgets import ColorWidget
from wagtail.blocks import CharBlock, FieldBlock, RichTextBlock, StreamBlock, StructBlock, URLBlock

_RICH_TEXT_FEATURES = [
    "h2",
    "h3",
    "h4",
    "bold",
    "italic",
    "link",
    "ol",
    "ul",
    "hr",
    "blockquote",
    "image",
]

logger = logging.getLogger(__name__)


class ColorBlock(FieldBlock):
    def __init__(
        self,
        default: str = "#000000",
        required: bool = True,
        palette_only: bool = True,
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


class ButtonBlock(StructBlock):
    # This is a table for emails which appears similar to a button. It gets around HTML restrictions for emails.
    text = CharBlock(label="Button text")
    url = URLBlock(label="Button URL")

    def __init__(self, local_blocks=None, **kwargs):
        from django.conf import settings

        palette = getattr(settings, "UNDERGROUND_COLOR_PALETTE", None)
        bg_default = palette[0][0] if palette else "#000000"
        super().__init__(
            local_blocks=list(local_blocks or [])
            + [
                (
                    "background_color",
                    ColorBlock(label="Background color", default=bg_default, palette_only=False),
                ),
                ("color", ColorBlock(label="Text color", default="#ffffff", palette_only=False)),
            ],
            **kwargs,
        )

    class Meta:
        icon = "crosshairs"
        label = "Button"
        template = "underground_email/blocks/button_block.html"


class HRuleBlock(StructBlock):
    def __init__(self, local_blocks=None, **kwargs):
        from django.conf import settings

        palette = getattr(settings, "UNDERGROUND_EMAIL_HRULE_COLORS", None)
        logger.info("Loaded palette %s for email hrule", palette)
        default = palette[0][0] if palette else "#cccccc"
        super().__init__(
            local_blocks=list(local_blocks or [])
            + [
                (
                    "color",
                    ColorBlock(
                        label="Color",
                        default=default,
                        palette_only=False,
                        palette_setting="UNDERGROUND_EMAIL_HRULE_COLORS",
                    ),
                ),
            ],
            **kwargs,
        )

    class Meta:
        icon = "minus"
        label = "Horizontal rule"
        template = "underground_email/blocks/hrule_block.html"


class EmailBodyBlock(StreamBlock):
    rich_text = RichTextBlock(features=_RICH_TEXT_FEATURES, label="Rich text")
    button = ButtonBlock()
    hrule = HRuleBlock()
