#!/usr/bin/env python3
"""
Fetch all private notes from the legacy party website using admin session cookies.

Usage:
  python fetch_all_private_notes.py [person_id]
  python fetch_all_private_notes.py 27322

Without a person_id, pages through all signups and fetches notes for each.
Outputs newline-delimited JSON to stdout. Redirect to a file for import.

Reads LEGACY_WEBSITE_URL, LEGACY_API_TOKEN, LEGACY_USER_AGENT, and
LEGACY_ADMIN_COOKIE_FILE from the environment (see ../.env).
"""

import argparse
import html
import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request

from config import LEGACY_API_TOKEN, LEGACY_ADMIN_COOKIE_FILE, LEGACY_USER_AGENT, LEGACY_WEBSITE_URL

# --- HTTP setup with cookie jar ---
cookie_jar = http.cookiejar.MozillaCookieJar(LEGACY_ADMIN_COOKIE_FILE)
cookie_jar.load(ignore_discard=True, ignore_expires=True)
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
opener.addheaders = [
    ("User-Agent", LEGACY_USER_AGENT),
    ("Accept", "application/json, text/javascript, */*; q=0.01"),
    ("X-Requested-With", "XMLHttpRequest"),
]


def get(path, params=None, referer=None):
    url = f"{LEGACY_WEBSITE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"Referer": referer or f"{LEGACY_WEBSITE_URL}/admin/signups"},
    )
    try:
        with opener.open(req) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"error": str(e), "body": body}, e.code


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def extract_note_text(oneliner):
    """Extract the note body from the activity_content_text div."""
    match = re.search(r'<div class="activity_content_text">(.*?)</div>', oneliner, re.DOTALL)
    if match:
        return strip_html(match.group(1))
    return ""


def fetch_private_notes_for_person(person_id):
    """Fetch all private note activities for a person, all pages."""
    notes = []
    page = 1
    while True:
        params = {"id": person_id, "page": page, "range": "All time", "type_id": ""}
        data, status = get(
            "/admin/activities/signup.json",
            params,
            referer=f"{LEGACY_WEBSITE_URL}/admin/signups/{person_id}",
        )
        if status != 200:
            print(f"  [warn] person {person_id} page {page}: HTTP {status}", file=sys.stderr)
            break

        activities = data.get("activities", [])
        if not activities:
            break

        for act in activities:
            if act.get("type") == "profile_private_note":
                related = act.get("relatedSignups", {})
                author = related.get("author", {})
                subject = related.get("signup", {})
                notes.append(
                    {
                        "activity_id": act.get("id"),
                        "person_legacy_id": subject.get("id") or person_id,
                        "person_name": subject.get("name", ""),
                        "author_legacy_id": author.get("id"),
                        "author_name": author.get("name", ""),
                        "text": extract_note_text(act.get("oneliner", "")),
                        "is_private": act.get("isPrivate", True),
                        "created_at": act.get("timestamp", ""),
                    }
                )

        if len(activities) < 20:
            break
        page += 1

    return notes


def fetch_all_person_ids():
    """Page through all signups via the API to collect person IDs."""
    ids = []
    next_cursor = None
    page = 0
    while True:
        params = {"limit": 100}
        if next_cursor:
            params["next"] = next_cursor
        url = f"{LEGACY_WEBSITE_URL}/api/v2/signups?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {LEGACY_API_TOKEN}",
                "Accept": "application/json",
                "User-Agent": LEGACY_USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"  [error] listing signups: {e}", file=sys.stderr)
            break

        for item in data.get("data", []):
            ids.append(int(item["id"]))

        next_url = data.get("links", {}).get("next")
        if not next_url:
            break
        parsed = urllib.parse.urlparse(next_url)
        next_cursor = urllib.parse.parse_qs(parsed.query).get("next", [None])[0]
        page += 1
        if page % 10 == 0:
            print(f"  [info] listed {len(ids)} people so far...", file=sys.stderr)

    return ids


# ---- Main ----

parser = argparse.ArgumentParser(description="Fetch private notes from the legacy CRM.")
parser.add_argument("person_id", nargs="?", type=int, help="Fetch only notes for this person ID.")
args = parser.parse_args()

if args.person_id:
    person_ids = [args.person_id]
else:
    print("Fetching all person IDs...", file=sys.stderr)
    person_ids = fetch_all_person_ids()
    print(f"Found {len(person_ids)} people. Fetching private notes...", file=sys.stderr)

all_notes = []
for i, pid in enumerate(person_ids):
    notes = fetch_private_notes_for_person(pid)
    for n in notes:
        print(json.dumps(n))
        all_notes.append(n)
    if notes:
        print(
            f"  [{i+1}/{len(person_ids)}] Person {pid}: {len(notes)} private note(s)",
            file=sys.stderr,
        )
    time.sleep(0.1)

print(f"\nDone. Total private notes: {len(all_notes)}", file=sys.stderr)
