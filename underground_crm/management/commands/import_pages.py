"""
Management command to parse and import legacy CMS pages into Wagtail from pre-fetched files.

Usage:
    python manage.py import_pages --domain <domain>
    python manage.py import_pages --domain <domain> --replace

For each <slug>.html found in <domain>/, the command:
  1. Reads <domain>/<slug>.json to determine the page type and title.
  2. Extracts the page's article content (see extract_importable_html) and
     writes it to <domain>/importable/<slug>.html.
  3. Creates (or replaces) the corresponding Wagtail page, rebuilding the
     article content as StreamField blocks (see
     underground_crm.legacy_html.decompose_legacy_content), falling back to
     a single Raw HTML body block when nothing could be decomposed.

Supported page types:
  "Basic"            -> UndergroundBasicPage
  "Calendar"         -> FeedPage (ordered soonest first, as an events index)
  "Donation"         -> PaymentPage
  "Event"            -> EventPage
  "Blog"             -> FeedPage (ordered newest first), with a stub BlogPost
                        for each post in its list (see build_blog_post_stubs)
  "Blog Post"        -> BlogPost (keeping the intro of a stub made from its
                        blog's list, and cutting the intro's text from the
                        start of the body; see build_blog_post)
  "Redirect"         -> Redirect
  "Signup"           -> RegistrationPage (the <form>'s fields become
                        person_field StreamField blocks when they match
                        whitelisted Person fields; see build_registration_page)
  "Volunteer Signup",
  "Feedback",
  "Suggestion Box"   -> FormPage (the <form>'s fields become input
                        StreamField blocks; see build_form_page)
"""

import copy
import datetime
import functools
import html
import json
import logging
import re
from string import punctuation

import phonenumbers
from dataclasses import dataclass
from dateutil import parser
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from requests.utils import super_len
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Page, Site

from underground_crm.contactability import (
    get_ambiguous_admin_by_full_name,
    get_validated_email_address,
    parse_address,
)
from underground_crm.blocks import registration_person_field_names
from underground_crm.legacy_html import (
    BUTTON_BLOCK,
    RAW_HTML_BLOCK,
    ExtraReconstructor,
    ImageResolver,
    do_blocks_have_visible_content,
    recompose_legacy_content,
    get_body_soup,
    remove_duplicated_intro,
)
from underground_crm.legacy_documents import RemoteDocumentResolver, rewrite_document_links
from underground_crm.legacy_images import RemoteImageResolver
from underground_crm.models import Address, FeedPage, UndergroundBasicPage
from underground_crm.models import Tag as CrmTag
from underground_crm.models.pages import (
    EventPage,
    BlogPost,
    FormPage,
    FormPageTag,
    RegistrationPage,
    DEFAULT_PAGE_SIZE,
)
from underground_payments.models import PaymentPage
from underground_crm.numbers import parse_localized_number
from underground_crm.management.commands.importing import read_page_graph, iter_pages_breadth_first

logger = logging.getLogger(__name__)


@dataclass
class ImportCounter:
    """Tracks counts of imported, replaced, and skipped pages."""

    imported: int = 0
    replaced: int = 0
    skipped: int = 0

    def increment_imported(self) -> None:
        self.imported += 1

    def increment_replaced(self) -> None:
        self.replaced += 1

    def increment_skipped(self) -> None:
        self.skipped += 1

    def has_evaluated_any_pages(self):
        return bool(self.imported or self.replaced or self.skipped)

    def get_summary(self) -> str:
        return f"Imported: {self.imported}, replaced: {self.replaced}, skipped: {self.skipped}."


def should_show_toc(soup: BeautifulSoup) -> bool:
    """Return True if the page has a table-of-contents nav.

    Looks for a <nav id="toc"> anywhere in the document. When present, the page
    was rendered with a TOC, so TOC-aware template behavior should be enabled.
    """
    return soup.find("nav", id="toc") is not None


def _prettify(tag: Tag) -> str:
    """Return prettified HTML with 2-space indentation."""
    lines = tag.prettify().splitlines(keepends=True)
    result = []
    for line in lines:
        stripped = line.lstrip(" ")
        spaces = len(line) - len(stripped)
        result.append("  " * spaces + stripped)
    return "".join(result)


def _load_page_attributes(json_path: Path) -> dict | None:
    """Load page metadata from the JSON sidecar file.

    Handles both full JSON:API envelopes and unwrapped attribute objects.
    Returns None if no attributes can be read.
    """
    with open(json_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    # The JSON may be a full JSON:API envelope {"data": {...}, "meta": {}}
    # or the unwrapped record object {"id": ..., "attributes": ...} directly,
    # depending on which endpoint was used to fetch it.
    if isinstance(raw.get("data"), dict):
        record = raw["data"]
    else:
        record = raw
    attributes = record.get("attributes", {})
    if not attributes:
        return None
    # The legacy page id lives on the record itself, not inside its
    # "attributes" object, but get_page_args() needs it alongside the rest
    # of the metadata to populate BasicPage.legacy_id.
    if "id" not in attributes and record.get("id") is not None:
        attributes = {**attributes, "id": record["id"]}
    return attributes


def extract_importable_html(
    html_file: Path,
    importable_dir: Optional[Path],
    document_resolver: Optional[RemoteDocumentResolver] = None,
) -> tuple[BeautifulSoup, str] | None:
    """Extract the element that holds the page-specific content, write it to
    importable_dir, and return the full-page soup alongside the extracted
    HTML string.

    The page's own ``div.content`` is preferred when present: the outer
    ``id="content"`` region holds more extraneous elements.

    With a `document_resolver`, links to legacy documents are replaced with
    links to our migrated documents before the extraction occurs.
    """
    document_soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
    if document_resolver is not None:
        rewrite_document_links(document_soup, document_resolver)
    body_soup = get_body_soup(document_soup)
    if body_soup is None:
        return None
    html_content = _prettify(body_soup)
    if importable_dir:
        (importable_dir / html_file.name).write_text(html_content, encoding="utf-8")
    return document_soup, html_content


def extract_author(soup: BeautifulSoup) -> Optional[settings.AUTH_USER_MODEL]:
    byline = soup.find("div", class_="byline")
    if not byline:
        return None
    author_name = soup.find("span", class_="linked-signup-name")
    if not author_name:
        return None
    return get_ambiguous_admin_by_full_name(
        author_name.text,
    )


def extract_og_image(head: BeautifulSoup) -> Optional[str]:
    og_image = head.find("meta", property="og:image")
    return og_image.get("content")


def extract_og_type(head: BeautifulSoup) -> Optional[str]:
    og_type = head.find("meta", property="og:type")
    return og_type.get("content")


def extract_og_description(head: BeautifulSoup) -> Optional[str]:
    og_description = head.find("meta", property="og:description")
    return og_description.get("content")


def get_publication_date(attributes: Dict[str, Any]) -> Optional[datetime.datetime]:
    raw_date = attributes.get("published_at")
    if not raw_date:
        return None
    return datetime.datetime.fromisoformat(raw_date)


# Map the geo-political subdivision to an IANA timezone
SUBDIVISION_TO_TIMEZONE = {
    "NSW": "Australia/Sydney",
    "ACT": "Australia/Sydney",
    "VIC": "Australia/Melbourne",
    "QLD": "Australia/Brisbane",
    "SA": "Australia/Adelaide",
    "WA": "Australia/Perth",
    "TAS": "Australia/Hobart",
    "NT": "Australia/Darwin",
}


def parse_event_datetime(
    event_string,
) -> Tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
    """
    Parse an event string like:
    "        May 16, 2026 at 18:00 - 11pm (NSW/ACT/VIC/TAS timezone)"

    Returns:
        tuple: (start_datetime, end_datetime)
    """
    event_string = event_string.strip()

    times_part, _, timezone_part = event_string.partition(" (")
    start_part, _, end_time_string = times_part.partition(" - ")
    timezone_label = timezone_part.replace(")", "").replace(" timezone", "")

    # Split on "/" and take the first state/territory
    first_state = timezone_label.split("/")[0]
    iana_name = SUBDIVISION_TO_TIMEZONE.get(first_state) or settings.TIME_ZONE
    timezone_obj = ZoneInfo(iana_name)

    # Parse the start datetime
    # dateutil copes with a leading weekday, day-first dates and 12-hour times.
    naïve_start = parser.parse(start_part)
    start_datetime = naïve_start.replace(tzinfo=timezone_obj)
    if not end_time_string:
        return start_datetime, None
    ending_default = start_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
    end_datetime = parser.parse(end_time_string, default=ending_default)

    if end_datetime < start_datetime:
        end_datetime += datetime.timedelta(days=1)

    return start_datetime, end_datetime


def get_substantial_child_strings(parent_tag: Tag) -> List[str]:
    return [stripped for stripped in [tag.text.strip() for tag in parent_tag.contents] if stripped]
    # return [tag for tag in parent_tag.contents if tag.text.strip()]


def get_event_detail_pairs(soup: BeautifulSoup) -> List[Tag]:
    return [
        e
        for e in soup.find_all("div", class_="event-detail")
        if e.contents and len(get_substantial_child_strings(e)) > 0
    ]


def extract_event_time(
    soup: BeautifulSoup,
) -> Tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
    event_pairs = get_event_detail_pairs(soup)
    if not event_pairs:
        logger.info(f"Event time element was not found")
        return None, None
    time_contents = get_substantial_child_strings(event_pairs[0])
    if time_contents[0] != "When":
        logger.info(f"Unable to determine time from wrong event detail")
        return None, None
    return parse_event_datetime(time_contents[1])


def extract_event_venue(soup: BeautifulSoup, create_if_not_found=True) -> Optional[Address]:
    pairs = get_event_detail_pairs(soup)
    if len(pairs) < 2:
        logger.info("Unable to determine event location")
        return None
    location_pair = pairs[1]
    location_contents = get_substantial_child_strings(location_pair)
    if location_contents[0] != "Where":
        logger.info("Unable to determine location from wrong event detail")
        return None
    address = parse_address(location_contents[1])
    try:
        existing_address = Address.objects.get(
            line1=address.line1,
            line2=address.line2,
            line3=address.line3,
            city=address.city,
            state=address.state,
            postcode=address.postcode,
            country_code=address.postcode,
        )
        return existing_address
    except Address.DoesNotExist:
        if create_if_not_found:
            address._skip_geocoding = True
            address.save()
        return address


def get_host_by_email_address(email_address: str):
    if not email_address:
        return None
    validated_address = get_validated_email_address(email_address.strip())
    if not validated_address:
        return None
    User = get_user_model()
    try:
        return User.objects.get(email=validated_address)
    except User.DoesNotExist:
        return None


def get_host_by_phone(raw_phone: str) -> Optional[settings.AUTH_USER_MODEL]:
    try:
        phone = phonenumbers.parse(raw_phone)
    except phonenumbers.NumberParseException as e:
        logger.info(f"Unable to determine an event host by phone number: {e}")
        return None
    User = get_user_model()
    try:
        return User.objects.get(phone_number=phone)
    except User.DoesNotExist:
        logger.info(f"Unable to find a user with phone number {phone}")
        return None


def extract_host_attributes(soup: BeautifulSoup) -> Optional[List[str]]:
    # Returns the text describing the host and their contact details, eg `Owen · owen.miller@fusionparty.org.au`
    # Notice though, the email address is not visible in the initial HTML − it requires some JavaScript to execute,
    # revealing the email address on the page. It is therefore not extracted here.
    pairs = get_event_detail_pairs(soup)
    if len(pairs) < 3:
        logger.info(f"Unable to determine event host. Only {len(pairs)} event pairs")
        return None
    host_pair = pairs[2]
    host_contents = get_substantial_child_strings(host_pair)
    if not host_contents or len(host_contents) != 2 or not host_contents[0] == "Contact":
        logger.info("Unable to determine host from wrong event detail")
        return None
    logger.info(f"Host contents: {host_contents}")
    host_text = host_contents[1]
    logger.info(f"Raw host: {host_text}")
    return host_text.split("·")


def extract_event_host(soup: BeautifulSoup) -> Optional[settings.AUTH_USER_MODEL]:
    host_parts = extract_host_attributes(soup)
    logger.info(f"Host parts: {host_parts}")
    if not host_parts:
        return None
    if host_parts and len(host_parts) > 1:
        host = get_host_by_email_address(host_parts[1])
        if host:
            return host
        else:
            # The 2nd part might be a phone number and not an email address
            host = get_host_by_phone(host_parts[1])
            if host:
                return host
    if host_parts and len(host_parts) > 2:
        host = get_host_by_phone(host_parts[2])
        if host:
            return host
    host = get_ambiguous_admin_by_full_name(host_parts[0].strip())
    if host:
        logger.info("The extracted host is %s", host)
    return host


def extract_event_population(soup: BeautifulSoup):
    pairs = get_event_detail_pairs(soup)
    if len(pairs) < 4:
        logger.info(f"Unable to determine event population. Only {len(pairs)} event pairs")
        return None
    for pair in pairs:
        subhead = pair.find("p", class_="subhead")
        if subhead:
            result = parse_localized_number(subhead.text.split(" ")[0])
            if result is not None:
                return int(result)
    logger.info("Unable to determine event population")
    return None


def build_body_blocks(
    document_soup: BeautifulSoup,
    importable_html: str,
    image_resolver: Optional[ImageResolver] = None,
    extra_reconstructor: Optional[ExtraReconstructor] = None,
) -> List[dict]:
    """
    Reconstructs the page's content as StreamField blocks. Without an
    `image_resolver`, every image is left in a Raw HTML block. A theme's
    `extra_reconstructor` function can create custom blocks (not just StreamField).
    """
    content_element = document_soup.find(id="content")
    blocks = (
        recompose_legacy_content(
            content_element,
            image_resolver=image_resolver,
            extra_reconstructor=extra_reconstructor,
        )
        if content_element is not None
        else []
    )
    if blocks:
        return blocks
    return [{"type": RAW_HTML_BLOCK, "value": importable_html}]


def get_page_args(
    document_soup,
    importable_html,
    attributes,
    slug: str,
    site,
    body_blocks: Optional[List[dict]] = None,
    image_resolver: Optional[ImageResolver] = None,
    extra_reconstructor: Optional[ExtraReconstructor] = None,
) -> dict:
    """
    The keyword arguments common to every imported page. `body_blocks` is the
    page's body when the caller has already built (and adjusted) it; otherwise
    it is built here from the document, with `image_resolver` internalizing its
    images and `extra_reconstructor` (see build_body_blocks) reconstructing any
    custom blocks.
    """
    head = document_soup.find("head")
    seo_title = attributes.get("title", "")
    # The name attribute is an administrative label for the page. The headline is what public viewers see as a
    # prominent h1.
    headline = attributes.get("headline") or attributes.get("name") or seo_title
    seo_image_url = extract_og_image(head)
    if seo_image_url:
        # This cannot be used in the importing script just yet, as the search_image property of PageWithMetadata is a
        # hosted Wagtail image, not a URL.
        pass
    author = extract_author(document_soup)
    legacy_id = attributes.get("id")
    publication_date = get_publication_date(attributes)
    return {
        "title": headline,
        "slug": slug,
        "owner": author,
        "seo_title": seo_title,
        "search_description": extract_og_description(head),
        # Kept through publishing (Wagtail only sets it when empty), and the
        # date feeds list a page under (see feed_item_date).
        "first_published_at": publication_date,
        # The "Publication time" shown when the page is edited. A time in the
        # past does not hold the page back; only a future one schedules it.
        "go_live_at": publication_date,
        "og_type_override": extract_og_type(head),
        "body": json.dumps(
            body_blocks
            if body_blocks is not None
            else build_body_blocks(
                document_soup, importable_html, image_resolver, extra_reconstructor
            )
        ),
        "show_toc": should_show_toc(document_soup),
        "legacy_id": int(legacy_id) if legacy_id is not None else None,
    }


def build_underground_basic_page(
    document_soup: BeautifulSoup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=UndergroundBasicPage,
    image_resolver: Optional[ImageResolver] = None,
    extra_reconstructor: Optional[ExtraReconstructor] = None,
) -> UndergroundBasicPage:
    """
    Build and return an unsaved Wagtail page from imported HTML content.

    The caller is responsible for saving it via parent_page.add_child().
    Subclasses of UndergroundBasicPage are accepted; any fields they add
    beyond the base set must be set on the returned instance before saving.
    """
    return return_class(
        **get_page_args(
            document_soup,
            importable_html,
            attributes,
            slug,
            site,
            image_resolver=image_resolver,
            extra_reconstructor=extra_reconstructor,
        )
    )


def build_event_page(
    document_soup: BeautifulSoup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=EventPage,
    image_resolver: Optional[ImageResolver] = None,
) -> EventPage:
    kwargs = get_page_args(
        document_soup=document_soup,
        importable_html=importable_html,
        attributes=attributes,
        slug=slug,
        site=site,
        image_resolver=image_resolver,
    )
    kwargs.pop("show_toc")
    # EventPage descends from FormServingPage, not BasicPage, so it has no
    # legacy_id field to store this in.
    kwargs.pop("legacy_id")
    page = cast(
        EventPage,
        return_class(**kwargs),
    )
    page.host = extract_event_host(document_soup)
    page.start_time, page.end_time = extract_event_time(document_soup)
    page.venue = extract_event_venue(document_soup)
    page.population = extract_event_population(document_soup)
    return page


def extract_page_size(soup: BeautifulSoup) -> Optional[int]:
    content_div = soup.find("div", id="content")
    if not content_div:
        logger.info(f"No content div could be found for this blog")
        return None
    ul = content_div.find(
        "ul"
    )  # The highest list in the content soup will be the list of blog posts
    if not ul:
        logger.info(f"No ul could be found for this blog")
        return None
    list_items = ul.find_all("li", recursive=False)
    if not list_items:
        logger.info(f"No list items for this blog")
        return None
    return len(list_items)


def build_feed_body_blocks(
    document_soup: BeautifulSoup,
    legacy_id: Optional[str],
    image_resolver: Optional[ImageResolver] = None,
) -> List[dict]:
    """
    The body of a legacy Blog page, without its ``<ul id="blog-page-<id>">``
    of posts.

    A FeedPage's template lists its children itself, so importing the legacy
    list into the body too would show every post twice. Whatever else the
    page holds (an introduction above the list, say) is kept; a page holding
    nothing else gets an empty body.

    Works on a copy: the caller still needs the list to make the stubs (see
    build_blog_post_stubs) and to size the page.
    """
    without_list = copy.copy(document_soup)
    post_list = without_list.find("ul", id=f"blog-page-{legacy_id}")
    if post_list is not None:
        post_list.decompose()
    content_element = without_list.find(id="content")
    return (
        recompose_legacy_content(content_element, image_resolver=image_resolver)
        if content_element is not None
        else []
    )


def build_feed_page(
    document_soup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=FeedPage,
    ordering: str = FeedPage.Ordering.NEWEST_FIRST,
    image_resolver: Optional[ImageResolver] = None,
) -> FeedPage:
    kwargs = get_page_args(
        document_soup=document_soup,
        importable_html=importable_html,
        attributes=attributes,
        slug=slug,
        site=site,
        body_blocks=(
            build_feed_body_blocks(document_soup, attributes.get("id"), image_resolver)
            if ordering == FeedPage.Ordering.NEWEST_FIRST
            else None
        ),
        image_resolver=image_resolver,
    )
    kwargs.pop("show_toc")
    page = cast(
        FeedPage,
        return_class(**kwargs),
    )
    page.page_size = extract_page_size(document_soup) or DEFAULT_PAGE_SIZE
    page.ordering = ordering
    return page


def extract_redirection_destination(document_soup: BeautifulSoup) -> Optional[str]:
    """
    The URL that a legacy redirection leads to. Fetching a redirection's URL follows
    the redirect, so its saved HTML is that of the destination, which names its own
    URL in its canonical link or, failing that, its og:url.

    A redirection's "parent_slug" is no guide: it is only where the redirection sits
    in the legacy page tree, which is often a folder of redirections.
    """
    canonical = document_soup.find("link", rel="canonical", href=True)
    if canonical is not None:
        return str(canonical["href"])
    og_url = document_soup.find("meta", property="og:url", content=True)
    if og_url is not None:
        return str(og_url["content"])
    return None


def _strip_www(hostname: str) -> str:
    hostname = hostname.lower()
    return hostname[len("www.") :] if hostname.startswith("www.") else hostname


def build_redirection(
    document_soup,
    importable_html,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=Redirect,
    image_resolver: Optional[ImageResolver] = None,
    legacy_domain: Optional[str] = None,
) -> Optional[Redirect]:
    """
    A Redirect from the legacy redirection's path to its destination, or None when the
    destination cannot be determined.

    A destination on `legacy_domain` must already have been imported, and the
    redirection points at that page. A destination elsewhere is kept as a link.
    """
    destination_url = extract_redirection_destination(document_soup)
    if not destination_url:
        logger.warning(f"Cannot create redirection at {slug}: its destination is unknown")
        return None
    parsed_url = urlparse(destination_url)
    destination_page = None
    redirect_link = ""
    if not parsed_url.hostname or (
        legacy_domain and _strip_www(parsed_url.hostname) == _strip_www(legacy_domain)
    ):
        destination_slug = parsed_url.path.strip("/").split("/")[-1]
        if destination_slug == slug:
            logger.warning(
                f"Cannot create redirection at {slug}: its saved HTML is its own page, "
                "not that of its destination"
            )
            return None
        destination_page = (
            Page.objects.filter(slug=destination_slug).first()
            if destination_slug
            else site.root_page
        )
        if destination_page is None:
            logger.warning(
                f"Cannot create redirection at {slug}: page {destination_slug} does not exist"
            )
            return None
    else:
        redirect_link = destination_url
    return cast(
        Redirect,
        return_class(
            # RedirectMiddleware looks up the request's normalized path
            # ("/blog"), but only Redirect.clean() normalizes old_path, and
            # save() never calls it, so the bare slug would never match.
            old_path=Redirect.normalise_path(slug),
            site=site,
            is_permanent=True,
            redirect_page=destination_page,
            redirect_page_route_path="",
            redirect_link=redirect_link,
        ),
    )


def extract_donation_frequency(soup: BeautifulSoup) -> tuple[bool, bool]:
    """
    Inspect donation-frequency radio buttons to determine which recurrence
    options are offered on the page.

    Looks for a <fieldset> whose <legend> contains the word "frequency", then
    collects the `value` attributes of any <input type="radio"> inside it.

    Returns (allow_monthly, allow_annual).
    """
    for fieldset in soup.find_all("fieldset"):
        legend = fieldset.find("legend")
        if not legend or "frequency" not in legend.get_text().lower():
            continue
        values = {
            inp.get("value", "").strip().lower()
            for inp in fieldset.find_all("input", {"type": "radio"})
        }
        return "monthly" in values, ("annual" in values or "yearly" in values)
    return False, False


def build_payment_page(
    document_soup: BeautifulSoup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=PaymentPage,
    image_resolver: Optional[ImageResolver] = None,
) -> PaymentPage:
    kwargs = get_page_args(
        document_soup, importable_html, attributes, slug, site, image_resolver=image_resolver
    )
    kwargs.pop("show_toc")
    page = return_class(**kwargs)
    page.allow_monthly_payments, page.allow_annual_payments = extract_donation_frequency(
        document_soup
    )
    return page


def build_blog_post(
    document_soup: BeautifulSoup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=BlogPost,
    existing_intro: Optional[List[dict]] = None,
    image_resolver: Optional[ImageResolver] = None,
    extra_reconstructor: Optional[ExtraReconstructor] = None,
) -> BlogPost:
    """
    Build and return an unsaved BlogPost from imported HTML content.

    `existing_intro` is the intro of the stub that the blog page's list already
    made for this post (see build_blog_post_stubs), or None when there was no
    stub. The post keeps that intro, and whatever the intro already says is cut
    from the start of the body so that it is not shown twice.

    `image_resolver` internalizes the body's images (see
    underground_crm.legacy_images). `extra_reconstructor` claims the markup a
    theme has its own blocks for (see build_body_blocks).
    """
    body_blocks = build_body_blocks(
        document_soup, importable_html, image_resolver, extra_reconstructor
    )
    if existing_intro is not None:
        body_blocks, duplicated = remove_duplicated_intro(body_blocks, existing_intro)
        logger.info("[intro] cut %s duplicated element(s) from the body of '%s'.", duplicated, slug)
    page = cast(
        BlogPost,
        return_class(
            **get_page_args(
                document_soup=document_soup,
                importable_html=importable_html,
                attributes=attributes,
                slug=slug,
                site=site,
                body_blocks=body_blocks,
            )
        ),
    )
    if existing_intro is not None:
        page.intro = json.dumps(existing_intro)
    # BlogPost is the only page type with its own author field (distinct from
    # the inherited "owner") — get_page_args() no longer sets it, since every
    # other page type this is called for lacks that field entirely.
    page.author = extract_author(document_soup)
    return page


class BlogPostListing(NamedTuple):
    """One entry of a legacy blog page's list of posts, before it becomes a BlogPost."""

    slug: str
    title: str
    author_name: Optional[str]
    published_at: Optional[datetime.datetime]
    intro: List[dict]


def _blog_post_slug(header: Tag) -> Optional[str]:
    """Extract the slug of a blog post from its intro in the feed."""
    link = header.find("a", href=True)
    if link is None:
        return None
    segments = [segment for segment in urlparse(str(link["href"])).path.split("/") if segment]
    return segments[-1] if segments else None


def _blog_post_published_at(header: Tag) -> Optional[datetime.datetime]:
    """
    The date in a listed post's byline, e.g. "Posted by Drew Wolfendale · April
    15, 2026 10:49 AM", read in the site's own time zone. None when the byline
    is missing or worded differently.
    """
    byline = header.find("p")
    if byline is None or "·" not in byline.get_text():
        return None
    raw_date = " ".join(byline.get_text().rsplit("·", 1)[1].split())
    try:
        naive = datetime.datetime.strptime(raw_date, "%B %d, %Y %I:%M %p")
    except ValueError:
        return None
    return naive.replace(tzinfo=ZoneInfo(settings.TIME_ZONE))


def _without_read_more_button(blocks: List[dict], slug: str) -> List[dict]:
    """
    Drop the "Read more" button that was added to each blog-post intro in the feed.
    """
    if blocks and blocks[-1]["type"] == BUTTON_BLOCK:
        destination = urlparse(blocks[-1]["value"]["url"]).path.strip("/")
        if destination == slug:
            return blocks[:-1]
    return blocks


def extract_blog_post_listings(
    document_soup: BeautifulSoup,
    legacy_id: Optional[str],
    image_resolver: Optional[ImageResolver] = None,
) -> List[BlogPostListing]:
    """
    Read the posts out of a legacy blog page's ``<ul id="blog-page-<id>">``.

    Each child ``<li>`` has a ``<header>`` (the title in its ``<h3>``, the slug
    in that title's link, the author and date in its byline) and a ``<div>``
    holding the post's excerpt, which is reconstructed exactly as a Basic
    page's content is. An ``<li>`` missing either child, or whose header gives
    no slug or title, is reported and left out.
    """
    post_list = document_soup.find("ul", id=f"blog-page-{legacy_id}")
    if post_list is None:
        logger.warning("No <ul id='blog-page-%s'> could be found for this blog.", legacy_id)
        return []
    listings = []
    for item in post_list.find_all("li", recursive=False):
        header = item.find("header", recursive=False)
        excerpt = item.find("div", recursive=False)
        if header is None or excerpt is None:
            logger.warning("Skipping <li id='%s'>: no header or no excerpt.", item.get("id"))
            continue
        slug = _blog_post_slug(header)
        heading = header.find("h3")
        title = " ".join(heading.get_text().split()) if heading else ""
        if not slug or not title:
            logger.warning("Skipping <li id='%s'>: no slug or no title.", item.get("id"))
            continue
        author = header.find("span", class_="linked-signup-name")
        listings.append(
            BlogPostListing(
                slug=slug,
                title=title,
                author_name=author.get_text(strip=True) if author else None,
                published_at=_blog_post_published_at(header),
                intro=_without_read_more_button(
                    recompose_legacy_content(excerpt, image_resolver=image_resolver), slug
                ),
            )
        )
    return listings


def build_blog_post_stubs(
    document_soup: BeautifulSoup,
    attributes: Dict[str, Any],
    site: Site,
    image_resolver: Optional[ImageResolver] = None,
) -> List[BlogPost]:
    """
    Build an unsaved BlogPost for each post in a legacy blog page's list.

    These are stubs: the list carries only each post's title, author, date and
    excerpt (which becomes its `intro`), so the body is empty until the post's
    own page is imported. The caller is responsible for saving them beneath the
    blog's FeedPage.
    """
    stubs = []
    for listing in extract_blog_post_listings(document_soup, attributes.get("id"), image_resolver):
        author = (
            get_ambiguous_admin_by_full_name(listing.author_name) if listing.author_name else None
        )
        stubs.append(
            BlogPost(
                title=listing.title,
                slug=listing.slug,
                owner=author,
                author=author,
                first_published_at=listing.published_at,
                go_live_at=listing.published_at,
                intro=json.dumps(listing.intro),
            )
        )
    return stubs


# Input types that aren't real user-facing fields, so extract_form_inputs()
# ignores them regardless of surrounding markup.
_SKIPPED_INPUT_TYPES = {"hidden", "submit", "button", "reset"}


class FormInput(NamedTuple):
    """One user-facing field extracted from a legacy <form>."""

    description: str
    # "checkbox" or "text" — the FORM_INPUT_BLOCKS block type it maps to.
    input_type: str
    # The legacy record field this input writes to, parsed out of the input's
    # name attribute: "signup[mobile_number]" names "mobile_number".
    field_name: str


def _legacy_field_name(raw_name: str) -> str:
    """
    The innermost bracketed segment of a legacy input name — legacy forms
    name their inputs "<record>[<field>]", e.g. "signup[mobile_number]" or
    "volunteer_signup[volunteer_type_ids][]" — falling back to the whole
    name when there are no brackets (e.g. "email_address").
    """
    segments = re.findall(r"\[([^\]]+)\]", raw_name)
    return segments[-1] if segments else raw_name


def extract_form_inputs(form_tag: Tag) -> List[FormInput]:
    """
    Extract a FormInput for every real, user-facing field in a legacy <form>
    — skipping hidden/CSRF/bookkeeping inputs, the submit button, and any
    field wrapped in an aria-hidden container (e.g. the "Optional email code"
    honeypot/identity field used to recognise a logged-in visitor). Checkbox
    groups (e.g. volunteer_type_ids[]) yield one entry per checkbox, using
    its <label for="..."> text as the description; text inputs and textareas
    do the same.
    """
    inputs: List[FormInput] = []
    for field in form_tag.find_all(["input", "textarea"]):
        field_type = field.get("type", "text") if field.name == "input" else "text"
        if field_type in _SKIPPED_INPUT_TYPES:
            continue
        if field.find_parent(attrs={"aria-hidden": "true"}):
            continue

        field_id = field.get("id")
        label = form_tag.find("label", attrs={"for": field_id}) if field_id else None
        raw_name = field.get("name", "").strip()
        description = label.get_text(strip=True) if label else raw_name
        if not description:
            continue

        input_type = "checkbox" if field_type == "checkbox" else "text"
        inputs.append(FormInput(description, input_type, _legacy_field_name(raw_name)))
    return inputs


# Only "Volunteer Signup" submissions should tag the submitting Person as a
# volunteer; "Feedback" and "Suggestion Box" are legacy types that share the
# same <form>-extraction logic but must leave tags_to_apply empty.
_VOLUNTEER_SIGNUP_PAGE_TYPE_NAME = "Volunteer Signup"
_VOLUNTEER_TAG_NAME = "Volunteer"


def get_tags_to_apply_for_legacy_type(page_type_name: Optional[str]) -> List[CrmTag]:
    if page_type_name != _VOLUNTEER_SIGNUP_PAGE_TYPE_NAME:
        return []
    volunteer_tag, _created = CrmTag.objects.get_or_create(name=_VOLUNTEER_TAG_NAME)
    return [volunteer_tag]


def build_form_page(
    document_soup: BeautifulSoup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=FormPage,
    image_resolver: Optional[ImageResolver] = None,
) -> FormPage:
    """
    Build a FormPage from a legacy form-bearing page ("Volunteer Signup",
    "Feedback", or "Suggestion Box"). The <form>'s checkbox/text/textarea
    fields become "checkbox"/"text" input StreamField blocks (see
    FORM_INPUT_BLOCKS in models/pages.py), each preceded by an "html" block
    carrying the legacy question text, since a bare input block has no
    per-question label of its own; everything else in #content becomes an
    ordinary "html" block ahead of them, so the page's intro copy is
    preserved.

    The source page snapshot may reflect one already-signed-up member's
    current answers (checked boxes, filled-in text) — only the field
    descriptions are imported, never that member's answers.

    Only "Volunteer Signup" pages get the "Volunteer" tag wired up as
    tags_to_apply, so that authenticated submitters are tagged as
    volunteers (see FormPage.tags_to_apply); "Feedback" and "Suggestion Box"
    pages get no tags_to_apply, since submitting them says nothing about
    whether the submitter volunteers.
    """
    # Work on a private copy: extracting the <form> below must not mutate the
    # caller's soup, which a --replace re-import passes in again.
    document_soup = copy.copy(document_soup)
    content = document_soup.find(id="content")
    form_tag = content.find("form") if content else None

    if content is not None and form_tag is not None:
        form_tag.extract()
        remaining_html = _prettify(content)
    else:
        remaining_html = importable_html

    body_blocks: List[Tuple[str, Any]] = []
    if remaining_html.strip():
        body_blocks.append(("html", remaining_html))
    if form_tag is not None:
        for form_input in extract_form_inputs(form_tag):
            body_blocks.append(("html", f"<p>{html.escape(form_input.description)}</p>"))
            body_blocks.append(
                (form_input.input_type, False if form_input.input_type == "checkbox" else "")
            )

    kwargs = get_page_args(
        document_soup=document_soup,
        importable_html=importable_html,
        attributes=attributes,
        slug=slug,
        site=site,
    )
    kwargs.pop("show_toc")
    # FormPage descends from FormServingPage, not BasicPage, so it has no
    # legacy_id field to store this in.
    kwargs.pop("legacy_id")
    kwargs["body"] = body_blocks
    page = cast(FormPage, return_class(**kwargs))
    tags_to_apply = get_tags_to_apply_for_legacy_type(get_page_type_attribute(attributes))
    page.tagged_items = [FormPageTag(tag=tag) for tag in tags_to_apply]
    return page


# Legacy signup fields whose values RegistrationForm collects on its own —
# identity fields are always rendered for anonymous visitors — so no
# person_field block is needed for them.
_IDENTITY_FIELD_NAMES = {"email", "email_address", "first_name", "last_name"}

# Legacy signup fields that write to a differently named Person field. The
# legacy free-text address question becomes a home-address input: the
# registration form resolves the submitted string to an Address record (see
# RegistrationForm), so nothing lands in the import-only submitted_address.
_LEGACY_SIGNUP_FIELD_ALIASES = {"submitted_address": "home_address"}


def build_registration_page(
    document_soup: BeautifulSoup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    image_resolver: Optional[ImageResolver] = None,
) -> RegistrationPage:
    """
    Build a RegistrationPage from a legacy "Signup" page. A signup <form>
    asks for details of the visitor's own record, so each field becomes a
    "person_field" block when its legacy field name matches a whitelisted
    Person field — Person's field names follow the legacy system's, so they
    usually match directly — with the legacy question text kept as the
    block's label override. Identity fields (email/first/last name) are
    skipped because RegistrationForm always renders them for anonymous
    visitors, and any other unmappable field is dropped with a warning: a
    registration submission writes to the Person record, so there is nowhere
    to put a free-form answer.

    As in build_form_page, everything in #content other than the <form>
    becomes an "html" block ahead of the inputs, preserving the intro copy,
    and only the field descriptions are imported — never the answers that
    the snapshot may show for one already-signed-up member.
    """
    # Work on a private copy: extracting the <form> below must not mutate the
    # caller's soup, which a --replace re-import passes in again.
    document_soup = copy.copy(document_soup)
    content = document_soup.find(id="content")
    form_tag = content.find("form") if content else None

    if content is not None and form_tag is not None:
        form_tag.extract()
        remaining_html = _prettify(content)
    else:
        remaining_html = importable_html

    allowed_person_fields = set(registration_person_field_names())
    body_blocks: List[Tuple[str, Any]] = []
    if remaining_html.strip():
        body_blocks.append(("html", remaining_html))
    if form_tag is not None:
        for form_input in extract_form_inputs(form_tag):
            if form_input.field_name in _IDENTITY_FIELD_NAMES:
                continue
            person_field = _LEGACY_SIGNUP_FIELD_ALIASES.get(
                form_input.field_name, form_input.field_name
            )
            if person_field not in allowed_person_fields:
                logger.warning(
                    "Skipping the legacy signup field %r (%r) on %r: it does not match "
                    "any whitelisted Person field.",
                    form_input.field_name,
                    form_input.description,
                    slug,
                )
                continue
            body_blocks.append(
                (
                    "person_field",
                    {"field": person_field, "label_override": form_input.description},
                )
            )

    kwargs = get_page_args(
        document_soup=document_soup,
        importable_html=importable_html,
        attributes=attributes,
        slug=slug,
        site=site,
    )
    kwargs.pop("show_toc")
    # RegistrationPage descends from FormServingPage, not BasicPage, so it
    # has no legacy_id field.
    kwargs.pop("legacy_id")
    kwargs["body"] = body_blocks
    return RegistrationPage(**kwargs)


def iter_html_files_breadth_first(domain_dir: Path, pages_file: Path) -> Iterator[Path]:
    """The `*.html` files of `domain_dir`, each after the file of its parent page.

    `pages_file` is the page graph that build_page_graph writes (`all_pages.json`).
    A redirection is listed beneath the page that it redirects to, so it too follows its
    destination.

    A page in the graph that was never fetched is passed over. A fetched file that the
    graph does not reach (it is absent from the graph, or its parent is) comes last,
    in name order.
    """
    available = {path.stem: path for path in domain_dir.glob("*.html")}
    visited: set[str] = set()
    for node in iter_pages_breadth_first(read_page_graph(pages_file)):
        visited.add(node.slug)
        if node.slug in available:
            yield available[node.slug]
    for stem in sorted(available.keys() - visited):
        yield available[stem]


# fetch_pages --with-pagination saves each further page of a listing as
# "<slug>?page=<number>.html". They are read as part of the listing they belong
# to, never imported as pages of their own.
_PAGINATED_FILE_STEM = re.compile(r"^(?P<slug>.+)\?page=(?P<number>\d+)$")


def find_paginated_pages(domain_dir: Path, slug: str) -> List[Path]:
    """The further pages of the listing `slug` that were fetched into
    `domain_dir`, in page order."""
    found = []
    for path in domain_dir.glob("*.html"):
        match = _PAGINATED_FILE_STEM.match(path.stem)
        if match and match["slug"] == slug:
            found.append((int(match["number"]), path))
    return [path for _, path in sorted(found)]


BLOG_POST_TYPE_NAME = "Blog Post"

PAGE_BUILDING_MAP: dict[str, Any] = {
    "Basic": build_underground_basic_page,
    # A legacy calendar is a feed of events, so it becomes a FeedPage whose
    # ordering is soonest-first rather than a blog's newest-first.
    "Calendar": functools.partial(build_feed_page, ordering=FeedPage.Ordering.SOONEST_FIRST),
    "Donation": build_payment_page,
    "Donation (v2)": build_payment_page,
    "Event": build_event_page,
    "Blog": build_feed_page,
    BLOG_POST_TYPE_NAME: build_blog_post,
    "Redirect": build_redirection,
    "Signup": build_registration_page,
    "Volunteer Signup": build_form_page,
    "Feedback": build_form_page,
    "Suggestion Box": build_form_page,
    "Political Capital": build_underground_basic_page,
}


def get_unfilled_stub_intro(existing: Page, attributes: Dict[str, Any]) -> Optional[List[dict]]:
    """
    Get the `intro` of a stub blog post, if this Page is indeed a blog post with an
    empty body. Otherwise None.
    """
    if get_page_type_attribute(attributes) != BLOG_POST_TYPE_NAME:
        return None
    post = existing.specific
    if not isinstance(post, BlogPost) or do_blocks_have_visible_content(post.body.get_prep_value()):
        return None
    return [
        {"type": block["type"], "value": block["value"]} for block in post.intro.get_prep_value()
    ]


CHILD_STUB_BUILDING_MAP: dict[str, Any] = {
    "Blog": build_blog_post_stubs,
}


def get_page_type_attribute(attributes: Dict[str, Any]):
    return attributes.get("page_type_name")


def is_redirection(attributes: Dict[str, Any]):
    return get_page_type_attribute(attributes) == "Redirect"


def is_site_root(attributes: Dict[str, Any]) -> bool:
    """Return True if the legacy page's own path was "/", i.e. it was the
    legacy site's home page and should become the Wagtail site's root page
    rather than being imported as a child page."""
    return attributes.get("url_path") == "/"


def get_site_from_options(options):
    if options.get("site_id"):
        return Site.objects.get(id=options["site_id"])
    else:
        sites = Site.objects.all()
        if sites.count() > 1:
            raise ValueError(
                f"It is not clear which of the {sites.count()} sites to use − please specify a site_id"
            )
        return sites.first()


class Command(BaseCommand):
    help = "Parse and import legacy CMS pages into Wagtail from pre-fetched HTML files."
    counter = ImportCounter()
    skipped_slugs = set()

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--domain",
            required=True,
            help="Domain directory to import from (e.g. fusionparty.org.au).",
        )
        parser.add_argument(
            "--pages-file",
            type=Path,
            required=False,
            help=(
                "The page graph written by build_page_graph (<domain>/all_pages.json). "
                "Pages are imported breadth-first through it, so that each parent page is "
                "imported before its children. Without it, the files are imported in name order."
            ),
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Replace any existing Wagtail page that has the same slug.",
        )
        parser.add_argument(
            "--site-id",
            required=False,
            help="The Wagtail site ID where the pages should be imported.",
        )
        parser.add_argument(
            "--slug",
            required=False,
            help="The slug of the page to be imported",
        )
        parser.add_argument(
            "--no-image-internalization",
            action="store_true",
            help=(
                "Do not download the images a page references. Each <img> is left in a Raw "
                "HTML block pointing at its original URL instead of becoming an Image block."
            ),
        )
        parser.add_argument(
            "--no-document-internalization",
            action="store_true",
            help=(
                "Do not download the documents (PDFs and the like) referenced by a page. "
                "Each linked document remains in its original document store."
            ),
        )
        parser.add_argument(
            "--image-base-url",
            required=False,
            default="",
            help=(
                "Base URL for resolving relative image sources found in legacy bodies. "
                "Defaults to settings.WAGTAILADMIN_BASE_URL. Sources that stay relative are "
                "left as raw HTML."
            ),
        )

    def build_document_resolver(self, options) -> Optional[RemoteDocumentResolver]:
        """
        The resolver that internalizes the documents that every page in this run
        links to, or None when --no-document-internalization was passed.
        """
        if options.get("no_document_internalization"):
            return None
        return RemoteDocumentResolver(
            base_url=options.get("image_base_url") or settings.WAGTAILADMIN_BASE_URL,
            stdout=self.stdout,
        )

    def build_image_resolver(self, options) -> Optional[RemoteImageResolver]:
        """
        The resolver that internalizes the images of every page in this run, or
        None when --no-image-internalization was passed. Which images are worth
        fetching is decided by LEGACY_ASSET_URLS and SATISFACTORY_IMAGE_DOMAINS
        (see underground_crm.legacy_images).
        """
        if options.get("no_image_internalization"):
            return None
        return RemoteImageResolver(
            base_url=options.get("image_base_url") or settings.WAGTAILADMIN_BASE_URL,
            stdout=self.stdout,
        )

    def _replace_root_page(self, site: Site, old_root: Page, new_page: Page) -> Page:
        """
        Swap `new_page` in as the site's root (home) page in place of
        `old_root`, first moving `old_root`'s existing children so they
        remain in place as children of `new_page`, then deleting `old_root`
        (now childless) once it is no longer the site's root page.
        """
        children = list(old_root.get_children())
        if old_root.slug == new_page.slug:
            # Both pages are siblings until `old_root` is deleted below, so
            # Wagtail would reject `new_page` for reusing the slug.
            old_root.slug = f"{old_root.slug}-replaced-{old_root.pk}"
            old_root.save()
        old_root.get_parent().add_child(instance=new_page)
        for child in children:
            child.move(new_page, pos="last-child")
        site.root_page = new_page
        site.save()
        old_root.delete()
        return new_page

    def _should_continue_with_json_path(self, json_path: Path, slug: str):
        if json_path.exists():
            return True
        logger.error(f"[skip] no JSON metadata file for slug '{slug}'.")
        self.counter.increment_skipped()
        return False

    def _is_deleted_from_legacy_site(self, domain_dir: Path, slug: str) -> bool:
        json_path = domain_dir / f"{slug}.json"
        if not json_path.exists():
            return False
        attributes = _load_page_attributes(json_path)
        return bool(attributes) and attributes.get("status") == "deleted"

    def _is_redirection_on_file(self, domain_dir: Path, slug: str) -> bool:
        json_path = domain_dir / f"{slug}.json"
        if not json_path.exists():
            return False
        return is_redirection(_load_page_attributes(json_path) or {})

    def _get_attributes_for_continuation(self, json_path: Path) -> dict | None:
        attributes = _load_page_attributes(json_path)
        if attributes is None:
            logger.error(f"[skip] could not read attributes from '{json_path.name}'.")
            self.counter.increment_skipped()
            return None
        return attributes

    def _get_page_builder_for_continuation(
        self, attributes: dict, page_building_map: dict, slug: str, site: Site
    ) -> Optional[Callable]:
        type_name = get_page_type_attribute(attributes)
        if type_name in page_building_map:
            return page_building_map[type_name]
        logger.warning(f"[skip] unsupported page type '{type_name}' for slug '{slug}'.")
        self.counter.increment_skipped()
        self.skipped_slugs.add(slug)
        return None

    def _get_child_stub_builder(
        self, attributes: dict, stub_building_map: dict
    ) -> Optional[Callable]:
        return stub_building_map.get(get_page_type_attribute(attributes))

    def _create_child_stubs(
        self,
        stub_builder: Callable,
        document_soups: List[BeautifulSoup],
        attributes: dict,
        site: Site,
        parent: Page,
        image_resolver: Optional[ImageResolver] = None,
    ) -> None:
        """
        Save the stubs from each of the listing's pages.Pre-existing stubs are
        left alone.
        """
        for document_soup in document_soups:
            for stub in stub_builder(document_soup, attributes, site, image_resolver):
                if Page.objects.filter(slug=stub.slug).exists():
                    logger.warning(f"[skip] stub '{stub.slug}': a page with that slug exists.")
                    continue
                parent.add_child(instance=stub)
                stub.save_revision().publish()
                logger.info(
                    f"Created stub {stub.__class__.__name__} '{stub.slug}' under '{parent.title}'."
                )

    def _read_paginated_pages(self, domain_dir: Path, slug: str) -> List[BeautifulSoup]:
        """The parsed further pages of the listing `slug`, in page order."""
        soups = []
        for path in find_paginated_pages(domain_dir, slug):
            logger.info(f"Reading listing page '{path.name}'.")
            soups.append(BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser"))
        return soups

    def is_accounts_url(self, slug: str):
        if not slug:
            return False
        from underground_crm.urls import urlpatterns as underground_urls

        for urlpattern in underground_urls:
            matched = urlpattern.pattern.match(f"{slug.strip(punctuation)}/")
            if matched:
                return True
        return False

    def create_pages_from_path(
        self,
        domain_dir: Path,
        should_replace: bool,
        page_building_map: Dict[str, Page],
        site: Site,
        slug: Optional[str],
        child_stub_building_map: Optional[Dict[str, Callable]] = None,
        image_resolver: Optional[ImageResolver] = None,
        document_resolver: Optional[RemoteDocumentResolver] = None,
        pages_file: Optional[Path] = None,
    ) -> None:
        if not domain_dir.is_dir():
            raise CommandError(f"'{domain_dir}' is not a directory.")
        child_stub_building_map = child_stub_building_map or {}

        importable_dir = domain_dir / "importable"
        importable_dir.mkdir(exist_ok=True)

        if site is None:
            raise CommandError(
                "No Wagtail Site found in the database. "
                "Run migrations and create a site before importing pages."
            )

        root_page = site.root_page
        if root_page.get_parent() is None:
            raise CommandError(
                f"The site's root page ('{root_page.title}', pk={root_page.pk}) has no "
                "parent, so it appears to be Wagtail's own internal tree root rather "
                "than an ordinary home page. Site.root_page must point at a page "
                "beneath that tree root — repoint the site's root page (Wagtail admin: "
                "Settings > Sites, or via the Django shell) before importing pages."
            )
        logger.info(f"Importing pages under '{root_page.title}' (pk={root_page.pk}).")

        # Note that even redirections will need the HTML here (for the resultant page in the redirection)
        html_files = (
            iter_html_files_breadth_first(domain_dir, pages_file)
            if pages_file
            else sorted(domain_dir.glob("*.html"))
        )
        if not html_files:
            logger.error(f"No .html files found in '{domain_dir}'.")
            return
        # A redirection is generally placed beneath a folder of redirections, rather
        # than beneath its destination, so it is imported after every page that it
        # could lead to.
        html_files = sorted(
            html_files, key=lambda path: self._is_redirection_on_file(domain_dir, path.stem)
        )

        for html_file in html_files:
            if _PAGINATED_FILE_STEM.match(html_file.stem):
                continue
            current_slug = html_file.stem
            if slug and current_slug != slug:
                continue
            json_path = domain_dir / f"{current_slug}.json"
            if not self._should_continue_with_json_path(json_path, current_slug):
                continue

            attributes = self._get_attributes_for_continuation(json_path)
            if not attributes:
                continue

            if attributes.get("status") == "deleted":
                logger.warning("[skip] %s has been deleted and shall be skipped", current_slug)
                self.skipped_slugs.add(current_slug)
                self.counter.increment_skipped()
                continue

            page_builder = self._get_page_builder_for_continuation(
                attributes, page_building_map, slug=current_slug, site=site
            )
            if not page_builder:
                continue
            logger.info("Importing page %s", current_slug)

            extracted = extract_importable_html(html_file, importable_dir, document_resolver)
            if extracted is None:
                logger.warning(f"[skip] no element with id='content' found in '{html_file.name}'.")
                self.counter.increment_skipped()
                continue

            logger.info(f"Wrote parsed content to '{importable_dir / html_file.name}'.")
            document_soup, importable_html = extracted

            replacing_root = is_site_root(attributes)
            is_replacing = False
            stub_intro: Optional[List[dict]] = None

            if replacing_root:
                if root_page.slug == current_slug and not should_replace:
                    logger.warning(
                        f"[skip] the site's root page is already '{current_slug}'. "
                        "Pass --replace to overwrite it."
                    )
                    self.counter.increment_skipped()
                    continue
                is_replacing = root_page.slug == current_slug
            else:
                if is_redirection(attributes):
                    existing = Redirect.objects.filter(
                        old_path=Redirect.normalise_path(current_slug), site=site
                    ).first()
                else:
                    existing = Page.objects.filter(slug=current_slug).first()

                if existing is not None:
                    if isinstance(existing, Page):
                        stub_intro = get_unfilled_stub_intro(existing, attributes)
                    if stub_intro is None and not should_replace:
                        logger.warning(
                            f"[skip] page with slug '{current_slug}' already exists. "
                            "Pass --replace to overwrite it."
                        )
                        self.counter.increment_skipped()
                        continue
                    logger.info(
                        f"  Deleting existing {'stub ' if stub_intro is not None else ''}page "
                        f"'{current_slug}' (pk={getattr(existing, 'pk', None)}) for replacement."
                    )
                    existing.delete()
                    root_page.refresh_from_db()
                    is_replacing = True

            # Only a Blog Post builder can use a stub's intro.
            builder_extras = {"existing_intro": stub_intro} if stub_intro is not None else {}
            if image_resolver is not None:
                builder_extras["image_resolver"] = image_resolver
            if is_redirection(attributes):
                builder_extras["legacy_domain"] = domain_dir.name
            new_page: UndergroundBasicPage = page_builder(
                document_soup=document_soup,
                importable_html=importable_html,
                attributes=attributes,
                slug=current_slug,
                site=site,
                **builder_extras,
            )
            if new_page is None:
                logger.warning(f"[skip] {current_slug} could not be built.")
                self.counter.increment_skipped()
                continue
            if replacing_root:
                old_root = root_page
                new_page = self._replace_root_page(site, old_root, new_page)
                root_page = new_page
                logger.info(
                    f"  Updated the site's root page (was '{old_root.title}') to "
                    f"{new_page.__class__.__name__} (slug='{current_slug}'), keeping its subpages in place."
                )
            else:
                immediate_parent = root_page
                if isinstance(new_page, Page):
                    parent_slug = attributes.get("parent_slug")
                    if parent_slug:
                        try:
                            potential_parent = Page.objects.get(slug=parent_slug)
                            immediate_parent = potential_parent
                        except Page.DoesNotExist as e:
                            if self.is_accounts_url(parent_slug):
                                logger.info(
                                    "%s shall be a child of the root, rather than a child of %s",
                                    slug,
                                    parent_slug,
                                )
                            elif (
                                parent_slug in self.skipped_slugs
                                or self._is_deleted_from_legacy_site(domain_dir, parent_slug)
                            ):
                                logger.warning(
                                    "[skip] %s is being skipped, since its parent %s is not being migrated",
                                    current_slug,
                                    parent_slug,
                                )
                                self.skipped_slugs.add(current_slug)
                                self.counter.increment_skipped()
                                continue
                            else:
                                raise FileNotFoundError(f"Parent page {parent_slug} does not exist")
                    immediate_parent.add_child(instance=new_page)
                elif isinstance(new_page, Redirect):
                    new_page.save()

                logger.info(
                    f"Created {new_page.__class__.__name__} '{getattr(new_page, 'slug', None) or getattr(new_page, 'old_path', None)}'"
                    f"(slug='{current_slug}') under '{immediate_parent.title}' (pk={immediate_parent.pk})."
                )

            if isinstance(new_page, Page):
                # add_child() saves only the page row: the page would be live
                # but have no revision, so no history and no live_revision.
                new_page.save_revision().publish()
                stub_builder = self._get_child_stub_builder(attributes, child_stub_building_map)
                if stub_builder:
                    self._create_child_stubs(
                        stub_builder,
                        [document_soup, *self._read_paginated_pages(domain_dir, current_slug)],
                        attributes,
                        site,
                        parent=new_page,
                        image_resolver=image_resolver,
                    )

            if is_replacing:
                self.counter.increment_replaced()
            else:
                self.counter.increment_imported()

        if slug and not self.counter.has_evaluated_any_pages():
            raise CommandError(f"'{slug}' was not available for importing.")
        if isinstance(image_resolver, RemoteImageResolver):
            logger.info(f"Images: {image_resolver.get_summary()}.")
        if isinstance(document_resolver, RemoteDocumentResolver):
            logger.info(f"Documents: {document_resolver.get_summary()}.")
        logger.info("Done. %s", self.counter.get_summary())

    def handle(self, *args, **options) -> None:
        domain_dir = Path(options["domain"])
        return self.create_pages_from_path(
            domain_dir=domain_dir,
            should_replace=options["replace"],
            page_building_map=PAGE_BUILDING_MAP,
            site=get_site_from_options(options),
            slug=options.get("slug"),
            child_stub_building_map=CHILD_STUB_BUILDING_MAP,
            image_resolver=self.build_image_resolver(options),
            document_resolver=self.build_document_resolver(options),
            pages_file=options.get("pages_file"),
        )
