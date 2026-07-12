"""
On-demand fetching and parsing of the external RSS/Atom feeds that FeedPage
subscriptions point at.

A feed is fetched the first time a listing needs it and the parsed entries
are then held in an in-process TTL cache, so a page render costs at most one
fetch per feed URL per cache window — there is no periodic refresh task to
schedule. Fetch failures are cached as an empty result, so an unreachable
site is retried at most once per cache window instead of stalling every
render. Parsing is separated from fetching so that tests can exercise it on
feed documents without any network access.
"""

import calendar
import datetime
import logging
import threading
from dataclasses import dataclass

import feedparser
import requests
from cachetools import TTLCache, cached
from django.utils.html import strip_tags

from underground_crm.models.pages import FeedItem

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 10
USER_AGENT = "underground-crm feed fetcher"
CACHE_TTL_SECONDS = 60 * 30
# An instance subscribes to at most a handful of other organizations' feeds,
# so this bound exists only to keep a misbehaving caller from growing the
# cache without limit.
CACHE_MAX_FEEDS = 256


@dataclass(frozen=True)
class ParsedEntry:
    """One entry read out of a fetched feed document."""

    title: str
    url: str
    summary: str
    published_at: datetime.datetime | None

    def as_feed_item(self, *, source_title: str) -> FeedItem:
        return FeedItem(
            title=self.title,
            url=self.url,
            date=self.published_at,
            summary=self.summary,
            source_title=source_title,
        )


def parse_feed_content(content: bytes) -> list[ParsedEntry]:
    """Parse an RSS or Atom document into entries. Summaries are reduced to
    plain text — markup from an external site must never be rendered on our
    pages."""
    parsed = feedparser.parse(content)
    entries: list[ParsedEntry] = []
    for entry in parsed.entries:
        url = entry.get("link") or ""
        if not url:
            logger.warning("Skipping a feed entry without a link: %r", entry.get("title"))
            continue
        # feedparser normalizes both elements to UTC struct_time values.
        timestamp = entry.get("published_parsed") or entry.get("updated_parsed")
        published_at = (
            datetime.datetime.fromtimestamp(calendar.timegm(timestamp), tz=datetime.timezone.utc)
            if timestamp
            else None
        )
        entries.append(
            ParsedEntry(
                title=entry.get("title") or "",
                url=url,
                summary=strip_tags(entry.get("summary") or ""),
                published_at=published_at,
            )
        )
    return entries


def fetch_feed_content(url: str) -> bytes:
    """Fetch a feed document over HTTP, raising requests.RequestException on
    any failure. The timeout is short because this runs during a page render,
    not in a background task."""
    response = requests.get(url, timeout=FETCH_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.content


@cached(cache=TTLCache(maxsize=CACHE_MAX_FEEDS, ttl=CACHE_TTL_SECONDS), lock=threading.Lock())
def get_external_feed(url: str) -> list[ParsedEntry]:
    """The parsed entries of the feed at `url`, fetched on demand and cached
    in this process for CACHE_TTL_SECONDS. A fetch failure is logged and
    yields an empty (equally cached) result, so the listing degrades to local
    items only rather than erroring or re-fetching on every render."""
    try:
        content = fetch_feed_content(url)
    except requests.RequestException as error:
        logger.warning("Could not fetch the external feed at %s: %s", url, error)
        return []
    return parse_feed_content(content)
