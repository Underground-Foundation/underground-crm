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

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from bs4 import BeautifulSoup, Tag
from django.core.management.base import BaseCommand, CommandError
from wagtail.models import Page, Site

from underground_crm.models import UndergroundBasicPage

PAGE_TYPE_MAP: dict[str, type[UndergroundBasicPage]] = {
    "Basic": UndergroundBasicPage,
}


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
    soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
    content = soup.find(id="content")
    if content is None:
        return None
    html_content = _prettify(content)
    (importable_dir / html_file.name).write_text(html_content, encoding="utf-8")
    return soup, html_content

def build_importable_page(
    page_class: type[UndergroundBasicPage],
    slug: str,
    title: str,
    seo_title: str,
    html_content: str,
    soup: BeautifulSoup,
) -> UndergroundBasicPage:
    """Build and return an unsaved Wagtail page from imported HTML content.

    The caller is responsible for saving it via parent_page.add_child().
    Subclasses of UndergroundBasicPage are accepted; any fields they add
    beyond the base set must be set on the returned instance before saving.
    """
    page_kwargs: dict = {
        "title": title,
        "slug": slug,
        "seo_title": seo_title,
        "body": json.dumps([{"type": "html", "value": html_content}]),
        "show_toc": should_show_toc(soup),
    }
    return page_class(**page_kwargs)


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

    def _get_type_name_for_continuation(self, attributes: dict, page_type_map: dict, slug: str):
        type_name = attributes.get("page_type_name")
        if type_name in page_type_map:
            return True
        self.stderr.write(
            f"  [skip] unsupported page type '{type_name}' for slug '{slug}'."
        )
        self.counter.increment_skipped()
        return False

    def create_pages_from_path(self, domain_dir: Path, should_replace: bool, page_type_map: Dict[str, Page]) -> None:
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

            type_name = self._get_type_name_for_continuation(attributes, page_type_map, slug)
            if not type_name:
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

            soup, html_content = extracted
            self.stdout.write(f"  Wrote parsed content to '{importable_dir / html_file.name}'.")

            page_class = page_type_map[type_name]
            # The name attribute is an administrative label for the page. The headline is what public viewers see as a
            # prominent h1.
            name = attributes.get("name") or attributes.get("headline") or slug
            seo_title = attributes.get("title", "")

            new_page = build_importable_page(
                page_class, slug, name, seo_title, html_content, soup
            )
            parent_page.add_child(instance=new_page)

            self.stdout.write(
                f"  Created {page_class.__name__} '{name}'"
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
        return self.create_pages_from_path(domain_dir=domain_dir, should_replace=options["replace"], page_type_map=PAGE_TYPE_MAP)
