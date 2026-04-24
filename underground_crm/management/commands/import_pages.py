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
  "Basic" -> UndergroundBasicPage
"""
import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from django.db.models import Q
from django.contrib.auth import get_user_model
from typing import Any, Callable, Dict, Optional

from bs4 import BeautifulSoup, Tag
from django.core.management.base import BaseCommand, CommandError
from django.db.models.functions import Coalesce
from wagtail.models import Page, Site

from underground_crm.models import UndergroundBasicPage

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


def _extract_importable_html(
    html_file: Path,
    importable_dir: Path,
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
    (importable_dir / html_file.name).write_text(html_content, encoding="utf-8")
    return document_soup, html_content

def extract_author(soup) -> Optional[str]:
    byline = soup.find('div', class_='byline')
    if not byline:
        return None
    author_name = soup.find('span', class_='linked-signup-name')
    if not author_name:
        return None
    name_parts = author_name.text.split(" ")
    User = get_user_model()
    authors = User.objects.annotate(
        search_name=Coalesce('preferred_name', 'first_name')
    ).filter(
        # todo: use more than 2 words of a name
        Q(search_name__startswith=name_parts[0]),
        last_name__endswith=name_parts[-1]
    ).order_by(
        '-is_admin',    # True first
        '-is_staff',    # True first
        '-is_active'    # True first
    )
    if authors.count() > 1:
        print(f"Author {author_name} is ambiguous")
    if authors.count() == 0:
        print(f"Author {author_name} could not be found in our database. Please import users before pages")
    return authors.first()

def extract_og_image(head) -> Optional[str]:
    og_image = head.find('meta', property='og:image')
    return og_image.get("content")

def extract_og_type(head) -> Optional[str]:
    og_type = head.find('meta', property='og:type')
    return og_type.get("content")

def extract_og_description(head) -> Optional[str]:
    og_description = head.find('meta', property='og:description')
    return og_description.get("content")

def get_publication_date(attributes: Dict[str, Any]) -> Optional[datetime.datetime]:
    raw_date = attributes.get("published_at")
    if not raw_date:
        return None
    return datetime.datetime.fromisoformat(raw_date)

def build_underground_basic_page(document_soup, importable_html, attributes, slug,
                                 return_class=UndergroundBasicPage) -> UndergroundBasicPage:
    """
    Build and return an unsaved Wagtail page from imported HTML content.

    The caller is responsible for saving it via parent_page.add_child().
    Subclasses of UndergroundBasicPage are accepted; any fields they add
    beyond the base set must be set on the returned instance before saving.
    """
    # The name attribute is an administrative label for the page. The headline is what public viewers see as a
    # prominent h1.
    name = attributes.get("name") or attributes.get("headline") or slug
    head = document_soup.find("head")
    seo_title = attributes.get("title", "")
    seo_image_url = extract_og_image(head)
    if seo_image_url:
        # This cannot be used in the importing script just yet, as the search_image property of PageWithMetadata is a
        # hosted Wagtail image, not a URL.
        pass
    author=extract_author(document_soup)
    return return_class(
        title=seo_title,
        slug=slug,
        owner=author,
        seo_title=seo_title,
        search_description=extract_og_description(head),
        latest_revision_created_at=get_publication_date(attributes),
        author=author,
        og_type=extract_og_type(head),
        body=json.dumps([{"type": "html", "value": importable_html}]),
        show_toc=should_show_toc(document_soup)
    )


PAGE_BUILDING_MAP: dict[str, Any] = {
    "Basic": build_underground_basic_page,
}

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

    def _get_page_builder_for_continuation(self, attributes: dict, page_building_map: dict,
                                           slug: str) -> Optional[Callable]:
        type_name = attributes.get("page_type_name")
        if type_name in page_building_map:
            return page_building_map[type_name]
        self.stderr.write(
            f"  [skip] unsupported page type '{type_name}' for slug '{slug}'."
        )
        self.counter.increment_skipped()
        return None

    def create_pages_from_path(self, domain_dir: Path, should_replace: bool, page_building_map: Dict[str, Page]) -> None:
        if not domain_dir.is_dir():
            raise CommandError(f"'{domain_dir}' is not a directory.")

        importable_dir = domain_dir / "importable"
        importable_dir.mkdir(exist_ok=True)

        site = Site.objects.filter(is_default_site=True).first() or Site.objects.first()
        if site is None:
            raise CommandError(
                "No Wagtail Site found in the database. "
                "Run migrations and create a site before importing pages."
            )

        parent_page = site.root_page
        self.stdout.write(f"Importing pages under '{parent_page.title}' (pk={parent_page.pk}).")

        html_files = sorted(domain_dir.glob("*.html"))
        if not html_files:
            self.stderr.write(self.style.WARNING(f"No .html files found in '{domain_dir}'."))
            return

        for html_file in html_files:
            slug = html_file.stem
            json_path = domain_dir / f"{slug}.json"
            if not self._should_continue_with_json_path(json_path, slug):
                continue

            attributes = self._get_attributes_for_continuation(json_path)
            if not attributes:
                continue

            page_builder = self._get_page_builder_for_continuation(attributes, page_building_map, slug)
            if not page_builder:
                continue

            existing = Page.objects.filter(slug=slug).first()
            is_replacing = False

            if existing is not None:
                if not should_replace:
                    self.stderr.write(
                        f"  [skip] page with slug '{slug}' already exists (pk={existing.pk}). "
                        "Pass --replace to overwrite it."
                    )
                    self.counter.increment_skipped()
                    continue
                self.stdout.write(
                    f"  Deleting existing page '{slug}' (pk={existing.pk}) for replacement."
                )
                existing.delete()
                is_replacing = True

            extracted = _extract_importable_html(html_file, importable_dir)
            if extracted is None:
                self.stderr.write(
                    f"  [skip] no element with id='content' found in '{html_file.name}'."
                )
                self.counter.increment_skipped()
                continue

            self.stdout.write(f"  Wrote parsed content to '{importable_dir / html_file.name}'.")
            document_soup, importable_html = extracted
            new_page = page_builder(document_soup=document_soup, importable_html=importable_html,
                                    attributes=attributes, slug=slug)
            parent_page.add_child(instance=new_page)

            self.stdout.write(
                f"  Created {new_page.__class__.__name__} '{new_page.name}'"
                f" (slug='{slug}') under '{parent_page.title}'."
            )

            if is_replacing:
                self.counter.increment_replaced()
            else:
                self.counter.increment_imported()

        self.stdout.write(self.style.SUCCESS(
            f"Done. {self.counter.get_summary()}"
        ))

    def handle(self, *args, **options) -> None:
        domain_dir = Path(options["domain"])
        return self.create_pages_from_path(domain_dir=domain_dir, should_replace=options["replace"],
                                           page_building_map=PAGE_BUILDING_MAP)
