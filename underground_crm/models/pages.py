import datetime
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import resolve, Resolver404
from django.utils.translation import gettext_lazy as _
from django.utils.cache import patch_cache_control
from wagtail.models import Page, PageViewRestriction
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
from underground_crm.panels import ReadOnlyPanel

logger = logging.getLogger(__name__)


class PageWithMetadataForm(WagtailAdminPageForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model

        User = get_user_model()
        if "author" in self.fields:
            self.fields["author"].queryset = User.objects.order_by(
                "-is_admin", "-is_staff", "first_name", "last_name"
            )

    def clean(self):
        cleaned_data = super().clean()
        if "body" in cleaned_data and not getattr(self.for_user, "has_html_permission", False):
            if any(b.block_type == "html" for b in cleaned_data["body"]):
                raise ValidationError(
                    "This page contains Raw HTML blocks. "
                    "You need HTML permission to save it. "
                    "Ask an admin to remove the Raw HTML blocks or grant you permission."
                )
        return cleaned_data


class PageWithMetadata(Page):
    """
    Abstract base class for pages that carry Open Graph metadata and
    automatic cache-control headers.

    Provides search_image and og_type fields, a standard set of
    promote_panels covering the OG properties shared by all concrete
    page types that inherit from this class, and cache-time logic that
    suppresses public caching for pages that require a login.
    """

    DEFAULT_CACHE_TTL: int = 3600

    search_image = models.ForeignKey(
        "wagtailimages.Image",
        verbose_name=_("Search image"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    @property
    def og_type(self) -> str:
        return "website"

    cache_ttl_override = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Cache TTL override (seconds)"),
        help_text=_(
            "Override the automatically calculated cache duration in seconds. "
            "Setting this to 0 will disable caching entirely. "
            "Leave blank to use the calculated value."
        ),
    )

    base_form_class = PageWithMetadataForm

    @property
    def cache_time(self) -> int:
        if self.cache_ttl_override is not None:
            return self.cache_ttl_override
        return self._calculated_cache_time()

    def _calculated_cache_time(self) -> int:
        """Return the number of seconds this page may be cached by a shared cache.

        Resolves the page's own URL path to find the view that serves it, then
        checks whether authentication is required.  Returns 0 if the page must
        not be cached publicly; returns DEFAULT_CACHE_TTL otherwise.

        Subclasses can override this method to apply finer-grained rules.
        """
        url_parts = self.get_url_parts()
        if not url_parts:
            return 0
        logger.info("The URL parts for %s are %s", self.slug, url_parts)
        _, _, page_path = url_parts

        try:
            match = resolve(page_path)
        except Resolver404:
            return 0

        # Plain Django views can advertise a login requirement via this attribute.
        if getattr(match.func, "login_required", False):
            return 0

        # Wagtail pages all route through wagtail.views.serve, so check the
        # page-level privacy restrictions directly.
        for restriction in self.get_view_restrictions():
            if restriction.restriction_type in (
                PageViewRestriction.LOGIN,
                PageViewRestriction.GROUPS,
            ):
                return 0

        return self.DEFAULT_CACHE_TTL

    def serve(self, request, *args, **kwargs):
        response = super().serve(request, *args, **kwargs)
        ttl = self.cache_time
        if ttl > 0:
            patch_cache_control(response, max_age=ttl, s_maxage=ttl, public=True)
        else:
            patch_cache_control(response, no_store=True)
        return response

    promote_panels = [
        FieldPanel("slug"),
        FieldPanel("seo_title", heading="og:title"),
        FieldPanel("search_description", heading="og:description"),
        FieldPanel("search_image", heading="og:image"),
    ]

    visibility_panels = [
        FieldPanel("show_in_menus"),
        FieldPanel("go_live_at", heading=_("Publication time")),
        FieldPanel("expire_at", heading=_("Expiration time")),
        FieldPanel("cache_ttl_override"),
        ReadOnlyPanel("cache_time", heading=_("Calculated cache time (seconds)")),
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

    legacy_id = models.PositiveIntegerField(blank=True, null=True)
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
                    label=_("Rich Text"),
                ),
            ),
            (
                "html",
                RawHTMLBlock(
                    label=_("Raw HTML"),
                    help_text=_(
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
                                    ("full-width", _("Full width")),
                                    ("left", _("Left aligned")),
                                    ("right", _("Right aligned")),
                                    ("w-50", _("Half width")),
                                ],
                                default="full-width",
                            ),
                        ),
                    ],
                    icon="image",
                    label=_("Image"),
                    template="underground_crm/blocks/image_block.html",
                ),
            ),
            ("blockquote", BlockQuoteBlock(label=_("Blockquote"))),
        ],
        use_json_field=True,
        blank=True,
    )

    content_panels = Page.content_panels + [
        FieldPanel("body"),
    ]

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(PageWithMetadata.promote_panels, heading=_("Metadata")),
            ObjectList(PageWithMetadata.visibility_panels, heading=_("Visibility")),
        ]
    )

    class Meta:
        verbose_name = _("Basic Page")


class UndergroundBasicPage(BasicPage):
    """
    Extends BasicPage with a table-of-contents control.
    """

    show_toc = models.BooleanField(
        default=False,
        help_text=_("Show the table-of-contents sidebar for this page."),
        verbose_name=_("Show table of contents"),
    )
    default = True

    content_panels = BasicPage.content_panels + [
        FieldPanel("show_toc"),
    ]

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(PageWithMetadata.promote_panels, heading=_("Metadata")),
            ObjectList(PageWithMetadata.visibility_panels, heading=_("Visibility")),
        ]
    )

    class Meta:
        verbose_name = _("Basic Page")


class Blog(BasicPage):
    """
    A paginated blog index page. Inherits the StreamField body from BasicPage
    and adds a configurable page size for child post listings.
    """

    is_creatable = True

    page_size = models.PositiveIntegerField(
        default=10,
        help_text=_("Number of posts to display per page."),
    )

    content_panels = BasicPage.content_panels + [
        FieldPanel("page_size"),
    ]

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(PageWithMetadata.visibility_panels, heading=_("Visibility")),
        ]
    )

    class Meta:
        verbose_name = _("Blog")


class BlogPost(UndergroundBasicPage):
    """
    A page belonging within a Blog (although this isn't strictly enforced).
    """

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Author"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    content_panels = BasicPage.content_panels + [
        FieldPanel("author", heading=_("Author")),
    ]


class EventPage(BasicPage):
    is_creatable = True

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
    # todo: keep this in sync
    population = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_(
            "This is updated automatically. It is only defined as a field so we can migrate legacy events seamlessly."
        ),
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)

    @property
    def is_multi_day(self):
        if self.start_time and self.end_time:
            return self.start_time.date() != self.end_time.date()
        return False

    @property
    def has_started(self):
        if not self.start_time:
            return None
        return self.start_time <= datetime.datetime.utcnow()

    def __str__(self):
        return self.slug or self.title


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
