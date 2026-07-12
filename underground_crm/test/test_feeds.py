"""
Tests for FeedPage: the RSS/Atom feeds served by its rss/ and atom/
sub-routes, the shared selectors (BlogPost.published, EventPage.upcoming)
that those feeds and the HTML listing both rely on, and the merging of
items from external feed subscriptions into the listing.
"""

import datetime
import xml.etree.ElementTree as ET

from django.test import LiveServerTestCase, RequestFactory, TestCase
from django.utils import timezone
from django.utils.text import slugify
from wagtail.models import Page, PageViewRestriction, Site

from underground_crm.external_feeds import ParsedEntry, get_external_feed, parse_feed_content
from underground_crm.models import (
    Address,
    BlogPost,
    EventPage,
    FeedPage,
    FeedSubscription,
)
from underground_crm.models.pages import PageWithMetadata

ATOM_NAMESPACE = "{http://www.w3.org/2005/Atom}"

RSS_CONTENT_TYPE = "application/rss+xml; charset=utf-8"
ATOM_CONTENT_TYPE = "application/atom+xml; charset=utf-8"

# Publication timestamps for blog posts, chosen so the expected feed order is
# unambiguous. The concrete dates are arbitrary; only their relative order matters.
EARLIER_PUBLICATION = datetime.datetime(2026, 6, 1, 9, 0, tzinfo=datetime.timezone.utc)
LATER_PUBLICATION = datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc)

# Event offsets relative to the moment the test runs. They are on a scale of
# days so that the seconds elapsing between test setup and the feed request
# cannot flip an event between "past" and "upcoming".
DAYS_SINCE_PAST_EVENT = 7
DAYS_UNTIL_SOON_EVENT = 3
DAYS_UNTIL_LATER_EVENT = 14

# Every test event runs for the same plausible length; none of the assertions
# depend on when an event ends.
EVENT_DURATION = datetime.timedelta(hours=2)


def rss_item_titles(content: bytes) -> list[str | None]:
    return [item.findtext("title") for item in ET.fromstring(content).iter("item")]


def rss_item_descriptions(content: bytes) -> list[str | None]:
    return [item.findtext("description") for item in ET.fromstring(content).iter("item")]


def atom_entry_titles(content: bytes) -> list[str | None]:
    root = ET.fromstring(content)
    return [
        entry.findtext(f"{ATOM_NAMESPACE}title") for entry in root.iter(f"{ATOM_NAMESPACE}entry")
    ]


def home_page() -> Page:
    return Site.objects.get(is_default_site=True).root_page


def add_feed_page(*, title: str, slug: str, **kwargs) -> FeedPage:
    feed_page = FeedPage(title=title, slug=slug, **kwargs)
    home_page().add_child(instance=feed_page)
    return feed_page


def add_post(
    feed_page: FeedPage,
    *,
    title: str,
    published_at: datetime.datetime | None = None,
    live: bool = True,
    description: str = "",
) -> BlogPost:
    post = BlogPost(
        title=title,
        slug=slugify(title),
        live=live,
        first_published_at=published_at,
        search_description=description,
    )
    feed_page.add_child(instance=post)
    return post


def add_event(
    feed_page: FeedPage,
    *,
    title: str,
    start_time: datetime.datetime,
    venue: Address | None = None,
) -> EventPage:
    event = EventPage(
        title=title,
        slug=slugify(title),
        start_time=start_time,
        end_time=start_time + EVENT_DURATION,
        venue=venue,
    )
    feed_page.add_child(instance=event)
    return event


class BlogFeedTest(TestCase):
    def setUp(self) -> None:
        self.blog = add_feed_page(title="Party News", slug="party-news")

    def test_rss_feed_lists_published_posts_newest_first(self) -> None:
        add_post(self.blog, title="Campaign launch in Melbourne", published_at=EARLIER_PUBLICATION)
        add_post(self.blog, title="Housing policy update", published_at=LATER_PUBLICATION)

        response = self.client.get(f"{self.blog.url}rss/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], RSS_CONTENT_TYPE)
        self.assertEqual(
            rss_item_titles(response.content),
            ["Housing policy update", "Campaign launch in Melbourne"],
            "Feed items should be ordered by first_published_at, newest first, "
            "so subscribers see the latest post at the top.",
        )

    def test_atom_feed_lists_the_same_posts(self) -> None:
        add_post(self.blog, title="Campaign launch in Melbourne", published_at=EARLIER_PUBLICATION)

        response = self.client.get(f"{self.blog.url}atom/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], ATOM_CONTENT_TYPE)
        self.assertEqual(atom_entry_titles(response.content), ["Campaign launch in Melbourne"])

    def test_feed_omits_draft_posts(self) -> None:
        add_post(self.blog, title="Campaign launch in Melbourne", published_at=EARLIER_PUBLICATION)
        add_post(self.blog, title="Unfinished draft about preselection", live=False)

        response = self.client.get(f"{self.blog.url}rss/")

        self.assertEqual(
            rss_item_titles(response.content),
            ["Campaign launch in Melbourne"],
            "Draft (unpublished) posts must never appear in a public feed.",
        )

    def test_feed_omits_posts_restricted_to_logged_in_visitors(self) -> None:
        add_post(self.blog, title="Campaign launch in Melbourne", published_at=EARLIER_PUBLICATION)
        members_post = add_post(
            self.blog, title="Members-only strategy discussion", published_at=LATER_PUBLICATION
        )
        PageViewRestriction.objects.create(
            page=members_post, restriction_type=PageViewRestriction.LOGIN
        )

        response = self.client.get(f"{self.blog.url}rss/")

        self.assertEqual(
            rss_item_titles(response.content),
            ["Campaign launch in Melbourne"],
            "A feed is served to anonymous subscribers, so view-restricted "
            "pages must be excluded from it.",
        )

    def test_feed_is_scoped_to_its_own_index(self) -> None:
        add_post(self.blog, title="Campaign launch in Melbourne", published_at=EARLIER_PUBLICATION)
        other_blog = add_feed_page(title="Branch Newsletter", slug="branch-newsletter")
        add_post(other_blog, title="Branch barbecue wrap-up", published_at=LATER_PUBLICATION)

        response = self.client.get(f"{self.blog.url}rss/")

        self.assertEqual(
            rss_item_titles(response.content),
            ["Campaign launch in Melbourne"],
            "Each index's feed must only contain its own descendants — that is "
            "the point of serving feeds per FeedPage rather than site-wide.",
        )

    def test_rss_item_description_uses_the_search_description(self) -> None:
        add_post(
            self.blog,
            title="Campaign launch in Melbourne",
            published_at=EARLIER_PUBLICATION,
            description="We kicked off the state campaign with 200 volunteers.",
        )

        response = self.client.get(f"{self.blog.url}rss/")

        self.assertEqual(
            rss_item_descriptions(response.content),
            ["We kicked off the state campaign with 200 volunteers."],
        )

    def test_feed_response_is_publicly_cacheable_like_its_page(self) -> None:
        response = self.client.get(f"{self.blog.url}rss/")

        cache_control = response["Cache-Control"]
        self.assertIn(
            f"max-age={PageWithMetadata.DEFAULT_CACHE_TTL}",
            cache_control,
            "The feed should inherit the index page's calculated cache policy.",
        )
        self.assertIn("public", cache_control)


class BlogListingTest(TestCase):
    PAGE_SIZE = 2

    def setUp(self) -> None:
        self.blog = add_feed_page(title="Party News", slug="party-news", page_size=self.PAGE_SIZE)
        self.post_titles = [
            "Campaign launch in Melbourne",
            "Housing policy update",
            "Doorknocking weekend recap",
        ]
        for days, title in enumerate(self.post_titles):
            add_post(
                self.blog,
                title=title,
                published_at=EARLIER_PUBLICATION + datetime.timedelta(days=days),
            )

    def test_feed_page_renders_its_posts(self) -> None:
        response = self.client.get(self.blog.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Doorknocking weekend recap")

    def test_listing_is_paginated_by_page_size(self) -> None:
        request = RequestFactory().get(self.blog.url)

        first_page = self.blog.get_context(request)["feed_items"]

        self.assertEqual(
            len(first_page),
            self.PAGE_SIZE,
            "The first listing page should hold exactly page_size items; the "
            "remaining post belongs to the second page.",
        )
        expected_remainder = len(self.post_titles) - self.PAGE_SIZE
        second_page = self.blog.get_context(RequestFactory().get(self.blog.url, {"page": "2"}))[
            "feed_items"
        ]
        self.assertEqual(len(second_page), expected_remainder)


class EventFeedTest(TestCase):
    def setUp(self) -> None:
        self.reference_time = timezone.now()
        self.event_index = add_feed_page(
            title="Upcoming Events",
            slug="events",
            ordering=FeedPage.Ordering.SOONEST_FIRST,
        )

    def _add_event(self, *, title: str, starts_in_days: int, venue: Address | None = None):
        return add_event(
            self.event_index,
            title=title,
            start_time=self.reference_time + datetime.timedelta(days=starts_in_days),
            venue=venue,
        )

    def test_rss_feed_lists_upcoming_events_soonest_first(self) -> None:
        self._add_event(title="Policy workshop", starts_in_days=DAYS_UNTIL_LATER_EVENT)
        self._add_event(title="Volunteer training night", starts_in_days=DAYS_UNTIL_SOON_EVENT)
        self._add_event(title="Election night party", starts_in_days=-DAYS_SINCE_PAST_EVENT)

        response = self.client.get(f"{self.event_index.url}rss/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            rss_item_titles(response.content),
            ["Volunteer training night", "Policy workshop"],
            "The feed should list only events that have not yet started, "
            "ordered soonest first, so subscribers can plan attendance.",
        )

    def test_atom_event_feed_is_served(self) -> None:
        self._add_event(title="Volunteer training night", starts_in_days=DAYS_UNTIL_SOON_EVENT)

        response = self.client.get(f"{self.event_index.url}atom/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], ATOM_CONTENT_TYPE)
        self.assertEqual(atom_entry_titles(response.content), ["Volunteer training night"])

    def test_event_item_description_leads_with_start_time_and_venue(self) -> None:
        venue = Address.objects.create(
            line1="12 Hope Street", city="Fitzroy", state="VIC", postcode="3065"
        )
        self._add_event(
            title="Volunteer training night",
            starts_in_days=DAYS_UNTIL_SOON_EVENT,
            venue=venue,
        )

        response = self.client.get(f"{self.event_index.url}rss/")

        (description,) = rss_item_descriptions(response.content)
        self.assertIn(
            "Starts",
            description,
            "Feed readers show only title and description, so the start time "
            "must be part of the description.",
        )
        self.assertIn("12 Hope Street", description)

    def test_event_index_page_renders_upcoming_events(self) -> None:
        self._add_event(title="Volunteer training night", starts_in_days=DAYS_UNTIL_SOON_EVENT)
        self._add_event(title="Election night party", starts_in_days=-DAYS_SINCE_PAST_EVENT)

        response = self.client.get(self.event_index.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Volunteer training night")
        self.assertNotContains(
            response,
            "Election night party",
            msg_prefix="The index lists upcoming events only, so a past event " "should not appear",
        )


class UpcomingEventSelectorTest(TestCase):
    """EventPage.upcoming() with an explicit reference time, so no assertion
    here depends on the wall clock."""

    REFERENCE_TIME = datetime.datetime(2026, 7, 1, 18, 0, tzinfo=datetime.timezone.utc)

    def setUp(self) -> None:
        self.event_index = add_feed_page(title="Upcoming Events", slug="events")

    def test_event_that_started_before_the_reference_time_is_excluded(self) -> None:
        add_event(
            self.event_index,
            title="Election night party",
            start_time=self.REFERENCE_TIME - datetime.timedelta(days=DAYS_SINCE_PAST_EVENT),
        )
        upcoming_event = add_event(
            self.event_index,
            title="Volunteer training night",
            start_time=self.REFERENCE_TIME + datetime.timedelta(days=DAYS_UNTIL_SOON_EVENT),
        )

        upcoming = EventPage.upcoming(at=self.REFERENCE_TIME)

        self.assertEqual(
            list(upcoming),
            [upcoming_event],
            "An event that started before the reference time is no longer "
            "upcoming and should be excluded.",
        )

    def test_event_without_a_start_time_is_excluded(self) -> None:
        event = add_event(
            self.event_index,
            title="Date to be announced: policy retreat",
            start_time=self.REFERENCE_TIME + datetime.timedelta(days=DAYS_UNTIL_SOON_EVENT),
        )
        # A null start time cannot be set through page validation (the field
        # is required when editing), but legacy imports write it directly to
        # the database — reproduce that state the same way.
        EventPage.objects.filter(pk=event.pk).update(start_time=None)

        upcoming = EventPage.upcoming(at=self.REFERENCE_TIME)

        self.assertEqual(
            list(upcoming),
            [],
            "An event with no start time cannot be placed in a schedule, so "
            "it should not appear among upcoming events.",
        )


class ExternalFeedParsingTest(TestCase):
    """Parsing feed documents into entries, without any network access. The
    realistic document is produced by rendering another FeedPage's own RSS
    view through the test client."""

    def setUp(self) -> None:
        self.ajp_news = add_feed_page(title="AJP News", slug="ajp-news")
        add_post(
            self.ajp_news,
            title="Animal Justice Party opposes live exports",
            published_at=LATER_PUBLICATION,
            description="Our members rallied at the port on Saturday.",
        )

    def test_parsed_entries_reproduce_the_source_posts(self) -> None:
        rss_document = self.client.get(f"{self.ajp_news.url}rss/").content

        (entry,) = parse_feed_content(rss_document)

        self.assertEqual(entry.title, "Animal Justice Party opposes live exports")
        self.assertEqual(
            entry.published_at,
            LATER_PUBLICATION,
            "The entry's publication time should survive the round trip "
            "through RSS serialization and parsing.",
        )
        self.assertEqual(entry.summary, "Our members rallied at the port on Saturday.")

    def test_parse_strips_markup_from_summaries(self) -> None:
        rss_with_markup = b"""<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0"><channel>
          <title>AJP News</title><link>https://ajp.example.org/news/</link>
          <description>News</description>
          <item>
            <title>Animal Justice Party opposes live exports</title>
            <link>https://ajp.example.org/news/live-exports/</link>
            <guid>https://ajp.example.org/news/live-exports/</guid>
            <description>&lt;script&gt;alert(1)&lt;/script&gt;Rallied &lt;b&gt;at the port&lt;/b&gt;.</description>
          </item>
        </channel></rss>"""

        (entry,) = parse_feed_content(rss_with_markup)

        self.assertNotIn(
            "<script>",
            entry.summary,
            "External markup must be reduced to plain text at parse time — "
            "the summary is later rendered on our pages.",
        )
        self.assertIn("at the port", entry.summary)


class ExternalFeedSubscriptionTest(LiveServerTestCase):
    """
    End-to-end federation over real HTTP: another organization's FeedPage
    (here standing in for the Animal Justice Party's blog) serves an RSS
    document through the live test server; a subscription on our own FeedPage
    points at that URL, and rendering our listing fetches it on demand.
    """

    # A LiveServerTestCase truncates every table after each test, which would
    # discard the root page and default Site that Wagtail's migrations seed.
    # Restoring the serialized migration data keeps the later tests working.
    serialized_rollback = True

    def setUp(self) -> None:
        # The fetched-feed cache is process-wide with a thirty-minute TTL, so
        # it outlives each test; start every test with it empty.
        get_external_feed.cache_clear()
        self.our_news = add_feed_page(title="Party News", slug="party-news")
        self.ajp_news = add_feed_page(title="AJP News", slug="ajp-news")
        add_post(
            self.ajp_news,
            title="Animal Justice Party opposes live exports",
            published_at=LATER_PUBLICATION,
            description="Our members rallied at the port on Saturday.",
        )
        self.subscription = FeedSubscription.objects.create(
            page=self.our_news,
            title="Animal Justice Party",
            url=f"{self.live_server_url}{self.ajp_news.url}rss/",
        )

    def test_listing_merges_external_items_with_our_own(self) -> None:
        add_post(self.our_news, title="Housing policy update", published_at=EARLIER_PUBLICATION)

        response = self.client.get(self.our_news.url)

        self.assertContains(response, "Animal Justice Party opposes live exports")
        self.assertContains(
            response,
            "Animal Justice Party",
            msg_prefix="External items should carry their source's name so "
            "readers can tell them from our own",
        )
        self.assertContains(response, "Housing policy update")

    def test_local_source_filter_hides_external_items(self) -> None:
        add_post(self.our_news, title="Housing policy update", published_at=EARLIER_PUBLICATION)

        response = self.client.get(self.our_news.url, {"source": FeedPage.SOURCE_LOCAL})

        self.assertContains(response, "Housing policy update")
        self.assertNotContains(
            response,
            "Animal Justice Party opposes live exports",
            msg_prefix="With the local-only filter selected, subscribed "
            "items should disappear from the listing",
        )

    def test_our_syndicated_feed_never_republishes_external_items(self) -> None:
        self.assertContains(
            self.client.get(self.our_news.url),
            "Animal Justice Party opposes live exports",
            msg_prefix="Sanity check: the external item does reach the HTML "
            "listing, so its absence from the RSS output below is meaningful",
        )

        response = self.client.get(f"{self.our_news.url}rss/")

        self.assertEqual(
            rss_item_titles(response.content),
            [],
            "External items must stay out of our own RSS output; otherwise "
            "two sites subscribing to each other would loop items forever.",
        )

    def test_fetched_feed_is_cached_until_the_cache_is_cleared(self) -> None:
        self.client.get(self.our_news.url)
        add_post(
            self.ajp_news,
            title="Animal Justice Party announces Melbourne candidate",
            published_at=LATER_PUBLICATION + datetime.timedelta(days=1),
        )

        cached_response = self.client.get(self.our_news.url)

        self.assertNotContains(
            cached_response,
            "Melbourne candidate",
            msg_prefix="A post published after the feed was fetched should "
            "stay invisible until the cache window lapses — the point of the "
            "cache is that rendering does not re-fetch the remote feed",
        )
        get_external_feed.cache_clear()
        fresh_response = self.client.get(self.our_news.url)
        self.assertContains(
            fresh_response,
            "Melbourne candidate",
            msg_prefix="Once the cached entries lapse, the next render "
            "should fetch the feed again and pick up the new post",
        )

    def test_unreachable_feed_degrades_to_local_items_only(self) -> None:
        add_post(self.our_news, title="Housing policy update", published_at=EARLIER_PUBLICATION)
        # Nothing listens on TCP port 1, so the fetch fails immediately with
        # a connection error rather than waiting out a timeout.
        self.subscription.url = "http://localhost:1/news/rss/"
        self.subscription.save()

        response = self.client.get(self.our_news.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Housing policy update",
            msg_prefix="A subscription whose site is down must not break the "
            "listing of our own items",
        )


class SoonestFirstExternalItemTest(TestCase):
    """Merging external items into an events (soonest-first) FeedPage, using
    feed_items(at=...) so no assertion depends on the wall clock. The parsed
    entries are planted directly in the fetched-feed cache — exactly where a
    real fetch would have put them — so no network access is involved."""

    REFERENCE_TIME = datetime.datetime(2026, 7, 1, 18, 0, tzinfo=datetime.timezone.utc)

    def setUp(self) -> None:
        # Every test here plants entries for the same subscription URL, so
        # clear whatever an earlier test left behind.
        get_external_feed.cache_clear()
        self.event_index = add_feed_page(
            title="Upcoming Events",
            slug="events",
            ordering=FeedPage.Ordering.SOONEST_FIRST,
        )
        self.subscription = FeedSubscription.objects.create(
            page=self.event_index,
            title="Animal Justice Party",
            url="https://ajp.example.org/events/rss/",
        )

    def _plant_external_event(self, *, title: str, dated: datetime.datetime) -> None:
        """Place one parsed entry in the fetched-feed cache for this test's
        subscription URL. cachetools' @cached exposes the cache and its key
        function on the wrapped function, so the entry lands exactly where a
        real fetch would have stored it."""
        entry = ParsedEntry(
            title=title,
            url=f"https://ajp.example.org/events/{slugify(title)}/",
            summary="",
            published_at=dated,
        )
        cache_key = get_external_feed.cache_key(self.subscription.url)
        get_external_feed.cache[cache_key] = [entry]

    def test_upcoming_external_events_interleave_by_date(self) -> None:
        add_event(
            self.event_index,
            title="Volunteer training night",
            start_time=self.REFERENCE_TIME + datetime.timedelta(days=DAYS_UNTIL_LATER_EVENT),
        )
        self._plant_external_event(
            title="AJP vegan barbecue",
            dated=self.REFERENCE_TIME + datetime.timedelta(days=DAYS_UNTIL_SOON_EVENT),
        )

        items = self.event_index.feed_items(at=self.REFERENCE_TIME)

        self.assertEqual(
            [item.title for item in items],
            ["AJP vegan barbecue", "Volunteer training night"],
            "A soonest-first listing should interleave external and local "
            "events by date, whichever happens sooner shown first.",
        )

    def test_past_external_events_are_excluded_from_a_soonest_first_listing(self) -> None:
        self._plant_external_event(
            title="AJP annual general meeting",
            dated=self.REFERENCE_TIME - datetime.timedelta(days=DAYS_SINCE_PAST_EVENT),
        )

        items = self.event_index.feed_items(at=self.REFERENCE_TIME)

        self.assertEqual(
            items,
            [],
            "A soonest-first listing shows upcoming items only, so an "
            "external item dated in the past should be excluded.",
        )
