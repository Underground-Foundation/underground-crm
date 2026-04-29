import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

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
        {"page[size": page_number, "page[size]": page_size, "filter[site_id]": site_id}
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
    records = data.get("data", [])
    if not records:
        return None, f"No pages found for page number '{page_number}'."
    return records, None


def fetch_page_html(domain: str, slug: str, html_opener):
    """Return (html_bytes, None) on success or (None, error_string) on failure."""
    url = f"https://{domain}/{slug}"
    try:
        with html_opener.open(url) as resp:
            return resp.read(), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} fetching {url}"


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
