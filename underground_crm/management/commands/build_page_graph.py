import io
import json
from typing import Optional

from django.core.management.base import BaseCommand, CommandError

from underground_crm.management.commands.legacy_api_client import (
    MAX_PAGE_SIZE,
    # noqa: F401 (re-exported)
    fetch_pages_json,
    get_api_headers,
)
from underground_crm.management.commands.importing import (
    require_env,
    PageNode,
    get_pages_file_path,
    read_page_graph,
)


class Command(BaseCommand):
    help = "Fetch the complete list of pages from the legacy CMS and use it to build a graph"

    def write_pages_for_domain(self, domain: str):
        pages_file_path = get_pages_file_path(domain)

        site_id = int(require_env("LEGACY_SITE_ID"))
        admin_url = require_env("LEGACY_ADMIN_URL").rstrip("/")

        api_headers = get_api_headers()
        all_pages = []
        last_page: Optional[list] = None
        page_number = 1
        while last_page is None or len(last_page) >= MAX_PAGE_SIZE:
            self.stdout.write(f"Fetching page {page_number}")
            last_page, error_msg = fetch_pages_json(
                admin_url, api_headers, site_id=site_id, page_number=page_number
            )
            if error_msg is not None:
                # A real fetch failure (expired token, rate limit, HTTP error) must
                # never be treated the same as a legitimate empty last page — doing
                # so previously let a mid-crawl failure silently truncate all_pages.json
                # while still reporting success.
                raise CommandError(f"Aborting after fetching {len(all_pages)} page(s): {error_msg}")
            all_pages.extend(last_page)
            page_number += 1
        with io.open(pages_file_path, "w") as output_io:
            json.dump(all_pages, output_io, indent=2)
        self.stdout.write(f"Wrote {len(all_pages)} pages to {pages_file_path}")

    def build_graph_from_stored_pages(self, domain: str):
        return read_page_graph(get_pages_file_path(domain))

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            required=True,
            help="Domain to fetch the rendered HTML from (e.g. example.com).",
        )
        parser.add_argument(
            "--fetch-latest",
            action="store_true",
            help="Fetch the latest list of pages from the legacy site",
        )

    def handle(self, *args, **options):
        domain = options["domain"]
        if options.get("fetch_latest"):
            self.write_pages_for_domain(domain)
        graph = self.build_graph_from_stored_pages(domain)
        self.stdout.write(
            f"Extracted a graph with {len(graph)} roots: {'; '.join(g.slug for g in graph)}"
        )
