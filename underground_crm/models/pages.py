from wagtail.models import Page
from wagtail.fields import StreamField
from wagtail.blocks import (
    CharBlock,
    RichTextBlock,
    RawHTMLBlock,
    BlockQuoteBlock,
    StructBlock,
    ChoiceBlock,
)
from wagtail.images.blocks import ImageChooserBlock
from wagtail.admin.panels import FieldPanel


class BasicPage(Page):
    """
    A general-purpose content page built on StreamField. Supports rich text,
    raw HTML, images, and blockquotes as composable blocks.

    The Raw HTML block makes it straightforward to migrate content from
    other platforms by pasting existing markup directly.
    """

    body = StreamField(
        [
            (
                "rich_text",
                RichTextBlock(
                    features=[
                        "h2", "h3", "h4",
                        "bold", "italic",
                        "link", "ol", "ul",
                        "hr", "blockquote", "image",
                    ],
                    label="Rich Text",
                ),
            ),
            (
                "html",
                RawHTMLBlock(
                    label="Raw HTML",
                    help_text=(
                        "Paste raw HTML directly. "
                        "Useful for migrating existing content or embedding custom markup."
                    ),
                ),
            ),
            (
                "image",
                StructBlock(
                    [
                        ("image", ImageChooserBlock()),
                        ("caption", CharBlock(required=False)),
                        (
                            "alignment",
                            ChoiceBlock(
                                choices=[
                                    ("full-width", "Full width"),
                                    ("left", "Left aligned"),
                                    ("right", "Right aligned"),
                                    ("w-50", "Half width"),
                                ],
                                default="full-width",
                            ),
                        ),
                    ],
                    icon="image",
                    label="Image",
                    template="underground_crm/blocks/image_block.html",
                ),
            ),
            ("blockquote", BlockQuoteBlock(label="Blockquote")),
        ],
        use_json_field=True,
        blank=True,
    )

    content_panels = Page.content_panels + [
        FieldPanel("body"),
    ]

    class Meta:
        verbose_name = "Basic Page"
