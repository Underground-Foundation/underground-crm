from django.conf import settings
from django.db import models
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
from wagtail.admin.panels import FieldPanel, ObjectList, TabbedInterface
from wagtail.admin.forms import WagtailAdminPageForm
from .address import Address


class PageWithMetadataForm(WagtailAdminPageForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.fields["author"].queryset = User.objects.order_by(
            "-is_admin", "-is_staff", "first_name", "last_name"
        )


class PageWithMetadata(Page):
    """
    Abstract base class for pages that carry Open Graph metadata.

    Provides search_image and og_type fields, and a standard set of
    promote_panels covering the OG properties shared by all concrete
    page types that inherit from this class.
    """

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    search_image = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    og_type = models.CharField(
        max_length=50,
        default="article",
        help_text=(
            "The Open Graph type for this page. "
            "See https://ogp.me/#types for the full list of valid types."
        ),
    )

    base_form_class = PageWithMetadataForm

    promote_panels = [
        FieldPanel("slug"),
        FieldPanel("author", heading="Author"),
        FieldPanel("seo_title", heading="og:title"),
        FieldPanel("search_description", heading="og:description"),
        FieldPanel("search_image", heading="og:image"),
        FieldPanel("og_type", heading="og:type"),
    ]

    visibility_panels = [
        FieldPanel("show_in_menus"),
        FieldPanel("go_live_at", heading="Publication time"),
        FieldPanel("expire_at", heading="Expiration time"),
    ]

    class Meta:
        abstract = True


class BasicPage(PageWithMetadata):
    """
    A general-purpose content page built on StreamField. Supports rich text,
    raw HTML, images, and blockquotes as composable blocks.

    The Raw HTML block makes it straightforward to migrate content from
    other platforms by pasting existing markup directly.

    Marked non-creatable so that theme repos can subclass or replace it
    without editors seeing a duplicate entry in the page chooser.
    """

    is_creatable = False

    body = StreamField(
        [
            (
                "rich_text",
                RichTextBlock(
                    features=[
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

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading="Content"),
            ObjectList(PageWithMetadata.promote_panels, heading="Metadata"),
            ObjectList(PageWithMetadata.visibility_panels, heading="Visibility"),
        ]
    )

    class Meta:
        verbose_name = "Basic Page"


class UndergroundBasicPage(BasicPage):
    """
    Extends BasicPage with a table-of-contents control. Intended as a base
    class for theme-specific page types; not directly creatable.
    """

    is_creatable = False

    show_toc = models.BooleanField(
        default=False,
        help_text="Show the table-of-contents sidebar for this page.",
        verbose_name="Show table of contents",
    )
    default = True

    class Meta:
        verbose_name = "Underground Basic Page"


class EventPage(BasicPage):
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    start_time = models.DateTimeField(null=True)
    end_time = models.DateTimeField(null=True)
    venue = models.ForeignKey(Address, null=True, blank=True, on_delete=models.SET_NULL)
    capacity = models.PositiveIntegerField(null=True, blank=True)

    @property
    def is_multi_day(self):
        if self.start_time and self.end_time:
            return self.start_time.date() != self.end_time.date()
        return False


class EventGuest(models.Model):
    event_page = models.ForeignKey(EventPage, on_delete=models.DO_NOTHING)
    # The guest might sign up on the event page
    guest = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    accompanying_population = models.PositiveIntegerField(null=True, blank=True, default=0)
