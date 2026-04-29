import io
import json
from typing import Dict, List, Optional

from django.core.management.base import BaseCommand

from underground_crm.management.commands.legacy_api_client import (
    MAX_PAGE_SIZE,
    fetch_pages_json,
    get_api_headers,
    get_pages_file_path,
    require_env,
)


class PageNode:
    id: int
    parent_id: Optional[int]
    slug: str

    def __init__(self, id, parent_id, slug):
        self.id = int(id)
        self.parent_id = int(parent_id) if parent_id is not None else parent_id
        self.slug = slug
        self.parent: Optional["PageNode"] = None
        self.children: List["PageNode"] = []


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
            if error_msg or not last_page:
                break
            all_pages.extend(last_page)
            page_number += 1
        with io.open(pages_file_path, "w") as output_io:
            json.dump(all_pages, output_io, indent=2)
        self.stdout.write(f"Wrote {len(all_pages)} pages to {pages_file_path}")

    def build_graph_from_stored_pages(self, domain: str):
        with io.open(get_pages_file_path(domain)) as input_io:
            pages = json.load(input_io)
        page_mapping: Dict[int, PageNode] = {}
        for page in pages:
            node = PageNode(
                id=page["id"],
                parent_id=page["attributes"]["parent_id"],
                slug=page["attributes"]["slug"],
            )
            page_mapping[node.id] = node
        orphans = []
        for page in page_mapping.values():
            if page.parent_id:
                page.parent = page_mapping.get(page.parent_id)
                if page.parent:
                    page.parent.children.append(page)  # Doubly linked
                else:
                    print(
                        f"Page {page.slug} is being skipped from the graph because its parent {page.parent_id} is not here"
                    )
            else:
                orphans.append(page)
        return orphans  # There should be at least 1 orphan

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
