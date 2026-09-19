"""
Tests for fetching every page of a paginated legacy listing (fetch_pages
--with-pagination): reading the page numbers out of ``<ul class="pagination">``,
and crawling from page to page.

The legacy News page (``blog_sample.html``, a saved copy of the live page) is
the real-world markup. Its pagination links to pages 2 to 7 and marks page 1 as
the current one.
"""

import unittest
import urllib.error
from pathlib import Path
from typing import Dict, List

from underground_crm.management.commands.legacy_api_client import (
    FIRST_PAGE_NUMBER,
    extract_pagination_page_numbers,
    fetch_all_page_html,
    fetch_page_html,
)

NEWS_PAGE = (
    Path(__file__).parent.parent / "management" / "commands" / "test" / "blog_sample.html"
).read_bytes()
# The highest page number in the News page's pagination.
NEWS_LAST_PAGE = 7
NEWS_PAGE_NUMBERS = set(range(FIRST_PAGE_NUMBER, NEWS_LAST_PAGE + 1))

DOMAIN = "fusionparty.org.au"
SLUG = "news"


def pagination_html(*items: str) -> str:
    return f'<nav><ul class="pagination">{"".join(items)}</ul></nav>'


def numbered_link(number: int) -> str:
    return f'<li class="page-item"><a class="page-link" href="https://www.{DOMAIN}/{SLUG}?page={number}">{number}</a></li>'


def current_page_item(number: int) -> str:
    return f'<li class="page-item active"><a class="page-link" href="#">{number}</a></li>'


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self) -> bytes:
        return self.body


class FakeSite:
    """Serves canned pages by URL and records which URLs were asked for, in
    place of the urllib opener that talks to the real legacy site."""

    def __init__(self, pages: Dict[str, bytes]):
        self.pages = pages
        self.requested: List[str] = []

    def open(self, url: str) -> FakeResponse:
        self.requested.append(url)
        if url not in self.pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)
        return FakeResponse(self.pages[url])


def news_url(page_number: int) -> str:
    if page_number == FIRST_PAGE_NUMBER:
        return f"https://{DOMAIN}/{SLUG}"
    return f"https://{DOMAIN}/{SLUG}?page={page_number}"


class TestExtractPaginationPageNumbers(unittest.TestCase):
    def test_the_news_page_links_to_every_page_of_its_listing(self):
        self.assertEqual(extract_pagination_page_numbers(NEWS_PAGE), NEWS_PAGE_NUMBERS)

    def test_the_page_it_is_on_is_read_from_the_label_of_its_own_link(self):
        html = pagination_html(current_page_item(4))
        self.assertEqual(extract_pagination_page_numbers(html), {4})

    def test_a_previous_link_with_an_empty_page_is_passed_over(self):
        empty_previous = f'<li class="page-item disabled"><a href="https://www.{DOMAIN}/{SLUG}?page=">Previous</a></li>'
        html = pagination_html(empty_previous, current_page_item(1))
        self.assertEqual(extract_pagination_page_numbers(html), {FIRST_PAGE_NUMBER})

    def test_next_and_previous_links_add_nothing_new_to_the_numbered_ones(self):
        next_link = f'<li><a href="?page=3">Next</a></li>'
        html = pagination_html(numbered_link(2), numbered_link(3), next_link)
        self.assertEqual(extract_pagination_page_numbers(html), {2, 3})

    def test_a_list_item_with_no_link_is_passed_over(self):
        html = pagination_html(
            '<li class="page-item disabled"><span>…</span></li>', numbered_link(2)
        )
        self.assertEqual(extract_pagination_page_numbers(html), {2})

    def test_a_page_without_pagination_has_no_page_numbers(self):
        self.assertEqual(extract_pagination_page_numbers("<ul><li>Just a list</li></ul>"), set())

    def test_a_link_to_page_zero_is_not_a_page(self):
        self.assertEqual(extract_pagination_page_numbers(pagination_html(numbered_link(0))), set())

    def test_bytes_are_read_as_well_as_text(self):
        html = pagination_html(numbered_link(2))
        self.assertEqual(extract_pagination_page_numbers(html.encode()), {2})


class TestFetchPageHtmlPageNumber(unittest.TestCase):
    def test_a_page_number_becomes_the_page_query_parameter(self):
        second_page = b"<p>Second page of news</p>"
        site = FakeSite({news_url(2): second_page})
        html, error = fetch_page_html(DOMAIN, SLUG, site, page_number=2)
        self.assertEqual((html, error), (second_page, None))

    def test_no_page_number_fetches_the_page_bare(self):
        site = FakeSite({news_url(FIRST_PAGE_NUMBER): NEWS_PAGE})
        fetch_page_html(DOMAIN, SLUG, site)
        self.assertEqual(site.requested, [news_url(FIRST_PAGE_NUMBER)])


class TestFetchAllPageHtml(unittest.TestCase):
    def test_every_page_the_news_listing_links_to_is_fetched(self):
        # Every page of the live listing carries the same full pagination.
        site = FakeSite({news_url(number): NEWS_PAGE for number in NEWS_PAGE_NUMBERS})
        pages, error = fetch_all_page_html(DOMAIN, SLUG, site)
        self.assertIsNone(error)
        self.assertEqual(set(pages), NEWS_PAGE_NUMBERS)

    def test_each_page_is_fetched_exactly_once(self):
        site = FakeSite({news_url(number): NEWS_PAGE for number in NEWS_PAGE_NUMBERS})
        fetch_all_page_html(DOMAIN, SLUG, site)
        self.assertEqual(
            sorted(site.requested),
            sorted(news_url(number) for number in NEWS_PAGE_NUMBERS),
            "the Next link repeats page 2, which must not be fetched twice",
        )

    def test_the_first_page_is_fetched_without_a_page_number(self):
        site = FakeSite({news_url(number): NEWS_PAGE for number in NEWS_PAGE_NUMBERS})
        fetch_all_page_html(DOMAIN, SLUG, site)
        self.assertEqual(site.requested[0], news_url(FIRST_PAGE_NUMBER))

    def test_each_page_is_kept_under_its_own_number(self):
        site = FakeSite(
            {
                news_url(1): pagination_html(current_page_item(1), numbered_link(2)).encode(),
                news_url(2): pagination_html(numbered_link(1), current_page_item(2)).encode(),
            }
        )
        pages, _ = fetch_all_page_html(DOMAIN, SLUG, site)
        self.assertIn(b"active", pages[1])
        self.assertIn(
            b"?page=1", pages[2], "page 2's own markup, not page 1's, is stored as page 2"
        )

    def test_a_windowed_pagination_is_followed_page_by_page(self):
        # A long listing may link only to the neighbouring pages, so page 1 does
        # not reach the last page directly.
        last_page = 5
        all_pages = range(FIRST_PAGE_NUMBER, last_page + 1)
        site = FakeSite(
            {
                news_url(number): pagination_html(
                    *(
                        numbered_link(neighbour)
                        for neighbour in (number - 1, number + 1)
                        if neighbour in all_pages
                    )
                ).encode()
                for number in all_pages
            }
        )
        pages, error = fetch_all_page_html(DOMAIN, SLUG, site)
        self.assertIsNone(error)
        self.assertEqual(set(pages), set(range(FIRST_PAGE_NUMBER, last_page + 1)))

    def test_a_page_with_no_pagination_is_just_itself(self):
        only_page = b"<p>A single page of news</p>"
        site = FakeSite({news_url(FIRST_PAGE_NUMBER): only_page})
        pages, error = fetch_all_page_html(DOMAIN, SLUG, site)
        self.assertEqual((pages, error), ({FIRST_PAGE_NUMBER: only_page}, None))

    def test_a_failed_fetch_stops_the_crawl_with_its_error(self):
        missing_page = 3
        site = FakeSite(
            {news_url(number): NEWS_PAGE for number in NEWS_PAGE_NUMBERS if number != missing_page}
        )
        pages, error = fetch_all_page_html(DOMAIN, SLUG, site)
        self.assertEqual(pages, {})
        self.assertIn(news_url(missing_page), error)


if __name__ == "__main__":
    unittest.main()
