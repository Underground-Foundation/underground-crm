"""
RSS and Atom feeds for FeedPage indexes.

Each feed serves the local items of one FeedPage (its live, public
descendant blog posts and events) rather than a site-wide listing, so a
site can run several independent indexes and each keeps its own feed. The
FeedPage's FeedRoutedIndexMixin routes instantiate the feed per request and
call it with the page as `index_page`; Django's Feed machinery then hands
that page to every hook below as `obj`.

Items fetched from the page's external feed subscriptions are deliberately
absent from this syndicated output: re-publishing another organization's
items would let two sites that subscribe to each other amplify items in a
loop, and anyone wanting those items can subscribe to the original feed
directly. External items appear only in the FeedPage's HTML listing.

The RSS/Atom split is only `feed_type` — everything else is shared, and the
item selection is the same FeedPage.local_item_pages() that the page's own
HTML listing paginates.
"""

import datetime
import mimetypes

from django.contrib.syndication.views import Feed
from django.http import HttpRequest
from django.utils import formats, timezone
from django.utils.feedgenerator import Atom1Feed, Enclosure
from django.utils.translation import gettext as _

from underground_crm.models.pages import (
    BlogPost,
    EventPage,
    PageWithMetadata,
    feed_item_date,
)

FEED_ITEM_LIMIT = 50


class IndexPageFeed(Feed):
    """RSS feed of one FeedPage's local items."""

    item_limit: int = FEED_ITEM_LIMIT
    search_image_rendition: str = "width-1200"

    def get_object(self, request: HttpRequest, *, index_page: PageWithMetadata) -> PageWithMetadata:
        return index_page

    def title(self, index_page: PageWithMetadata) -> str:
        return index_page.seo_title or index_page.title

    def link(self, index_page: PageWithMetadata) -> str:
        return index_page.get_full_url()

    def description(self, index_page: PageWithMetadata) -> str:
        return index_page.search_description or ""

    def items(self, index_page):
        return index_page.local_item_pages()[: self.item_limit]

    def item_title(self, page: PageWithMetadata) -> str:
        return page.seo_title or page.title

    def item_description(self, page: PageWithMetadata) -> str:
        """For events, lead with the schedule — feed readers typically show
        only the title and description, so the start time and venue must be
        part of the description rather than separate fields."""
        if not isinstance(page, EventPage):
            return page.summary
        details: list[str] = []
        if page.start_time is not None:
            local_start = timezone.localtime(page.start_time)
            details.append(
                _("Starts %(start_time)s.")
                % {"start_time": formats.date_format(local_start, "DATETIME_FORMAT")}
            )
        if page.venue is not None:
            details.append(_("Venue: %(venue)s.") % {"venue": page.venue})
        if page.summary:
            details.append(page.summary)
        return " ".join(details)

    def item_author_name(self, page: PageWithMetadata) -> str | None:
        if isinstance(page, BlogPost) and page.author:
            return page.author.full_name
        if isinstance(page, EventPage) and page.host:
            return page.host.full_name
        return None

    def item_link(self, page: PageWithMetadata) -> str:
        return page.get_full_url()

    def item_pubdate(self, page: PageWithMetadata) -> datetime.datetime | None:
        """An event is dated by its start time: subscribers — including other
        underground-crm instances aggregating this feed — need events ordered
        by when they happen, and RSS/Atom have no standard start-time
        element. Other pages are dated by first publication."""
        return feed_item_date(page)

    def item_updateddate(self, page: PageWithMetadata) -> datetime.datetime | None:
        return page.last_published_at

    def item_enclosures(self, page: PageWithMetadata) -> list[Enclosure]:
        if page.search_image is None:
            return []
        rendition = page.search_image.get_rendition(self.search_image_rendition)
        mime_type, _encoding = mimetypes.guess_type(rendition.url)
        return [
            Enclosure(
                url=rendition.full_url,
                length=str(rendition.file.size),
                mime_type=mime_type or "application/octet-stream",
            )
        ]


class IndexPageAtomFeed(IndexPageFeed):
    """Atom variant of IndexPageFeed."""

    feed_type = Atom1Feed
    subtitle = IndexPageFeed.description
