"""
Management command to parse and import legacy CMS pages into Wagtail from pre-fetched files.

Usage:
    python manage.py import_pages --domain <domain>
    python manage.py import_pages --domain <domain> --replace

For each <slug>.html found in <domain>/, the command:
  1. Reads <domain>/<slug>.json to determine the page type and title.
  2. Extracts the element with id="content" from the HTML and writes it
     to <domain>/importable/<slug>.html.
  3. Creates (or replaces) the corresponding Wagtail page using that content
     as a Raw HTML body block.

Supported page types:
  "Basic"    -> UndergroundBasicPage
  "Donation" -> PaymentPage
"""

import datetime
import json

import phonenumbers
from dataclasses import dataclass
from dateutil import parser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, cast
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
from underground_crm.models import Address, Blog, BasicPage, UndergroundBasicPage
from underground_crm.models.pages import EventPage, BlogPost
from underground_payments.models import PaymentPage
from underground_crm.numbers import parse_localized_number


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
        attributes = raw["data"].get("attributes", {})
    else:
        attributes = raw.get("attributes", {})
    return attributes or None


def extract_importable_html(
    html_file: Path,
    importable_dir: Optional[Path],
) -> tuple[BeautifulSoup, str] | None:
    """Extract the id='content' element, write it to importable_dir, and return
    the full-page soup alongside the extracted HTML string.

    Returns None if no id='content' element is found.
    """
    document_soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
    content = document_soup.find(id="content")
    if content is None:
        return None
    html_content = _prettify(content)
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

    parts = event_string.split(" - ")
    start_part = parts[0]
    remaining = parts[1]

    end_parts = remaining.split(" (")
    end_time_string = end_parts[0]
    timezone_label = end_parts[1].replace(")", "").replace(" timezone", "")

    # Split on "/" and take the first state/territory
    first_state = timezone_label.split("/")[0]
    iana_name = SUBDIVISION_TO_TIMEZONE.get(first_state) or settings.TIME_ZONE
    timezone_obj = ZoneInfo(iana_name)

    # Parse the start datetime
    naïve_start = datetime.datetime.strptime(start_part, "%B %d, %Y at %H:%M")
    start_datetime = naïve_start.replace(tzinfo=timezone_obj)
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
        print(f"Event time element was not found")
        return None, None
    time_contents = get_substantial_child_strings(event_pairs[0])
    if time_contents[0] != "When":
        print(f"Unable to determine time from wrong event detail")
        return None, None
    return parse_event_datetime(time_contents[1])


def extract_event_venue(soup: BeautifulSoup, create_if_not_found=True) -> Optional[Address]:
    pairs = get_event_detail_pairs(soup)
    if len(pairs) < 2:
        print("Unable to determine event location")
        return None
    location_pair = pairs[1]
    location_contents = get_substantial_child_strings(location_pair)
    if location_contents[0] != "Where":
        print("Unable to determine location from wrong event detail")
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
        print(f"Unable to determine an event host: {e}")
        return None
    User = get_user_model()
    try:
        return User.objects.get(phone_number=phone)
    except User.DoesNotExist:
        print(f"Unable to find a user with phone number {phone}")
        return None


def extract_host_attributes(soup: BeautifulSoup) -> Optional[List[str]]:
    # Returns the text describing the host and their contact details, eg `Owen · owen.miller@fusionparty.org.au`
    # Notice though, the email address is not visible in the initial HTML − it requires some JavaScript to execute,
    # revealing the email address on the page. It is therefore not extracted here.
    pairs = get_event_detail_pairs(soup)
    if len(pairs) < 3:
        print(f"Unable to determine event host. Only {len(pairs)} event pairs")
        return None
    host_pair = pairs[2]
    host_contents = get_substantial_child_strings(host_pair)
    if not host_contents or len(host_contents) != 2 or not host_contents[0] == "Contact":
        print("Unable to determine host from wrong event detail")
        return None
    print(f"Host contents: {host_contents}")
    host_text = host_contents[1]
    print(f"Raw host: {host_text}")
    return host_text.split("·")


def extract_event_host(soup: BeautifulSoup) -> Optional[settings.AUTH_USER_MODEL]:
    host_parts = extract_host_attributes(soup)
    print(f"Host parts: {host_parts}")
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
    return get_ambiguous_admin_by_full_name(host_parts[0].strip())


def extract_event_population(soup: BeautifulSoup):
    pairs = get_event_detail_pairs(soup)
    if len(pairs) < 4:
        print(f"Unable to determine event population. Only {len(pairs)} event pairs")
        return None
    for pair in pairs:
        subhead = pair.find("p", class_="subhead")
        if subhead:
            result = parse_localized_number(subhead.text.split(" ")[0])
            if result is not None:
                return int(result)
    print("Unable to determine event population")
    return None


def get_page_args(document_soup, importable_html, attributes, slug: str, site) -> dict:
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
    return {
        "title": headline,
        "slug": slug,
        "owner": author,
        "seo_title": seo_title,
        "search_description": extract_og_description(head),
        "latest_revision_created_at": get_publication_date(attributes),
        "author": author,
        "og_type": extract_og_type(head),
        "body": json.dumps([{"type": "html", "value": importable_html}]),
        "show_toc": should_show_toc(document_soup),
        "site": site,
    }


def build_underground_basic_page(
    document_soup: BeautifulSoup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=UndergroundBasicPage,
) -> UndergroundBasicPage:
    """
    Build and return an unsaved Wagtail page from imported HTML content.

    The caller is responsible for saving it via parent_page.add_child().
    Subclasses of UndergroundBasicPage are accepted; any fields they add
    beyond the base set must be set on the returned instance before saving.
    """
    return return_class(**get_page_args(document_soup, importable_html, attributes, slug, site))


def build_event_page(
    document_soup: BeautifulSoup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=EventPage,
) -> EventPage:
    kwargs = get_page_args(
        document_soup=document_soup,
        importable_html=importable_html,
        attributes=attributes,
        slug=slug,
        site=site,
    )
    kwargs.pop("show_toc")
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
        print(f"No content div could be found for this blog")
        return None
    ul = content_div.find(
        "ul"
    )  # The highest list in the content soup will be the list of blog posts
    if not ul:
        print(f"No ul could be found for this blog")
        return None
    list_items = ul.find_all("li", recursive=False)
    if not list_items:
        print(f"No list items for this blog")
        return None
    return len(list_items)


def build_blog_page(
    document_soup,
    importable_html: str,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=Blog,
) -> Blog:
    kwargs = get_page_args(
        document_soup=document_soup,
        importable_html=importable_html,
        attributes=attributes,
        slug=slug,
        site=site,
    )
    kwargs.pop("show_toc")
    page = cast(
        Blog,
        return_class(**kwargs),
    )
    page.page_size = extract_page_size(document_soup)
    return page


def build_redirection(
    document_soup,
    importable_html,
    attributes: Dict[str, Any],
    slug: str,
    site: Site,
    return_class=Redirect,
) -> Redirect:
    destination = attributes.get("parent_slug")
    try:
        destination_page = Page.objects.get(slug=destination)
    except Page.DoesNotExist:
        print(f"Cannot create redirection at {slug}: page {destination} does not exist")
        return None
    return cast(
        Redirect,
        return_class(
            old_path=slug,
            site=site,
            is_permanent=True,
            redirect_page=destination_page,
            redirect_page_route_path="",
            redirect_link="",
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
) -> PaymentPage:
    kwargs = get_page_args(document_soup, importable_html, attributes, slug, site)
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
) -> BlogPost:
    return build_underground_basic_page(
        document_soup=document_soup,
        importable_html=importable_html,
        attributes=attributes,
        slug=slug,
        site=site,
        return_class=return_class,
    )


PAGE_BUILDING_MAP: dict[str, Any] = {
    "Basic": build_underground_basic_page,
    "Donation": build_payment_page,
    "Event": build_event_page,
    "Blog": build_blog_page,
    "Blog Post": build_underground_basic_page,
    "Redirect": build_redirection,
}


def get_page_type_attribute(attributes: Dict[str, Any]):
    return attributes.get("page_type_name")


def is_redirection(attributes: Dict[str, Any]):
    return get_page_type_attribute(attributes) == "Redirect"


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

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--domain",
            required=True,
            help="Domain directory to import from (e.g. fusionparty.org.au).",
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

    def _should_continue_with_json_path(self, json_path: Path, slug: str):
        if json_path.exists():
            return True
        self.stderr.write(f"  [skip] no JSON metadata file for slug '{slug}'.")
        self.counter.increment_skipped()
        return False

    def _get_attributes_for_continuation(self, json_path: Path) -> dict | None:
        attributes = _load_page_attributes(json_path)
        if attributes is None:
            self.stderr.write(f"  [skip] could not read attributes from '{json_path.name}'.")
            self.counter.increment_skipped()
            return None
        return attributes

    def _get_page_builder_for_continuation(
        self, attributes: dict, page_building_map: dict, slug: str, site: Site
    ) -> Optional[Callable]:
        type_name = get_page_type_attribute(attributes)
        if type_name in page_building_map:
            return page_building_map[type_name]
        self.stderr.write(f"  [skip] unsupported page type '{type_name}' for slug '{slug}'.")
        self.counter.increment_skipped()
        return None

    def create_pages_from_path(
        self,
        domain_dir: Path,
        should_replace: bool,
        page_building_map: Dict[str, Page],
        site: Site,
        slug: Optional[str],
    ) -> None:
        if not domain_dir.is_dir():
            raise CommandError(f"'{domain_dir}' is not a directory.")

        importable_dir = domain_dir / "importable"
        importable_dir.mkdir(exist_ok=True)

        if site is None:
            raise CommandError(
                "No Wagtail Site found in the database. "
                "Run migrations and create a site before importing pages."
            )

        root_page = site.root_page
        self.stdout.write(f"Importing pages under '{root_page.title}' (pk={root_page.pk}).")

        # Note that even redirections will need the HTML here (for the resultant page in the redirection)
        html_files = sorted(domain_dir.glob("*.html"))
        if not html_files:
            self.stderr.write(self.style.WARNING(f"No .html files found in '{domain_dir}'."))
            return

        for html_file in html_files:
            current_slug = html_file.stem
            if slug and current_slug != slug:
                continue
            json_path = domain_dir / f"{current_slug}.json"
            if not self._should_continue_with_json_path(json_path, current_slug):
                continue

            attributes = self._get_attributes_for_continuation(json_path)
            if not attributes:
                continue

            page_builder = self._get_page_builder_for_continuation(
                attributes, page_building_map, slug=current_slug, site=site
            )
            if not page_builder:
                continue

            if is_redirection(attributes):
                existing = Redirect.objects.filter(old_path=current_slug).first()
            else:
                existing = Page.objects.filter(slug=current_slug).first()
            is_replacing = False

            if existing is not None:
                if not should_replace:
                    self.stderr.write(
                        f"  [skip] page with slug '{current_slug}' already exists. "
                        "Pass --replace to overwrite it."
                    )
                    self.counter.increment_skipped()
                    continue
                self.stdout.write(
                    f"  Deleting existing page '{current_slug}' (pk={getattr(existing, 'pk', None)}) for replacement."
                )
                existing.delete()
                root_page.refresh_from_db()
                is_replacing = True

            extracted = extract_importable_html(html_file, importable_dir)
            if extracted is None:
                self.stderr.write(
                    f"  [skip] no element with id='content' found in '{html_file.name}'."
                )
                self.counter.increment_skipped()
                continue

            self.stdout.write(f"  Wrote parsed content to '{importable_dir / html_file.name}'.")
            document_soup, importable_html = extracted
            new_page: UndergroundBasicPage = page_builder(
                document_soup=document_soup,
                importable_html=importable_html,
                attributes=attributes,
                slug=current_slug,
                site=site,
            )
            if isinstance(new_page, Page):
                parent_page_id = attributes.get("parent_id")
                if parent_page_id:
                    parent_page = BasicPage.objects.get(legacy_id=int(parent_page_id))
                    parent_page.add_child(instance=new_page)
                else:
                    root_page.add_child(instance=new_page)
            elif isinstance(new_page, Redirect):
                new_page.save()

            self.stdout.write(
                f"  Created {new_page.__class__.__name__} '{getattr(new_page, 'slug', None) or getattr(new_page, 'old_path', None)}'"
                f" (slug='{current_slug}') under '{root_page.title}'."
            )

            if is_replacing:
                self.counter.increment_replaced()
            else:
                self.counter.increment_imported()

        self.stdout.write(self.style.SUCCESS(f"Done. {self.counter.get_summary()}"))

    def handle(self, *args, **options) -> None:
        domain_dir = Path(options["domain"])
        return self.create_pages_from_path(
            domain_dir=domain_dir,
            should_replace=options["replace"],
            page_building_map=PAGE_BUILDING_MAP,
            site=get_site_from_options(options),
            slug=options.get("slug"),
        )
