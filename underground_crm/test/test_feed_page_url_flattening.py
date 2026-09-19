"""
Tests for FeedPage.show_slug_as_breadcrumb: when disabled (the default), a
feed's items are addressed directly beneath the feed's own parent, omitting
the feed's slug from their URL — the way the legacy CMS addresses blog and
calendar entries. See underground_crm/feed_routing.py for the routing side
of this, and FeedChildPageMixin.set_url_path in models/pages.py for the URL
generation side.

Routing is checked with Page.find_for_request rather than a full
self.client.get(), so these tests exercise the same tree-walking machinery a
real request goes through without depending on a downstream theme's own
page templates being complete.
"""

import datetime

from django.test import RequestFactory, TestCase
from django.utils.text import slugify
from wagtail.models import Page, Site

from underground_crm.models import BlogPost, EventPage, FeedPage

# An arbitrary but plausible event start time; only used so EventPage.save()
# has a value to validate against, never asserted on directly.
EVENT_START = datetime.datetime(2026, 11, 3, 18, 30, tzinfo=datetime.timezone.utc)
EVENT_DURATION = datetime.timedelta(hours=2)


def home_page() -> Page:
    return Site.objects.get(is_default_site=True).root_page


def add_feed_page(*, title: str, slug: str, **kwargs) -> FeedPage:
    feed_page = FeedPage(title=title, slug=slug, **kwargs)
    home_page().add_child(instance=feed_page)
    return feed_page


def add_post(feed_page: FeedPage, *, title: str) -> BlogPost:
    post = BlogPost(title=title, slug=slugify(title), live=True, first_published_at=EVENT_START)
    feed_page.add_child(instance=post)
    return post


def add_event(feed_page: FeedPage, *, title: str) -> EventPage:
    event = EventPage(
        title=title,
        slug=slugify(title),
        start_time=EVENT_START,
        end_time=EVENT_START + EVENT_DURATION,
    )
    feed_page.add_child(instance=event)
    return event


def resolve_page(path: str) -> Page | None:
    """The live page a request for `path` would be routed to, found through
    the same Page.route() tree walk a real request uses (see
    wagtail.views.serve), so this exercises the patch in feed_routing.py
    directly rather than via a full rendered response."""
    request = RequestFactory().get(path)
    resolved = Page.find_for_request(request, path)
    return resolved.specific if resolved is not None else None


class FlattenedByDefaultTest(TestCase):
    """show_slug_as_breadcrumb defaults to False, so newly created items
    should already be flattened without any explicit configuration."""

    def setUp(self) -> None:
        self.news = add_feed_page(title="Party News", slug="news")

    def test_show_slug_as_breadcrumb_defaults_to_false(self) -> None:
        self.assertFalse(self.news.show_slug_as_breadcrumb)

    def test_new_post_url_omits_the_feed_slug(self) -> None:
        post = add_post(self.news, title="Campaign launch in Melbourne")

        self.assertEqual(post.url, "/campaign-launch-in-melbourne/")

    def test_new_event_url_omits_the_feed_slug(self) -> None:
        event = add_event(self.news, title="Volunteer training night")

        self.assertEqual(event.url, "/volunteer-training-night/")

    def test_flattened_post_is_routed_to_at_its_url(self) -> None:
        post = add_post(self.news, title="Campaign launch in Melbourne")

        self.assertEqual(resolve_page(post.url), post)

    def test_flattened_event_is_routed_to_at_its_url(self) -> None:
        event = add_event(self.news, title="Volunteer training night")

        self.assertEqual(resolve_page(event.url), event)

    def test_feed_page_itself_still_keeps_its_own_slug(self) -> None:
        self.assertEqual(self.news.url, "/news/")
        response = self.client.get(self.news.url)
        self.assertEqual(response.status_code, 200)

    def test_old_style_nested_url_still_resolves_the_same_page(self) -> None:
        """
        Known limitation: flattening only adds an alternate, shorter URL —
        it does not remove the item from its real position in the page
        tree. Wagtail's routing matches a request against child slugs at
        each level regardless of a page's url_path, so the pre-flattening
        nested URL keeps resolving to the same event alongside the
        flattened one. This test documents that rather than a stricter
        (currently unimplemented) canonical-URL guarantee.
        """
        event = add_event(self.news, title="Volunteer training night")

        self.assertEqual(resolve_page("/news/volunteer-training-night/"), event)


class ShowSlugAsBreadcrumbEnabledTest(TestCase):
    """With show_slug_as_breadcrumb explicitly enabled, items nest beneath
    the feed's own URL as Wagtail would do without this feature at all."""

    def setUp(self) -> None:
        self.news = add_feed_page(title="Party News", slug="news", show_slug_as_breadcrumb=True)

    def test_new_post_url_is_nested_under_the_feed(self) -> None:
        post = add_post(self.news, title="Campaign launch in Melbourne")

        self.assertEqual(post.url, "/news/campaign-launch-in-melbourne/")

    def test_nested_event_is_routed_to_at_its_url(self) -> None:
        event = add_event(self.news, title="Volunteer training night")

        self.assertEqual(resolve_page(event.url), event)

    def test_flattened_style_url_does_not_resolve(self) -> None:
        add_event(self.news, title="Volunteer training night")

        self.assertIsNone(resolve_page("/volunteer-training-night/"))


class TogglingShowSlugAsBreadcrumbTest(TestCase):
    """Changing the flag on an existing feed must realign its existing
    children's URLs, not just apply to items created afterwards."""

    def setUp(self) -> None:
        self.news = add_feed_page(title="Party News", slug="news")
        self.event = add_event(self.news, title="Volunteer training night")

    def test_enabling_the_flag_nests_existing_children_under_the_feed(self) -> None:
        self.news.show_slug_as_breadcrumb = True
        self.news.save()

        self.event.refresh_from_db()
        self.assertEqual(self.event.url, "/news/volunteer-training-night/")
        self.assertEqual(resolve_page(self.event.url), self.event)

    def test_disabling_the_flag_flattens_existing_children_again(self) -> None:
        self.news.show_slug_as_breadcrumb = True
        self.news.save()
        self.news.show_slug_as_breadcrumb = False
        self.news.save()

        self.event.refresh_from_db()
        self.assertEqual(self.event.url, "/volunteer-training-night/")
        self.assertEqual(resolve_page(self.event.url), self.event)

    def test_saving_without_changing_the_flag_leaves_url_untouched(self) -> None:
        original_url_path = self.event.url_path

        self.news.title = "Party News and Updates"
        self.news.save()

        self.event.refresh_from_db()
        self.assertEqual(
            self.event.url_path,
            original_url_path,
            "A save that doesn't touch show_slug_as_breadcrumb shouldn't "
            "trigger a realignment pass over the feed's children.",
        )


class RenamingAFlattenedFeedTest(TestCase):
    """Since a flattened child's URL never included the feed's own slug,
    renaming the feed must not disturb its children's URLs."""

    def test_renaming_the_feed_slug_leaves_flattened_children_unchanged(self) -> None:
        news = add_feed_page(title="Party News", slug="news")
        event = add_event(news, title="Volunteer training night")

        news.slug = "media"
        news.save()

        self.assertEqual(news.url, "/media/")
        event.refresh_from_db()
        self.assertEqual(event.url, "/volunteer-training-night/")
        self.assertEqual(resolve_page(event.url), event)


class MultipleFlattenedFeedsTest(TestCase):
    """Flattened children of different feeds must still resolve to the
    correct item, since the routing fallback has to pick the right feed
    among possibly several flattened siblings."""

    def test_each_flattened_item_resolves_to_its_own_feed_item(self) -> None:
        news = add_feed_page(title="Party News", slug="news")
        social = add_feed_page(title="Social Calendar", slug="social")
        launch_event = add_event(news, title="Campaign launch")
        bbq_event = add_event(social, title="Branch barbecue")

        self.assertEqual(resolve_page(launch_event.url), launch_event)
        self.assertEqual(resolve_page(bbq_event.url), bbq_event)

    def test_unmatched_flattened_looking_url_does_not_resolve(self) -> None:
        add_feed_page(title="Party News", slug="news")

        self.assertIsNone(resolve_page("/no-such-event/"))
