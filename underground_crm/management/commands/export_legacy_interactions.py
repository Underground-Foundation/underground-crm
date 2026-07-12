"""
Management command to fetch interactions (contacts) from the legacy CRM and save
them to a JSON Lines (.jsonl) file, for later import via import_interactions.

Usage:
    python manage.py export_legacy_interactions --output-file interactions.jsonl
    python manage.py export_legacy_interactions --legacy-person-id <id> --output-file interactions.jsonl

Without --legacy-person-id, fetches interactions for all people in the legacy CRM.

Reads LEGACY_ADMIN_URL, LEGACY_API_TOKEN, and LEGACY_USER_AGENT from
the environment (see .env.example).
"""

from django.core.management.base import BaseCommand

from underground_crm.management.commands.importing import (
    fetch_interactions_via_api,
    make_legacy_api_headers,
    write_jsonl,
)
from underground_crm.management.commands.legacy_api_client import require_env

LEGACY_ADMIN_URL = require_env("LEGACY_ADMIN_URL").rstrip("/")
LEGACY_API_TOKEN = require_env("LEGACY_API_TOKEN")
LEGACY_USER_AGENT = require_env("LEGACY_USER_AGENT")


class Command(BaseCommand):
    help = "Fetch interactions from the legacy CRM and save them to a JSON Lines file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--legacy-person-id",
            type=int,
            default=None,
            help="Legacy CRM person ID to fetch interactions for. Omit to fetch all.",
        )
        parser.add_argument(
            "--output-file",
            required=True,
            help="Path to write the fetched interactions to, as newline-delimited JSON (.jsonl).",
        )

    def handle(self, *args, **options):
        legacy_person_id = options["legacy_person_id"]
        output_file = options["output_file"]

        if legacy_person_id:
            self.stdout.write(f"Fetching interactions for legacy ID {legacy_person_id}...")
        else:
            self.stdout.write("Fetching interactions for all people...")

        self.stdout.write(f"  Legacy CRM: {LEGACY_ADMIN_URL}")

        api_headers = make_legacy_api_headers(LEGACY_API_TOKEN, LEGACY_USER_AGENT)
        interactions = fetch_interactions_via_api(
            legacy_admin_url=LEGACY_ADMIN_URL,
            api_headers=api_headers,
            legacy_person_id=legacy_person_id,
            stdout=self.stdout,
        )

        self.stdout.write(f"  Found {len(interactions)} interaction(s).")

        write_jsonl(interactions, output_file)

        self.stdout.write(
            self.style.SUCCESS(f"Wrote {len(interactions)} interaction(s) to {output_file}.")
        )
