#!/usr/bin/env python3
"""
Fetch all interactions (contacts) from the legacy party website API.

Interactions correspond to logged contacts between staff and people —
phone calls, face-to-face meetings, SMS, etc.

Usage:
  python fetch_all_interactions.py [person_id]
  python fetch_all_interactions.py 27322

Without a person_id, pages through all interactions across all people.
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
    "Accept": "application/json",
    "Content-Type": "application/json",
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


def normalize(contact):
    """Flatten a raw contact record into a consistent dict."""
    return {
        "person_legacy_id": contact.get("person_id") or contact.get("recipient_id"),
        "author_legacy_id": contact.get("author_id") or contact.get("sender_id"),
        "method": contact.get("method", ""),
        "note": contact.get("note", "") or "",
        "status": contact.get("status", "") or "",
        "created_at": contact.get("created_at", ""),
        "contact_id": contact.get("contact_id"),
    }


def fetch_interactions_for_person(person_id):
    """Fetch all interactions for a specific person."""
    data, status = get(f"/api/v1/people/{person_id}/contacts", {"limit": 100})
    if status != 200:
        print(f"  [warn] person {person_id}: HTTP {status}", file=sys.stderr)
        return []
    return [normalize(c) for c in data.get("results", [])]


def fetch_all_interactions():
    """Page through all interactions across the entire database."""
    interactions = []
    next_cursor = None
    page = 0

    while True:
        params = {"limit": 100}
        if next_cursor:
            params["next"] = next_cursor

        data, status = get("/api/v1/contacts", params)
        if status != 200:
            print(f"  [error] page {page + 1}: HTTP {status} — {data}", file=sys.stderr)
            break

        results = data.get("results", [])
        next_cursor = data.get("next")
        page += 1

        for contact in results:
            interactions.append(normalize(contact))

        if page % 5 == 0:
            print(f"  [info] fetched {len(interactions)} interactions so far (page {page})...", file=sys.stderr)

        if not results or not next_cursor:
            break

        time.sleep(0.1)

    return interactions


# ---- Main ----

parser = argparse.ArgumentParser(description="Fetch interactions (contacts) from the legacy API.")
parser.add_argument("person_id", nargs="?", type=int, help="Fetch only interactions for this person ID.")
args = parser.parse_args()

if args.person_id:
    print(f"Fetching interactions for person {args.person_id}...", file=sys.stderr)
    interactions = fetch_interactions_for_person(args.person_id)
else:
    print("Fetching all interactions...", file=sys.stderr)
    interactions = fetch_all_interactions()

for interaction in interactions:
    print(json.dumps(interaction))

print(f"\nDone. Total interactions: {len(interactions)}", file=sys.stderr)
