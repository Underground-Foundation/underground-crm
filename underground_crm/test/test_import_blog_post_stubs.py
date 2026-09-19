"""
Tests for the stub BlogPosts that import_pages makes from the list of posts on
a legacy blog page (build_blog_post_stubs).

The HTML mirrors the structure of a real legacy blog page: a
``<ul id="blog-page-<legacy id>">`` whose ``<li>`` children each hold a
``<header>`` (title link, byline) and a ``<div>`` with the post's excerpt.
"""

import datetime
import tempfile
from io import StringIO
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from wagtail.models import Page, Site

from underground_crm.legacy_html import BUTTON_BLOCK, RICH_TEXT_BLOCK
from underground_crm.management.commands.import_pages import (
    build_blog_post_stubs,
    extract_blog_post_listings,
    find_paginated_pages,
)
from underground_crm.models import BlogPost, FeedPage, Person

from bs4 import BeautifulSoup

BLOG_LEGACY_ID = "3382"
BLOG_SLUG = "news"

FIRST_POST_SLUG = "the_line_has_been_crossed"
FIRST_POST_TITLE = "The Line Has Been Crossed"
FIRST_POST_AUTHOR = "Drew Wolfendale"
FIRST_POST_EXCERPT = "Something has changed in Australian politics."
# The legacy list wraps long titles across lines, so the source deliberately
# splits this one; the stub's title must have that whitespace collapsed.
SECOND_POST_SLUG = "fusion_minecraft_server"
SECOND_POST_TITLE_LINE_ONE = "Help Us Shape Fusion"
SECOND_POST_TITLE_LINE_TWO = "Party's Minecraft Server"
SECOND_POST_AUTHOR = "Ethan Cornwill"
SECOND_POST_EXCERPT = "We've just launched a Fusion Party Minecraft server."


def blog_list_item(
    *,
    slug: str,
    title: str,
    author: str,
    posted: str,
    excerpt: str,
    read_more_href: Optional[str] = None,
) -> str:
    """One ``<li>`` as the legacy blog page renders it, including its
    "Read more" button (which points at the post itself unless overridden)."""
    read_more_href = read_more_href if read_more_href is not None else f"/{slug}"
    return f"""
    <li class="mb-4 pb-4 border-bottom" id="blog-post-page-{slug}">
      <header class="mb-3">
        <h3 class="mb-1"><a href="/{slug}" class="text-reset">{title}</a></h3>
        <p class="small text-muted">
          Posted by <span class="linked-signup-name">{author}</span> &middot; {posted}
        </p>
      </header>
      <div class="mb-2">
        <p>{excerpt}</p>
        <p class="mt-2"><a href="{read_more_href}" class="btn btn-info rounded">Read more</a></p>
      </div>
    </li>"""


FIRST_POST_POSTED = "April 15, 2026 10:49 AM"
SECOND_POST_POSTED = "January 30, 2026 11:09 PM"


def blog_page_html(list_items: List[str], legacy_id: str = BLOG_LEGACY_ID) -> str:
    """A whole legacy blog page, with the ``<head>`` metadata get_page_args reads."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>News - Fusion Party</title>
  <meta property="og:image" content="https://example.org/share.png">
  <meta property="og:type" content="website">
  <meta property="og:description" content="News from Fusion Party">
</head>
<body>
  <main id="content">
    <div id="content">
      <ul class="list-unstyled mb-4" id="blog-page-{legacy_id}">{"".join(list_items)}
      </ul>
    </div>
  </main>
</body>
</html>"""


def default_list_items() -> List[str]:
    return [
        blog_list_item(
            slug=FIRST_POST_SLUG,
            title=FIRST_POST_TITLE,
            author=FIRST_POST_AUTHOR,
            posted=FIRST_POST_POSTED,
            excerpt=FIRST_POST_EXCERPT,
        ),
        blog_list_item(
            slug=SECOND_POST_SLUG,
            title=f"{SECOND_POST_TITLE_LINE_ONE}\n          {SECOND_POST_TITLE_LINE_TWO}",
            author=SECOND_POST_AUTHOR,
            posted=SECOND_POST_POSTED,
            excerpt=SECOND_POST_EXCERPT,
        ),
    ]


def blog_post_page_html(paragraphs: List[str]) -> str:
    """A legacy blog post's own page: the article sits in ``div.content``."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>The Line Has Been Crossed - Fusion Party</title>
  <meta property="og:image" content="https://example.org/share.png">
  <meta property="og:type" content="article">
  <meta property="og:description" content="Something has changed">
</head>
<body>
  <main id="content">
    <div class="content">{"".join(paragraphs)}</div>
  </main>
</body>
</html>"""


def blog_post_json(slug: str) -> str:
    return f"""{{
      "id": "7581",
      "attributes": {{
        "slug": "{slug}",
        "parent_slug": "{BLOG_SLUG}",
        "url_path": "/{slug}",
        "name": "The Line Has Been Crossed",
        "headline": "The Line Has Been Crossed",
        "title": "The Line Has Been Crossed - Fusion Party",
        "page_type_name": "Blog Post",
        "published_at": "2026-04-15T10:49:18+10:00"
      }}
    }}"""


OLDER_POST_SLUG = "a_new_name_for_a_new_brand"
OLDER_POST_TITLE = "A new name for a new brand"
OLDER_POST_AUTHOR = "Owen Miller"
OLDER_POST_EXCERPT = "In order for Fusion to gain more prominence, we need a new name."
OLDEST_POST_SLUG = "let_s_change_victorian_politics"
OLDEST_POST_TITLE = "Let's change Victorian politics"
OLDEST_POST_EXCERPT = "In 2022, we were running high from our national founding."


def older_list_items() -> List[str]:
    """The posts on the blog's second page of results."""
    return [
        blog_list_item(
            slug=OLDER_POST_SLUG,
            title=OLDER_POST_TITLE,
            author=OLDER_POST_AUTHOR,
            posted="June 27, 2025 7:50 PM",
            excerpt=OLDER_POST_EXCERPT,
        ),
        blog_list_item(
            slug=OLDEST_POST_SLUG,
            title=OLDEST_POST_TITLE,
            author=OLDER_POST_AUTHOR,
            posted="June 15, 2025 6:57 PM",
            excerpt=OLDEST_POST_EXCERPT,
        ),
    ]


class FindPaginatedPagesTest(SimpleTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.domain = Path(self.directory.name)

    def touch(self, *names: str) -> None:
        for name in names:
            (self.domain / name).write_text("<html></html>", encoding="utf-8")

    def test_the_pages_are_in_numeric_rather_than_alphabetical_order(self):
        tenth_page = 10
        self.touch(f"{BLOG_SLUG}?page={tenth_page}.html", f"{BLOG_SLUG}?page=2.html")
        self.assertEqual(
            [path.name for path in find_paginated_pages(self.domain, BLOG_SLUG)],
            [f"{BLOG_SLUG}?page=2.html", f"{BLOG_SLUG}?page={tenth_page}.html"],
            "page 10 comes after page 2, though '10' sorts before '2' as text",
        )

    def test_only_the_pages_of_the_named_listing_are_found(self):
        self.touch(f"{BLOG_SLUG}.html", f"{BLOG_SLUG}?page=2.html", "events?page=2.html")
        self.assertEqual(
            [path.name for path in find_paginated_pages(self.domain, BLOG_SLUG)],
            [f"{BLOG_SLUG}?page=2.html"],
        )

    def test_a_listing_with_no_further_pages_has_none(self):
        self.touch(f"{BLOG_SLUG}.html")
        self.assertEqual(find_paginated_pages(self.domain, BLOG_SLUG), [])


class ExtractBlogPostListingsTest(SimpleTestCase):
    def listings(self, list_items: Optional[List[str]] = None, legacy_id: str = BLOG_LEGACY_ID):
        soup = BeautifulSoup(blog_page_html(list_items or default_list_items()), "html.parser")
        return extract_blog_post_listings(soup, legacy_id)

    def test_each_list_item_becomes_a_listing_in_page_order(self):
        self.assertEqual(
            [listing.slug for listing in self.listings()],
            [FIRST_POST_SLUG, SECOND_POST_SLUG],
            "the newest-first order of the legacy list should be preserved",
        )

    def test_the_slug_is_the_last_segment_of_the_titles_link(self):
        listing = self.listings(
            [
                blog_list_item(
                    slug="statements/the_line_has_been_crossed",
                    title=FIRST_POST_TITLE,
                    author=FIRST_POST_AUTHOR,
                    posted=FIRST_POST_POSTED,
                    excerpt=FIRST_POST_EXCERPT,
                )
            ]
        )[0]
        self.assertEqual(listing.slug, FIRST_POST_SLUG)

    def test_the_title_has_the_whitespace_of_its_line_wrapping_collapsed(self):
        second = self.listings()[1]
        self.assertEqual(second.title, f"{SECOND_POST_TITLE_LINE_ONE} {SECOND_POST_TITLE_LINE_TWO}")

    def test_the_author_is_the_name_in_the_byline(self):
        first, second = self.listings()
        self.assertEqual(first.author_name, FIRST_POST_AUTHOR)
        self.assertEqual(second.author_name, SECOND_POST_AUTHOR)

    def test_the_date_is_read_in_the_sites_time_zone(self):
        expected = datetime.datetime(2026, 4, 15, 10, 49, tzinfo=ZoneInfo(settings.TIME_ZONE))
        self.assertEqual(self.listings()[0].published_at, expected)

    def test_a_time_after_noon_is_read_as_pm(self):
        # The second post is at 11:09 PM, which must not become 11:09 AM.
        second = self.listings()[1]
        self.assertEqual(second.published_at.hour, 23, "11:09 PM is hour 23 on a 24-hour clock")

    def test_an_unreadable_date_leaves_the_publication_date_unset(self):
        listing = self.listings(
            [
                blog_list_item(
                    slug=FIRST_POST_SLUG,
                    title=FIRST_POST_TITLE,
                    author=FIRST_POST_AUTHOR,
                    posted="a while ago",
                    excerpt=FIRST_POST_EXCERPT,
                )
            ]
        )[0]
        self.assertIsNone(listing.published_at)

    def test_the_excerpt_is_deconstructed_into_blocks(self):
        intro = self.listings()[0].intro
        self.assertEqual([block["type"] for block in intro], [RICH_TEXT_BLOCK])
        self.assertIn(FIRST_POST_EXCERPT, intro[0]["value"])

    def test_a_read_more_button_pointing_at_the_post_itself_is_dropped(self):
        for listing in self.listings():
            self.assertNotIn(BUTTON_BLOCK, [block["type"] for block in listing.intro])

    def test_a_button_pointing_elsewhere_is_kept(self):
        donation_page = "/donate"
        listing = self.listings(
            [
                blog_list_item(
                    slug=FIRST_POST_SLUG,
                    title=FIRST_POST_TITLE,
                    author=FIRST_POST_AUTHOR,
                    posted=FIRST_POST_POSTED,
                    excerpt=FIRST_POST_EXCERPT,
                    read_more_href=donation_page,
                )
            ]
        )[0]
        self.assertEqual(listing.intro[-1]["type"], BUTTON_BLOCK)
        self.assertEqual(listing.intro[-1]["value"]["url"], donation_page)

    def test_a_list_item_with_no_header_is_left_out(self):
        headerless = (
            '<li id="blog-post-page-orphan"><div><p>An excerpt with no title.</p></div></li>'
        )
        listings = self.listings([headerless, *default_list_items()])
        self.assertEqual(
            [listing.slug for listing in listings], [FIRST_POST_SLUG, SECOND_POST_SLUG]
        )

    def test_a_page_without_the_list_gives_no_listings(self):
        unrelated_legacy_id = "9999"
        self.assertEqual(self.listings(legacy_id=unrelated_legacy_id), [])


class BuildBlogPostStubsTest(TestCase):
    def test_the_author_is_the_person_matching_the_bylines_name(self):
        drew = Person.objects.create_user(
            email="drew.wolfendale@example.org",
            password="a-strong-passphrase",
            first_name="Drew",
            last_name="Wolfendale",
        )
        soup = BeautifulSoup(blog_page_html(default_list_items()), "html.parser")
        site = Site.objects.get(is_default_site=True)
        first, second = build_blog_post_stubs(soup, {"id": BLOG_LEGACY_ID}, site)
        self.assertEqual(first.author, drew)
        self.assertEqual(first.owner, drew)
        self.assertIsNone(
            second.author,
            "nobody named Ethan Cornwill has been imported, so there is no one to credit",
        )


class LegacyBlogDirectoryTestCase(TestCase):
    """A directory of pre-fetched legacy files holding one blog page, which the
    whole command can be run against."""

    def setUp(self) -> None:
        self.site = Site.objects.get(is_default_site=True)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.domain = Path(self.directory.name)
        (self.domain / f"{BLOG_SLUG}.html").write_text(
            blog_page_html(default_list_items()), encoding="utf-8"
        )
        (self.domain / f"{BLOG_SLUG}.json").write_text(
            f"""{{
              "id": "{BLOG_LEGACY_ID}",
              "attributes": {{
                "slug": "{BLOG_SLUG}",
                "url_path": "/{BLOG_SLUG}",
                "name": "News",
                "headline": "News",
                "title": "News - Fusion Party",
                "page_type_name": "Blog",
                "published_at": "2022-02-26T00:20:00+11:00"
              }}
            }}""",
            encoding="utf-8",
        )

    def run_import(self, slug: str = BLOG_SLUG, **options) -> str:
        """Run the import for one slug, returning what it wrote to stderr (where
        it reports what it skipped)."""
        errors = StringIO()
        call_command(
            "import_pages",
            domain=str(self.domain),
            slug=slug,
            stdout=StringIO(),
            stderr=errors,
            **options,
        )
        return errors.getvalue()

    def feed_page(self) -> FeedPage:
        return FeedPage.objects.get(slug=BLOG_SLUG)


class ImportBlogPageWithStubsTest(LegacyBlogDirectoryTestCase):

    def test_each_listed_post_becomes_a_live_child_of_the_feed_page(self):
        self.run_import()
        posts = BlogPost.objects.child_of(self.feed_page())
        self.assertEqual(
            {post.slug for post in posts},
            {FIRST_POST_SLUG, SECOND_POST_SLUG},
        )
        self.assertTrue(all(post.live for post in posts), "stubs should be published")

    def test_a_stub_carries_the_titles_date_and_excerpt_of_its_listing(self):
        self.run_import()
        post = BlogPost.objects.get(slug=FIRST_POST_SLUG)
        self.assertEqual(post.title, FIRST_POST_TITLE)
        self.assertEqual(
            post.first_published_at,
            datetime.datetime(2026, 4, 15, 10, 49, tzinfo=ZoneInfo(settings.TIME_ZONE)),
        )
        self.assertIn(FIRST_POST_EXCERPT, str(post.intro))
        self.assertEqual(len(post.body), 0, "the body waits for the post's own page to be imported")

    def test_a_stubs_publication_time_is_the_date_in_its_listing(self):
        self.run_import()
        published = datetime.datetime(2026, 4, 15, 10, 49, tzinfo=ZoneInfo(settings.TIME_ZONE))
        post = BlogPost.objects.get(slug=FIRST_POST_SLUG)
        self.assertEqual(post.go_live_at, published, "the edit form's Publication time")
        self.assertTrue(post.live, "a publication time in the past must not hold the page back")

    def test_a_post_that_already_exists_is_not_overwritten_by_its_stub(self):
        existing = BlogPost(title=FIRST_POST_TITLE, slug=FIRST_POST_SLUG)
        Page.objects.get(pk=self.site.root_page_id).add_child(instance=existing)
        self.run_import()
        self.assertEqual(
            BlogPost.objects.filter(slug=FIRST_POST_SLUG).count(),
            1,
            "importing the listing must not create a second post with the same slug",
        )
        self.assertTrue(BlogPost.objects.filter(slug=SECOND_POST_SLUG).exists())


class ImportBlogPageBodyTest(LegacyBlogDirectoryTestCase):
    """The feed page's template lists its children, so its body must not."""

    def test_the_legacy_post_list_is_not_kept_in_the_body(self):
        self.run_import()
        body = str(self.feed_page().body.get_prep_value())
        self.assertNotIn(f"blog-page-{BLOG_LEGACY_ID}", body)
        self.assertNotIn(FIRST_POST_EXCERPT, body)
        self.assertEqual(len(self.feed_page().body), 0)

    def test_the_stubs_are_still_made_from_the_list(self):
        self.run_import()
        self.assertEqual(BlogPost.objects.child_of(self.feed_page()).count(), 2)


FIRST_POST_SECOND_PARAGRAPH = (
    "One Nation's result in South Australia was not a quirky state result."
)
FIRST_POST_THIRD_PARAGRAPH = "A right-populist force has surged out of the fringe."


class ImportBlogPostIntoStubTest(LegacyBlogDirectoryTestCase):
    """Importing a post's own page once the blog page's list has stubbed it."""

    def setUp(self) -> None:
        super().setUp()
        # The list excerpts the first two paragraphs; the post itself has a third.
        self.write_post(
            [
                f"<p>{FIRST_POST_EXCERPT}</p>",
                f"<p>{FIRST_POST_SECOND_PARAGRAPH}</p>",
                f"<p>{FIRST_POST_THIRD_PARAGRAPH}</p>",
            ]
        )
        (self.domain / f"{BLOG_SLUG}.html").write_text(
            blog_page_html(
                [
                    blog_list_item(
                        slug=FIRST_POST_SLUG,
                        title=FIRST_POST_TITLE,
                        author=FIRST_POST_AUTHOR,
                        posted=FIRST_POST_POSTED,
                        excerpt=f"{FIRST_POST_EXCERPT}</p><p>{FIRST_POST_SECOND_PARAGRAPH}",
                    )
                ]
            ),
            encoding="utf-8",
        )
        self.run_import()

    def write_post(self, paragraphs: List[str]) -> None:
        (self.domain / f"{FIRST_POST_SLUG}.html").write_text(
            blog_post_page_html(paragraphs), encoding="utf-8"
        )
        (self.domain / f"{FIRST_POST_SLUG}.json").write_text(
            blog_post_json(FIRST_POST_SLUG), encoding="utf-8"
        )

    def post(self) -> BlogPost:
        return BlogPost.objects.get(slug=FIRST_POST_SLUG)

    def test_the_stub_is_filled_in_without_the_replace_flag(self):
        stub_pk = self.post().pk
        errors = self.run_import(slug=FIRST_POST_SLUG)
        self.assertNotIn("--replace", errors, "a stub has no body, so nothing needs replacing")
        self.assertEqual(BlogPost.objects.filter(slug=FIRST_POST_SLUG).count(), 1)
        self.assertNotEqual(self.post().pk, stub_pk, "the stub is replaced by the full post")

    def test_the_filled_in_post_has_the_publication_time_of_its_own_page(self):
        self.run_import(slug=FIRST_POST_SLUG)
        post = self.post()
        # blog_post_json() gives the post a published_at of 2026-04-15 10:49:18 (+10:00).
        published = datetime.datetime(
            2026, 4, 15, 10, 49, 18, tzinfo=datetime.timezone(datetime.timedelta(hours=10))
        )
        self.assertEqual(post.go_live_at, published)
        self.assertEqual(post.first_published_at, published)
        self.assertTrue(post.live)

    def test_the_post_stays_under_its_blog_page(self):
        self.run_import(slug=FIRST_POST_SLUG)
        self.assertEqual(self.post().get_parent().specific, self.feed_page())

    def test_the_intro_of_the_stub_is_kept(self):
        self.run_import(slug=FIRST_POST_SLUG)
        intro = str(self.post().intro)
        self.assertIn(FIRST_POST_EXCERPT, intro)
        self.assertIn(FIRST_POST_SECOND_PARAGRAPH, intro)

    def test_the_body_holds_only_what_the_intro_does_not(self):
        self.run_import(slug=FIRST_POST_SLUG)
        body = str(self.post().body)
        self.assertIn(FIRST_POST_THIRD_PARAGRAPH, body)
        self.assertNotIn(FIRST_POST_EXCERPT, body, "the intro already shows the opening paragraph")
        self.assertNotIn(FIRST_POST_SECOND_PARAGRAPH, body, "the intro already shows this one too")

    def test_a_post_with_a_body_needs_the_replace_flag(self):
        self.run_import(slug=FIRST_POST_SLUG)
        body_before = str(self.post().body)
        self.write_post([f"<p>{FIRST_POST_EXCERPT}</p>", "<p>A completely different article.</p>"])
        errors = self.run_import(slug=FIRST_POST_SLUG)
        self.assertIn("--replace", errors)
        self.assertEqual(str(self.post().body), body_before, "the existing post must be untouched")

    def test_replacing_a_post_with_a_body_starts_it_afresh(self):
        self.run_import(slug=FIRST_POST_SLUG)
        replacement = "A completely different article."
        self.write_post([f"<p>{replacement}</p>"])
        self.run_import(slug=FIRST_POST_SLUG, replace=True)
        self.assertIn(replacement, str(self.post().body))
        self.assertEqual(len(self.post().intro), 0, "the earlier intro does not carry over")

    def test_a_page_of_another_type_with_the_same_slug_still_needs_the_replace_flag(self):
        BlogPost.objects.get(slug=FIRST_POST_SLUG).delete()
        basic_page_json = blog_post_json(FIRST_POST_SLUG).replace("Blog Post", "Basic")
        (self.domain / f"{FIRST_POST_SLUG}.json").write_text(basic_page_json, encoding="utf-8")
        stub = BlogPost(title=FIRST_POST_TITLE, slug=FIRST_POST_SLUG)
        self.feed_page().add_child(instance=stub)
        errors = self.run_import(slug=FIRST_POST_SLUG)
        self.assertIn("--replace", errors, "only a Blog Post import may fill in a stub")


class ImportBlogPageWithFurtherPagesTest(LegacyBlogDirectoryTestCase):
    """The stubs of every page of the blog's list, not only its first."""

    def setUp(self) -> None:
        super().setUp()
        self.second_page = self.domain / f"{BLOG_SLUG}?page=2.html"
        self.second_page.write_text(blog_page_html(older_list_items()), encoding="utf-8")

    def test_the_posts_on_a_further_page_get_stubs_too(self):
        self.run_import()
        self.assertEqual(
            {post.slug for post in BlogPost.objects.child_of(self.feed_page())},
            {FIRST_POST_SLUG, SECOND_POST_SLUG, OLDER_POST_SLUG, OLDEST_POST_SLUG},
        )

    def test_a_stub_from_a_further_page_carries_its_own_details(self):
        self.run_import()
        post = BlogPost.objects.get(slug=OLDER_POST_SLUG)
        self.assertEqual(post.title, OLDER_POST_TITLE)
        self.assertIn(OLDER_POST_EXCERPT, str(post.intro))

    def test_a_further_page_is_not_imported_as_a_page_of_its_own(self):
        self.run_import()
        self.assertFalse(Page.objects.filter(slug__contains="page=").exists())

    def test_running_the_whole_directory_does_not_report_a_further_page_as_skipped(self):
        output = StringIO()
        errors = StringIO()
        call_command("import_pages", domain=str(self.domain), stdout=output, stderr=errors)
        self.assertNotIn("page=2", errors.getvalue(), "a further page is not a page to skip")

    def test_a_post_listed_on_two_pages_is_stubbed_once(self):
        # Posts shift between pages when a new one is published between fetches.
        overlapping = blog_page_html([*default_list_items()[:1], *older_list_items()])
        self.second_page.write_text(overlapping, encoding="utf-8")
        self.run_import()
        self.assertEqual(BlogPost.objects.filter(slug=FIRST_POST_SLUG).count(), 1)
        self.assertTrue(BlogPost.objects.filter(slug=OLDEST_POST_SLUG).exists())
