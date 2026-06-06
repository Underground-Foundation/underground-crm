import logging
from underground_crm.blocks import ColorBlock
from wagtail.blocks import CharBlock, RichTextBlock, StreamBlock, StructBlock, URLBlock

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
                (
                    "color",
                    ColorBlock(
                        label="Text color",
                        default="#ffffff",
                        palette_only=False,
                        palette_setting="UNDERGROUND_EMAIL_BUTTON_TEXT_COLORS",
                    ),
                ),
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
