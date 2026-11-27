#!/usr/bin/env python3
"""
Fetch all private notes from the legacy party website using admin session cookies.

Usage, from the underground-crm root directory:
  python -m migration.fetch_all_private_notes [person_id]
  python -m migration.fetch_all_private_notes 27322

Without a person_id, pages through all signups and fetches notes for each.
Outputs newline-delimited JSON to stdout. Redirect to a file for import.

Fetching and parsing are done by fetch_private_notes() in
underground_crm/management/commands/importing.py, which defines the fields of
each output record; this script only selects the people and prints the result.
Importing that module requires Django, so run this script from the
underground-crm virtualenv. It must be run as a module (python -m) so that the
root directory is on sys.path and both the migration and underground_crm
packages can be imported.

Reads LEGACY_ADMIN_URL, LEGACY_API_TOKEN, LEGACY_USER_AGENT, and
LEGACY_ADMIN_COOKIE_FILE from the environment (see ../.env).
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse

# config must be imported first: it loads ../.env, and importing.py reads
# LEGACY_USER_AGENT from the environment when it is imported.
from migration.config import (
    LEGACY_API_TOKEN,
    LEGACY_ADMIN_COOKIE_FILE,
    LEGACY_USER_AGENT,
    LEGACY_ADMIN_URL,
)
from underground_crm.management.commands.importing import (
    get_legacy_api_json,
    build_cookie_opener,
    fetch_private_notes,
    make_legacy_api_headers,
)


def fetch_all_person_ids():
    """Page through all signups via the API to collect person IDs."""
    api_headers = make_legacy_api_headers(LEGACY_API_TOKEN, LEGACY_USER_AGENT)
    ids = []
    next_cursor = None
    page = 0
    while True:
        params = {"limit": 100}
        if next_cursor:
            params["next"] = next_cursor
        try:
            data, _ = get_legacy_api_json(LEGACY_ADMIN_URL, "/api/v2/signups", api_headers, params)
        except urllib.error.URLError as e:
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

opener = build_cookie_opener(LEGACY_ADMIN_COOKIE_FILE)

all_notes = []
for i, pid in enumerate(person_ids):
    try:
        notes = fetch_private_notes(opener, LEGACY_ADMIN_URL, pid)
    except urllib.error.URLError as e:
        print(f"  [warn] person {pid}: {e}", file=sys.stderr)
        continue
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
