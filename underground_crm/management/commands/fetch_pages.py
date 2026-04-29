import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from underground_crm.management.commands.legacy_api_client import (
    fetch_page_html,
    fetch_page_json,
    get_api_headers,
    require_env,
)


def _make_html_opener(cookie_file, user_agent):
    cookie_jar = http.cookiejar.MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener.addheaders = [("User-Agent", user_agent), ("Accept", "text/html")]
    return opener


class Command(BaseCommand):
    help = "Fetch a single legacy page's JSON and HTML from the legacy website."

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            required=True,
            help="Domain to fetch the rendered HTML from (e.g. example.com).",
        )
        parser.add_argument(
            "--slug",
            required=True,
            help="Page slug to fetch.",
        )

    def handle(self, *args, **options):
        domain = options["domain"]
        slug = options["slug"]
        site_id = int(require_env("LEGACY_SITE_ID"))
        admin_url = require_env("LEGACY_ADMIN_URL").rstrip("/")
        user_agent = require_env("LEGACY_USER_AGENT")
        cookie_file = require_env("LEGACY_ADMIN_COOKIE_FILE")
        api_headers = get_api_headers()
        html_opener = _make_html_opener(cookie_file, user_agent)

        output_dir = Path(domain)
        output_dir.mkdir(exist_ok=True)

        self.stderr.write(f"Fetching JSON for slug '{slug}'...")
        record, error = fetch_page_json(slug, admin_url, api_headers=api_headers, site_id=site_id)
        if error:
            raise CommandError(error)

        json_path = output_dir / f"{slug}.json"
        json_path.write_text(json.dumps(record, indent=2))
        self.stderr.write(f"Saved {json_path}")

        self.stderr.write(f"Fetching HTML from https://{domain}/{slug}...")
        html_bytes, error = fetch_page_html(domain, slug, html_opener)
        if error:
            raise CommandError(error)

        html_path = output_dir / f"{slug}.html"
        html_path.write_bytes(html_bytes)
        self.stderr.write(f"Saved {html_path}")
