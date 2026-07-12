import dataclasses
import datetime
import logging
import uuid

from django.conf import settings
from django.contrib.syndication.views import Feed
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.urls import resolve, Resolver404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.cache import patch_cache_control
from django.utils.html import strip_tags
from django.utils.text import Truncator
from wagtail.contrib.routable_page.models import RoutablePageMixin, route
from wagtail.models import Page, PageViewRestriction
from wagtail.fields import StreamField
from wagtail.query import PageQuerySet
from wagtail.blocks import (
    BooleanBlock,
    CharBlock,
    DateBlock,
    DateTimeBlock,
    DecimalBlock,
    EmailBlock,
    FloatBlock,
    IntegerBlock,
    RichTextBlock,
    RawHTMLBlock,
    BlockQuoteBlock,
    StructBlock,
    ChoiceBlock,
    TextBlock,
    TimeBlock,
    URLBlock,
)
from modelcluster.contrib.taggit import ClusterTaggableManager
from modelcluster.fields import ParentalKey
from taggit.models import TaggedItemBase
from underground_crm.blocks import (
    AddressBlock,
    ButtonBlock,
    PersonFieldBlock,
    default_registration_body,
    registration_person_field_names,
)
from wagtail.images.blocks import ImageChooserBlock
from wagtail.admin.panels import FieldPanel, InlinePanel, ObjectList, TabbedInterface
from wagtail.admin.forms import WagtailAdminPageForm
from .address import Address
from .form_submission import FormSubmission
from .person import Tag
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

    og_type_override = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name=_("Open Graph type override"),
        help_text=_(
            "Override the 'og:type' meta value (see https://ogp.me/#types for valid "
            "values). Leave blank to use this page type's default."
        ),
    )

    @property
    def og_type(self) -> str:
        return self.og_type_override or "website"

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

    def _apply_cache_control(self, response: HttpResponse) -> HttpResponse:
        """Stamp the page's calculated cache policy onto any response derived
        from this page — the page itself, or a sub-route such as a feed."""
        ttl = self.cache_time
        if ttl > 0:
            patch_cache_control(response, max_age=ttl, s_maxage=ttl, public=True)
        else:
            patch_cache_control(response, no_store=True)
        return response

    def serve(self, request, *args, **kwargs):
        return self._apply_cache_control(super().serve(request, *args, **kwargs))

    SUMMARY_WORD_LIMIT: int = 60

    @property
    def summary(self) -> str:
        """A plain-text summary of the page: the meta description if the
        editor wrote one, otherwise the opening words of the body. Used for
        feed item descriptions and index-page listings."""
        if self.search_description:
            return self.search_description
        body = getattr(self, "body", None)
        if body is None:
            return ""
        return Truncator(strip_tags(str(body))).words(self.SUMMARY_WORD_LIMIT)

    promote_panels = [
        FieldPanel("slug"),
        FieldPanel("seo_title", heading="og:title"),
        FieldPanel("search_description", heading="og:description"),
        FieldPanel("search_image", heading="og:image"),
        FieldPanel("og_type_override", heading="og:type"),
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


BASIC_PAGE_BLOCKS = [
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
    ("button", ButtonBlock()),
]


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
        BASIC_PAGE_BLOCKS,
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


def _input_block_kwargs(**extra) -> dict:
    """
    Shared configuration for the visitor-input blocks below. required=False
    keeps both sides optional: the editor may leave the block's value (the
    input's pre-filled default) blank, and the visitor may leave the rendered
    form field blank. The blank in-place template stops the block's default
    value from also printing as plain, non-interactive text inline in the
    body — the actual <input> is rendered by FormSubmissionForm/form.as_p.
    """
    return {
        "required": False,
        "template": "underground_crm/blocks/input_block.html",
        "group": _("Form inputs"),
        **extra,
    }


# One entry per built-in Wagtail FieldBlock that makes sense as a visitor
# input, plus this library's own AddressBlock (an Addressr-backed autocomplete
# input). The remaining FieldBlock variations are deliberately absent:
# RichTextBlock/RawHTMLBlock/BlockQuoteBlock are already content blocks in
# BASIC_PAGE_BLOCKS; chooser and embed blocks aren't form inputs; and
# ChoiceBlock/MultipleChoiceBlock/RegexBlock need their choices/pattern
# supplied in code, so they only appear on pages that define specific named
# inputs (e.g. RegistrationPage's Person-field blocks).
FORM_INPUT_BLOCKS = [
    ("checkbox", BooleanBlock(**_input_block_kwargs(label=_("Checkbox"), icon="tick-inverse"))),
    ("text", CharBlock(**_input_block_kwargs(label=_("Text"), icon="pilcrow"))),
    ("multiline_text", TextBlock(**_input_block_kwargs(label=_("Multi-line text")))),
    ("email", EmailBlock(**_input_block_kwargs(label=_("Email"), icon="mail"))),
    ("address", AddressBlock(**_input_block_kwargs(label=_("Address"), icon="site"))),
    ("integer", IntegerBlock(**_input_block_kwargs(label=_("Integer")))),
    ("decimal", DecimalBlock(**_input_block_kwargs(label=_("Decimal")))),
    ("float", FloatBlock(**_input_block_kwargs(label=_("Float")))),
    ("url", URLBlock(**_input_block_kwargs(label=_("URL"), icon="link"))),
    ("date", DateBlock(**_input_block_kwargs(label=_("Date"), icon="date"))),
    ("time", TimeBlock(**_input_block_kwargs(label=_("Time"), icon="time"))),
    ("datetime", DateTimeBlock(**_input_block_kwargs(label=_("Date and time"), icon="date"))),
]

FORM_INPUT_BLOCK_NAMES = frozenset(name for name, _block in FORM_INPUT_BLOCKS)

FORM_PAGE_BLOCKS = BASIC_PAGE_BLOCKS + FORM_INPUT_BLOCKS


class FormServingPage(PageWithMetadata):
    """
    Abstract machinery shared by pages that serve a visitor form built from
    input blocks placed in their body (FormPage, RegistrationPage). Concrete
    subclasses each declare their own body StreamField — Django's multi-table
    inheritance doesn't allow a subclass to redeclare an inherited concrete
    field, so the block set has to be fixed per concrete page — plus an
    `inputs` property selecting the body blocks that become form fields, and
    optionally a different form class.
    """

    class Meta:
        abstract = True

    @property
    def inputs(self):
        """The body's input blocks, as bound blocks (each carries the stream
        child's stable .id plus .block_type, .block, and .value)."""
        raise NotImplementedError

    def get_submission_form_class(self):
        """Overridable hook so subclasses (e.g. EventPage, RegistrationPage)
        can swap in a form that processes the submission differently."""
        from underground_crm.forms.form_submission import FormSubmissionForm

        return FormSubmissionForm

    def get_context(self, request, *args, **kwargs):
        form_class = self.get_submission_form_class()

        context = super().get_context(request, *args, **kwargs)
        submitted = False
        if request.method == "POST":
            form = form_class(request.POST, request=request, page=self)
            if form.is_valid():
                form.save()
                submitted = True
                form = form_class(request=request, page=self)
        else:
            form = form_class(request=request, page=self)
        context["form"] = form
        context["submitted"] = submitted
        return context


class FormPage(FormServingPage):
    """
    A page built on StreamField that can host a form. Extends BasicPage's
    block set with one block per built-in Wagtail FieldBlock variation
    (checkbox, text, date, ...), so editors can interleave questions with
    ordinary rich content. The value an editor types into an input block is
    the pre-filled default shown to visitors (usually left blank); the
    question text itself is authored as ordinary content around the input.

    Not a BasicPage subclass: BasicPage's body field is fixed to
    BASIC_PAGE_BLOCKS (see FormServingPage on why the block set is fixed per
    concrete page), so FormPage mirrors BasicPage's definition instead.

    On a valid submission, a FormSubmission is recorded (see
    underground_crm.forms.form_submission.FormSubmissionForm for the actual
    logic). For authenticated submissions only, tags_to_apply are added to
    the submitting Person — unauthenticated submissions are never allowed to
    modify a Person record, verified or otherwise.
    """

    is_creatable = True

    body = StreamField(
        FORM_PAGE_BLOCKS,
        use_json_field=True,
        blank=True,
    )
    tags_to_apply = ClusterTaggableManager(
        through="FormPageTag",
        blank=True,
        related_name="form_pages",
        verbose_name=_("Tags to apply on submission"),
        help_text=_("Applied to the submitting Person's record. Authenticated submissions only."),
    )

    content_panels = Page.content_panels + [
        FieldPanel("body"),
        FieldPanel("tags_to_apply"),
    ]

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(PageWithMetadata.promote_panels, heading=_("Metadata")),
            ObjectList(PageWithMetadata.visibility_panels, heading=_("Visibility")),
        ]
    )

    class Meta:
        verbose_name = _("Form Page")

    @property
    def inputs(self):
        return [
            block
            for block in self.body  # pylint: disable=not-an-iterable
            if block.block_type in FORM_INPUT_BLOCK_NAMES
        ]


class FormPageTag(TaggedItemBase):
    """Explicit through model for FormPage.tags_to_apply, carrying a UUID PK for federation support."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content_object = ParentalKey(FormPage, on_delete=models.CASCADE, related_name="tagged_items")
    tag = models.ForeignKey(Tag, related_name="tagged_form_pages", on_delete=models.CASCADE)

    class Meta:
        unique_together = [("content_object", "tag")]


# RegistrationPage forms are made of Person-field blocks only — the generic
# FORM_INPUT_BLOCKS are absent because their answers would have nowhere to go:
# a registration submission updates the Person record instead of creating a
# FormSubmission, so free-form questions belong on a FormPage.
REGISTRATION_PAGE_BLOCKS = BASIC_PAGE_BLOCKS + [
    ("person_field", PersonFieldBlock()),
]


class RegistrationPage(FormServingPage):
    """
    A page whose form reads and writes the submitting visitor's own Person
    record, instead of recording a FormSubmission. A newly created page starts
    with one Person-field block per whitelisted field (see
    default_registration_body() in blocks.py); editors delete the details they
    do not want to collect, reorder the rest, and interleave them with ordinary
    content. Which Person fields may be exposed at all is governed by the
    whitelist (registration_person_field_names() in blocks.py), applied at
    render time so that removing a field from the whitelist retroactively
    disables it on already-published pages.

    Authenticated visitors see their current values pre-filled and update
    their own record. Anonymous visitors can only register — a new Person is
    created for an unknown email address, while an existing one is rejected
    with a prompt to log in, because an unauthenticated submission is never
    allowed to modify an existing Person record (the same stance as
    FormSubmissionForm, held more strictly here since profile fields are
    written rather than just tags).
    """

    is_creatable = True

    body = StreamField(
        REGISTRATION_PAGE_BLOCKS,
        use_json_field=True,
        blank=True,
        default=default_registration_body,
    )
    tags_to_apply = ClusterTaggableManager(
        through="RegistrationPageTag",
        blank=True,
        related_name="registration_pages",
        verbose_name=_("Tags to apply on registration"),
        help_text=_("Applied to the registering Person's record."),
    )

    content_panels = Page.content_panels + [
        FieldPanel("body"),
        FieldPanel("tags_to_apply"),
    ]

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(PageWithMetadata.promote_panels, heading=_("Metadata")),
            ObjectList(PageWithMetadata.visibility_panels, heading=_("Visibility")),
        ]
    )

    class Meta:
        verbose_name = _("Registration Page")

    @property
    def inputs(self):
        allowed = set(registration_person_field_names())
        return [
            block
            for block in self.body  # pylint: disable=not-an-iterable
            if block.block_type == "person_field" and block.value["field"] in allowed
        ]

    def get_submission_form_class(self):
        from underground_crm.forms.registration import RegistrationForm

        return RegistrationForm


class RegistrationPageTag(TaggedItemBase):
    """Explicit through model for RegistrationPage.tags_to_apply, carrying a UUID PK for federation support."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content_object = ParentalKey(
        RegistrationPage, on_delete=models.CASCADE, related_name="tagged_items"
    )
    tag = models.ForeignKey(Tag, related_name="tagged_registration_pages", on_delete=models.CASCADE)

    class Meta:
        unique_together = [("content_object", "tag")]


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


class FeedRoutedIndexMixin(RoutablePageMixin):
    """
    Adds `rss/` and `atom/` sub-routes to an index page, serving syndication
    feeds of the page's children. Concrete pages provide their feed classes
    via get_rss_feed_class() / get_atom_feed_class(), importing them lazily
    because underground_crm.feeds imports the page models from this module.
    """

    def get_rss_feed_class(self) -> type[Feed]:
        raise NotImplementedError

    def get_atom_feed_class(self) -> type[Feed]:
        raise NotImplementedError

    @route(r"^$")
    def index_route(self, request: HttpRequest) -> HttpResponse:
        """Serve the page itself through the ordinary Page.serve() path.

        RoutablePageMixin's default index route renders the template directly,
        which would bypass PageWithMetadata.serve() and lose the cache-control
        headers it applies.
        """
        return self.serve(request)

    @route(r"^rss/$", name="rss_feed")
    def rss_feed_route(self, request: HttpRequest) -> HttpResponse:
        return self._serve_feed(request, self.get_rss_feed_class())

    @route(r"^atom/$", name="atom_feed")
    def atom_feed_route(self, request: HttpRequest) -> HttpResponse:
        return self._serve_feed(request, self.get_atom_feed_class())

    def _serve_feed(self, request: HttpRequest, feed_class: type[Feed]) -> HttpResponse:
        """Feed instances are Django views; calling one renders the feed.
        A fresh instance is created per request, so the Feed machinery never
        shares per-request state between visitors."""
        return self._apply_cache_control(feed_class()(request, index_page=self))


_UTC = datetime.timezone.utc
_DISTANT_PAST = datetime.datetime.min.replace(tzinfo=_UTC)
_DISTANT_FUTURE = datetime.datetime.max.replace(tzinfo=_UTC)


def feed_item_date(page: Page) -> datetime.datetime | None:
    """The date a feed presents for a page: an event's start time, any other
    page's first publication time."""
    if isinstance(page, EventPage):
        return page.start_time
    return page.first_published_at


@dataclasses.dataclass(frozen=True)
class FeedItem:
    """
    One entry in a FeedPage listing — either a local page or an item fetched
    from an external feed subscription (`page` is None for the latter).
    """

    title: str
    url: str
    date: datetime.datetime | None
    summary: str
    # The subscribed organization's name; empty for local items.
    source_title: str
    page: Page | None = None

    @property
    def is_external(self) -> bool:
        return self.page is None

    @classmethod
    def from_page(cls, page: PageWithMetadata) -> "FeedItem":
        return cls(
            title=page.title,
            url=page.url,
            date=feed_item_date(page),
            summary=page.summary,
            source_title="",
            page=page,
        )


class FeedPage(FeedRoutedIndexMixin, BasicPage):
    """
    A paginated index page listing dated items: its own live descendants
    (blog posts and events), optionally merged with items fetched on demand
    from external feed subscriptions — another organization's blog or event
    calendar shown alongside our own. Serves RSS and Atom feeds of its
    local items at `rss/` and `atom/` beneath its own URL; external items
    stay out of that syndicated output (see underground_crm.feeds).

    The legacy importer maps both the "Blog" and "Calendar" legacy page
    types to this model; the two differ only in `ordering`.
    """

    is_creatable = True

    class Ordering(models.TextChoices):
        NEWEST_FIRST = "newest_first", _("Newest first (blog)")
        SOONEST_FIRST = "soonest_first", _("Soonest first, upcoming only (events)")

    SOURCE_QUERY_PARAM = "source"
    SOURCE_LOCAL = "local"

    page_size = models.PositiveIntegerField(
        default=10,
        help_text=_("Number of items to display per page."),
    )
    ordering = models.CharField(
        max_length=20,
        choices=Ordering.choices,
        default=Ordering.NEWEST_FIRST,
        help_text=_(
            "Newest first lists items by publication date, like a blog. "
            "Soonest first lists only items dated in the future, like an "
            "events calendar."
        ),
    )

    content_panels = BasicPage.content_panels + [
        FieldPanel("page_size"),
        FieldPanel("ordering"),
        InlinePanel(
            "subscriptions",
            heading=_("External feed subscriptions"),
            label=_("Subscription"),
        ),
    ]

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(PageWithMetadata.promote_panels, heading=_("Metadata")),
            ObjectList(PageWithMetadata.visibility_panels, heading=_("Visibility")),
        ]
    )

    class Meta:
        verbose_name = _("Feed Page")

    def local_item_pages(self, *, at: datetime.datetime | None = None) -> list[Page]:
        """The live, public descendant pages this feed lists, in the page's
        ordering. A soonest-first feed shows upcoming events only (posts are
        never dated in the future); a newest-first feed shows posts and any
        events together, most recent date first. `at` overrides the
        reference time for the upcoming-events cutoff."""
        if self.ordering == self.Ordering.SOONEST_FIRST:
            return list(EventPage.upcoming(within=self, at=at))
        posts = list(BlogPost.published(within=self))
        events = list(EventPage.objects.live().public().descendant_of(self))
        return sorted(
            posts + events,
            key=lambda page: feed_item_date(page) or _DISTANT_PAST,
            reverse=True,
        )

    def feed_items(
        self,
        *,
        include_external: bool = True,
        at: datetime.datetime | None = None,
    ) -> list[FeedItem]:
        """The listing's entries: local pages, merged (unless filtered out
        by the visitor) with items from this page's external feed
        subscriptions, fetched on demand through a TTL cache (see
        underground_crm.external_feeds). External items appear only in this
        HTML-facing listing, never in the syndicated RSS/Atom output."""
        from underground_crm.external_feeds import get_external_feed

        reference_time = at if at is not None else timezone.now()
        items = [FeedItem.from_page(page) for page in self.local_item_pages(at=at)]
        if include_external:
            for subscription in self.subscriptions.all():
                for entry in get_external_feed(subscription.url):
                    if self.ordering == self.Ordering.SOONEST_FIRST and (
                        entry.published_at is None or entry.published_at < reference_time
                    ):
                        continue
                    items.append(entry.as_feed_item(source_title=subscription.title))
        if self.ordering == self.Ordering.SOONEST_FIRST:
            items.sort(key=lambda item: item.date or _DISTANT_FUTURE)
        else:
            items.sort(key=lambda item: item.date or _DISTANT_PAST, reverse=True)
        return items

    def get_context(self, request, *args, **kwargs) -> dict:
        context = super().get_context(request, *args, **kwargs)
        source = request.GET.get(self.SOURCE_QUERY_PARAM, "")
        items = self.feed_items(include_external=source != self.SOURCE_LOCAL)
        context["feed_items"] = Paginator(items, self.page_size).get_page(request.GET.get("page"))
        context["source"] = source
        context["has_subscriptions"] = self.subscriptions.exists()
        site = self.get_site()
        site_name = site.site_name if site else ""
        context["site_name"] = site_name or settings.WAGTAIL_SITE_NAME
        return context

    def get_rss_feed_class(self) -> type[Feed]:
        from underground_crm.feeds import IndexPageFeed

        return IndexPageFeed

    def get_atom_feed_class(self) -> type[Feed]:
        from underground_crm.feeds import IndexPageAtomFeed

        return IndexPageAtomFeed


class BlogPost(UndergroundBasicPage):
    """
    A dated article belonging within a FeedPage index. Themes that subclass
    FeedPage should extend parent_page_types accordingly — Wagtail matches
    the listed classes exactly, not their subclasses.
    """

    parent_page_types = ["underground_crm.FeedPage"]

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

    @classmethod
    def published(cls, *, within: Page | None = None) -> PageQuerySet:
        """Live, publicly visible posts, newest first — the selection shared
        by the FeedPage HTML listing and its feeds. `within` narrows the
        result to descendants of one index page."""
        posts = cls.objects.live().public().order_by("-first_published_at")
        if within is not None:
            posts = posts.descendant_of(within)
        return posts


class EventPage(FormPage):
    """
    An event page. Extends FormPage (rather than BasicPage) so staff can add
    arbitrary extra "input" blocks to its body via the CMS, same as any
    FormPage. RSVPs are recorded as EventGuest rows (see EventGuest below) —
    a baseline "how many guests are you bringing" question is always present
    on the rendered form, on top of whatever admin-added inputs exist.

    Events are created beneath a FeedPage index (the same restriction and
    subclassing caveat as BlogPost.parent_page_types).
    """

    is_creatable = True

    parent_page_types = ["underground_crm.FeedPage"]

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

    def get_submission_form_class(self):
        from underground_crm.forms.event_guest import EventGuestForm

        return EventGuestForm

    @classmethod
    def upcoming(
        cls,
        *,
        within: Page | None = None,
        at: datetime.datetime | None = None,
    ) -> PageQuerySet:
        """Live, publicly visible events that have not yet started, soonest
        first — the selection shared by the FeedPage HTML listing and its
        feeds. Events without a start time are excluded, as they cannot
        be placed in a schedule. `within` narrows the result to descendants
        of one index page; `at` overrides the reference time so callers
        (particularly tests) can pin it instead of using the current time.
        """
        reference_time = at if at is not None else timezone.now()
        events = (
            cls.objects.live()
            .public()
            .filter(start_time__gte=reference_time)
            .order_by("start_time")
        )
        if within is not None:
            events = events.descendant_of(within)
        return events


class EventGuest(FormSubmission):
    """
    An RSVP to an EventPage. Extends FormSubmission (rather than adding a
    generic SubmittedField for guest count) so that, for a bare
    event page with no admin-added extra questions, this row alone fully
    describes the RSVP: is_authenticated/person/email_address (inherited)
    identify who's coming, and extra_guests says how many people they're
    bringing. Any admin-added extra questions on the page still produce
    ordinary SubmittedField rows against this same submission.
    """

    extra_guests = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Extra guests"),
        help_text=_("How many guests will be accompanying you?"),
    )
