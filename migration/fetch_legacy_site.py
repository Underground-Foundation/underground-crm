#!/usr/bin/env python3
"""
Crawl the legacy party website using the Cloudflare Browser Rendering API.

Initiates a crawl job for LEGACY_WEBSITE_URL, polls until it completes,
then outputs each crawled page as a newline-delimited JSON record to stdout.

Usage:
  python fetch_legacy_site.py
  python fetch_legacy_site.py --depth 3
  python fetch_legacy_site.py --formats markdown html

Reads LEGACY_WEBSITE_URL, CLOUDFLARE_TOKEN, and CLOUDFLARE_ACCOUNT_ID
from the environment (see ../.env).
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from config import CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_TOKEN, LEGACY_WEBSITE_URL

CRAWL_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/browser-rendering/crawl"
)

HEADERS = {
    "Authorization": f"Bearer {CLOUDFLARE_TOKEN}",
    "Content-Type": "application/json",
}

POLL_INTERVAL = 10  # seconds between status checks


def post(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"error": str(e), "body": body}, e.code


def get(url):
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"error": str(e), "body": body}, e.code


def start_crawl(url, depth, formats):
    payload = {
        "url": url,
        "depth": depth,
        "formats": formats,
    }
    print(f"Starting crawl for {url} (depth={depth}, formats={formats})...", file=sys.stderr)
    data, status = post(CRAWL_URL, payload)
    if status != 200:
        print(f"[error] Failed to start crawl: HTTP {status} — {data}", file=sys.stderr)
        sys.exit(1)
    job_id = data.get("id") or data.get("result", {}).get("id")
    if not job_id:
        print(f"[error] No job ID in response: {data}", file=sys.stderr)
        sys.exit(1)
    print(f"Crawl job started: {job_id}", file=sys.stderr)
    return job_id


def poll_until_done(job_id):
    """Poll the job status endpoint until the crawl is finished."""
    job_url = f"{CRAWL_URL}/{job_id}"
    terminal_statuses = {
        "completed",
        "cancelled_due_to_timeout",
        "cancelled_due_to_limits",
        "cancelled_by_user",
        "errored",
    }

    while True:
        data, status = get(job_url)
        if status != 200:
            print(f"[error] Polling failed: HTTP {status} — {data}", file=sys.stderr)
            sys.exit(1)

        result = data.get("result", data)
        job_status = result.get("status", "unknown")
        finished = result.get("finishedCount", 0)
        total = result.get("totalCount", "?")

        print(f"  [status] {job_status} — {finished}/{total} pages", file=sys.stderr)

        if job_status in terminal_statuses:
            if job_status != "completed":
                print(f"[warn] Crawl ended with status: {job_status}", file=sys.stderr)
            return result

        time.sleep(POLL_INTERVAL)


# ---- Main ----

parser = argparse.ArgumentParser(
    description="Crawl the legacy website via Cloudflare Browser Rendering."
)
parser.add_argument(
    "--depth",
    type=int,
    default=2,
    help="How many links deep to crawl from the start URL (default: 2).",
)
parser.add_argument(
    "--formats",
    nargs="+",
    default=["markdown"],
    choices=["markdown", "html", "screenshot", "content"],
    help="Content formats to capture for each page (default: markdown).",
)
args = parser.parse_args()

job_id = start_crawl(LEGACY_WEBSITE_URL, args.depth, args.formats)
result = poll_until_done(job_id)

records = result.get("records", [])
for record in records:
    print(json.dumps(record))

print(f"\nDone. Total pages crawled: {len(records)}", file=sys.stderr)
