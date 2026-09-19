import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from bs4 import BeautifulSoup
from django.core.management import CommandError


def require_env(name):
    val = os.environ.get(name)
    if not val:
        raise CommandError(f"{name} environment variable is not set.")
    return val


MAX_PAGE_SIZE = 100


def fetch_page_json(
    slug: str, admin_url: str, api_headers: dict, site_id: int
) -> Tuple[Optional[dict], Optional[str]]:
    """Return (record_dict, None) on success or (None, error_string) on failure."""
    url = f"{admin_url}/api/v2/pages?" + urllib.parse.urlencode(
        {"filter[slug]": slug, "filter[site_id]": site_id, "page[size]": 1}
    )
    req = urllib.request.Request(url, headers=api_headers)
    try:
        with urllib.request.urlopen(req) as resp:
            data, status = json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return None, f"API returned HTTP {e.code} for slug '{slug}': {body}"

    if status != 200:
        return None, f"API returned HTTP {status} for slug '{slug}': {data}"
    records = data.get("data", [])
    if not records:
        return None, f"No page found with slug '{slug}'."
    return records[0], None


def fetch_pages_json(
    admin_url: str, api_headers: dict, site_id: int, page_number: int = 1, page_size=MAX_PAGE_SIZE
) -> Tuple[Optional[List[dict]], Optional[str]]:
    url = f"{admin_url}/api/v2/pages?" + urllib.parse.urlencode(
        {"page[number]": page_number, "page[size]": page_size, "filter[site_id]": site_id}
    )
    req = urllib.request.Request(url, headers=api_headers)
    try:
        with urllib.request.urlopen(req) as resp:
            data, status = json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return None, f"{url} returned HTTP {e.code} for page number '{page_number}': {body}"

    if status != 200:
        return None, f"{url} returned HTTP {status} for page number '{page_number}': {data}"
    # An empty `data` array on a 200 response is the normal, successful signal
    # that pagination has reached the end — not a failure — so it must be
    # distinguished from the HTTPError/non-200 cases above, both of which
    # genuinely need to abort the crawl rather than be read as "no more pages".
    return data.get("data", []), None


FIRST_PAGE_NUMBER = 1


def fetch_page_html(domain: str, slug: str, html_opener, page_number: Optional[int] = None):
    """Return (html_bytes, None) on success or (None, error_string) on failure.

    `page_number` asks for that page of a paginated listing (``?page=<n>``);
    leaving it out fetches the page as a visitor first sees it."""
    url = f"https://{domain}/{slug}"
    if page_number is not None:
        url += "?" + urllib.parse.urlencode({"page": page_number})
    try:
        with html_opener.open(url) as resp:
            return resp.read(), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} fetching {url}"


def extract_pagination_page_numbers(html: Union[str, bytes]) -> Set[int]:
    """
    The numbers of the pages that a listing's ``<ul class="pagination">`` links
    to, along with the one it is currently on.

    Each ``<li>`` holds a link whose ``page`` query parameter is the page it
    goes to. The links that go nowhere are passed over: a "Previous" link on
    the first page has an empty ``page=``, and the current page's own link is
    only ``#``, so for that one the number is read from its text instead. A
    document with no pagination gives an empty set.
    """
    pagination = BeautifulSoup(html, "html.parser").find("ul", class_="pagination")
    if pagination is None:
        return set()
    numbers: Set[int] = set()
    for item in pagination.find_all("li"):
        link = item.find("a", href=True)
        if link is None:
            continue
        values = urllib.parse.parse_qs(urllib.parse.urlparse(str(link["href"])).query).get("page")
        label = link.get_text(strip=True)
        if values and values[0].isdigit():
            numbers.add(int(values[0]))
        elif "active" in item.get("class", []) and label.isdigit():
            numbers.add(int(label))
    return {number for number in numbers if number >= FIRST_PAGE_NUMBER}


def fetch_all_page_html(
    domain: str, slug: str, html_opener
) -> Tuple[Dict[int, bytes], Optional[str]]:
    """
    Fetch a listing's first page and every other page its pagination reaches.

    A long listing may show only the pages near the current one, so the crawl
    reads the pagination of each page it fetches, not just the first, until
    there is no page left that it has not fetched. Returns (pages keyed by
    number, None) on success, or ({}, error_string) as soon as any fetch fails.
    """
    pages: Dict[int, bytes] = {}
    pending: Set[int] = {FIRST_PAGE_NUMBER}
    while pending:
        number = min(pending)
        pending.discard(number)
        # The first page is fetched as a visitor would, without a page number.
        html_bytes, error = fetch_page_html(
            domain, slug, html_opener, page_number=None if number == FIRST_PAGE_NUMBER else number
        )
        if error:
            return {}, error
        pages[number] = html_bytes
        pending |= extract_pagination_page_numbers(html_bytes) - pages.keys()
    return pages, None


def get_api_headers() -> dict:
    api_token = require_env("LEGACY_API_TOKEN")
    user_agent = require_env("LEGACY_USER_AGENT")
    return {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "User-Agent": user_agent,
    }


def get_pages_file_path(domain: str):
    domain_dir = Path(domain)
    if not domain_dir.is_dir():
        raise CommandError(f"'{domain_dir}' is not a directory.")
    return domain_dir / "all_pages.json"
