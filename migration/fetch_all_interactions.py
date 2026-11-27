#!/usr/bin/env python3
"""
Fetch all interactions (contacts) from the legacy party website API.

Interactions correspond to logged contacts between staff and people —
phone calls, face-to-face meetings, SMS, etc.

Usage, from the underground-crm root directory:
  python -m migration.fetch_all_interactions [person_id]
  python -m migration.fetch_all_interactions 27322

Without a person_id, pages through all interactions across all people.
Outputs newline-delimited JSON to stdout. Redirect to a file for import.

Fetching and normalizing are done by fetch_interactions_via_api() in
underground_crm/management/commands/importing.py, which defines the fields of
each output record; this script only prints the result. Importing that module
requires Django, so run this script from the underground-crm virtualenv, as a
module (python -m) so that the root directory is on sys.path.

Reads LEGACY_ADMIN_URL, LEGACY_API_TOKEN, and LEGACY_USER_AGENT from
the environment (see ../.env).
"""

import argparse
import json
import sys

from django.core.management.base import CommandError, OutputWrapper

# config must be imported first: it loads ../.env, and importing.py reads
# LEGACY_USER_AGENT from the environment when it is imported.
from migration.config import LEGACY_API_TOKEN, LEGACY_USER_AGENT, LEGACY_ADMIN_URL
from underground_crm.management.commands.importing import (
    fetch_interactions_via_api,
    make_legacy_api_headers,
)

parser = argparse.ArgumentParser(description="Fetch interactions (contacts) from the legacy API.")
parser.add_argument(
    "person_id", nargs="?", type=int, help="Fetch only interactions for this person ID."
)
args = parser.parse_args()

if args.person_id:
    print(f"Fetching interactions for person {args.person_id}...", file=sys.stderr)
else:
    print("Fetching all interactions...", file=sys.stderr)

try:
    interactions = fetch_interactions_via_api(
        LEGACY_ADMIN_URL,
        make_legacy_api_headers(LEGACY_API_TOKEN, LEGACY_USER_AGENT),
        legacy_person_id=args.person_id,
        stdout=OutputWrapper(sys.stderr),
    )
except CommandError as e:
    print(f"  [error] {e}", file=sys.stderr)
    sys.exit(1)

for interaction in interactions:
    print(json.dumps(interaction))

print(f"\nDone. Total interactions: {len(interactions)}", file=sys.stderr)
