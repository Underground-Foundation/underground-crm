#!/usr/bin/env python3
"""
Fetch a single page from the legacy party website's v2 API and its rendered HTML.

Usage:
  python fetch_pages.py <domain> <slug>

Creates a directory named <domain>/ (if it does not already exist), then writes:
  <domain>/<slug>.json   — the JSON:API record from /api/v2/pages?filter[slug]=<slug>
  <domain>/<slug>.html   — the rendered HTML from https://<domain>/<slug>

Reads LEGACY_API_TOKEN, LEGACY_WEBSITE_URL, and LEGACY_USER_AGENT from the
environment (see ../.env).
"""

import argparse
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config import LEGACY_API_TOKEN, LEGACY_ADMIN_COOKIE_FILE, LEGACY_USER_AGENT, LEGACY_WEBSITE_URL

API_HEADERS = {
    "Authorization": f"Bearer {LEGACY_API_TOKEN}",
    "Accept": "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
    "User-Agent": LEGACY_USER_AGENT,
}

# Cookie-aware opener for fetching rendered HTML pages.
_cookie_jar = http.cookiejar.MozillaCookieJar(LEGACY_ADMIN_COOKIE_FILE)
_cookie_jar.load(ignore_discard=True, ignore_expires=True)
_html_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))
_html_opener.addheaders = [
    ("User-Agent", LEGACY_USER_AGENT),
    ("Accept", "text/html"),
]


def api_get(path, params=None):
    url = f"{LEGACY_WEBSITE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=API_HEADERS)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"error": str(e), "body": body}, e.code


def fetch_page_json(slug):
    """Return the JSON:API record for a single slug, or None on failure."""
    data, status = api_get("/api/v2/pages", {"filter[slug]": slug, "page[size]": 1})
    if status != 200:
        print(f"[error] API returned HTTP {status} for slug '{slug}': {data}", file=sys.stderr)
        return None
    records = data.get("data", [])
    if not records:
        print(f"[error] No page found with slug '{slug}'.", file=sys.stderr)
        return None
    return records[0]


def fetch_page_html(domain, slug):
    """Return the rendered HTML bytes for https://<domain>/<slug>."""
    url = f"https://{domain}/{slug}"
    try:
        with _html_opener.open(url) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        print(f"[error] HTTP {e.code} fetching {url}", file=sys.stderr)
        return None


# ---- Main ----

parser = argparse.ArgumentParser(description="Fetch a single legacy page's JSON and HTML.")
parser.add_argument("--domain", required=True, help="Domain to fetch the rendered HTML from (e.g. example.com).")
parser.add_argument("--slug", required=True, help="Page slug to fetch.")
args = parser.parse_args()

output_dir = Path(args.domain)
output_dir.mkdir(exist_ok=True)

print(f"Fetching JSON for slug '{args.slug}'...", file=sys.stderr)
page_json = fetch_page_json(args.slug)
if page_json is None:
    sys.exit(1)

json_path = output_dir / f"{args.slug}.json"
json_path.write_text(json.dumps(page_json, indent=2))
print(f"Saved {json_path}", file=sys.stderr)

print(f"Fetching HTML from https://{args.domain}/{args.slug}...", file=sys.stderr)
html_bytes = fetch_page_html(args.domain, args.slug)
if html_bytes is None:
    sys.exit(1)

html_path = output_dir / f"{args.slug}.html"
html_path.write_bytes(html_bytes)
print(f"Saved {html_path}", file=sys.stderr)
