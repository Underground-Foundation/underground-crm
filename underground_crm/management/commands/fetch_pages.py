import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


def _require_env(name):
    val = os.environ.get(name)
    if not val:
        raise CommandError(f"{name} environment variable is not set.")
    return val


def _make_html_opener(cookie_file, user_agent):
    cookie_jar = http.cookiejar.MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener.addheaders = [("User-Agent", user_agent), ("Accept", "text/html")]
    return opener


def _fetch_page_json(slug, website_url, api_headers):
    """Return (record_dict, None) on success or (None, error_string) on failure."""
    url = f"{website_url}/api/v2/pages?" + urllib.parse.urlencode(
        {"filter[slug]": slug, "page[size]": 1}
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


def _fetch_page_html(domain, slug, html_opener):
    """Return (html_bytes, None) on success or (None, error_string) on failure."""
    url = f"https://{domain}/{slug}"
    try:
        with html_opener.open(url) as resp:
            return resp.read(), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} fetching {url}"


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

        api_token = _require_env("LEGACY_API_TOKEN")
        website_url = _require_env("LEGACY_WEBSITE_URL").rstrip("/")
        user_agent = _require_env("LEGACY_USER_AGENT")
        cookie_file = _require_env("LEGACY_ADMIN_COOKIE_FILE")

        api_headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
            "User-Agent": user_agent,
        }
        html_opener = _make_html_opener(cookie_file, user_agent)

        output_dir = Path(domain)
        output_dir.mkdir(exist_ok=True)

        self.stderr.write(f"Fetching JSON for slug '{slug}'...")
        record, error = _fetch_page_json(slug, website_url, api_headers)
        if error:
            raise CommandError(error)

        json_path = output_dir / f"{slug}.json"
        json_path.write_text(json.dumps(record, indent=2))
        self.stderr.write(f"Saved {json_path}")

        self.stderr.write(f"Fetching HTML from https://{domain}/{slug}...")
        html_bytes, error = _fetch_page_html(domain, slug, html_opener)
        if error:
            raise CommandError(error)

        html_path = output_dir / f"{slug}.html"
        html_path.write_bytes(html_bytes)
        self.stderr.write(f"Saved {html_path}")
