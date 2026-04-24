#!/usr/bin/env python3
"""
Fetch pages from the legacy party website's v2 API.

Usage:
  python fetch_pages.py
  python fetch_pages.py <slug>

With a slug, fetches only the page matching that slug.
Without one, pages through all pages in the legacy system.

Outputs newline-delimited JSON to stdout. Redirect to a file for import.

Reads LEGACY_WEBSITE_URL, LEGACY_API_TOKEN, and LEGACY_USER_AGENT from
the environment (see ../.env).
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from config import LEGACY_API_TOKEN, LEGACY_USER_AGENT, LEGACY_WEBSITE_URL

HEADERS = {
    "Authorization": f"Bearer {LEGACY_API_TOKEN}",
    "Accept": "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
    "User-Agent": LEGACY_USER_AGENT,
}


def get(path, params=None):
    url = f"{LEGACY_WEBSITE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"error": str(e), "body": body}, e.code


def fetch_pages(slug=None):
    """Fetch pages from the v2 API, optionally filtered to a single slug."""
    pages = []
    params = {"page[size]": 100}
    if slug:
        params["filter[slug]"] = slug

    page_num = 1

    while True:
        params["page[number]"] = page_num
        data, status = get("/api/v2/pages", params)

        if status != 200:
            print(f"  [error] page {page_num}: HTTP {status} — {data}", file=sys.stderr)
            break

        records = data.get("data", [])
        pages.extend(records)

        if page_num % 5 == 0:
            print(f"  [info] fetched {len(pages)} pages so far (page {page_num})...", file=sys.stderr)

        # JSON:API uses links.next to signal whether more pages exist.
        if not records or not data.get("links", {}).get("next"):
            break

        page_num += 1
        time.sleep(0.1)

    return pages


# ---- Main ----

parser = argparse.ArgumentParser(description="Fetch pages from the legacy v2 API.")
parser.add_argument("slug", nargs="?", help="Fetch only the page with this slug.")
args = parser.parse_args()

if args.slug:
    print(f"Fetching page with slug '{args.slug}'...", file=sys.stderr)
else:
    print("Fetching all pages...", file=sys.stderr)

pages = fetch_pages(args.slug)

for page in pages:
    print(json.dumps(page))

print(f"\nDone. Total pages: {len(pages)}", file=sys.stderr)
