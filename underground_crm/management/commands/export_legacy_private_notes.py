"""
Management command to fetch private notes for a legacy CRM person and save them
to a JSON Lines (.jsonl) file, for later import via import_private_notes.

Usage:
    python manage.py export_legacy_private_notes --legacy-person-id <id> --output-file <path>
    python manage.py export_legacy_private_notes --legacy-person-id <id> --output-file <path> --cookie-file <path>

Requires an admin session cookie file (Netscape format) for the legacy CRM.
Export your cookies from the browser using a cookie export extension,
then point --cookie-file at the file.

Reads LEGACY_ADMIN_URL, LEGACY_USER_AGENT, and LEGACY_ADMIN_COOKIE_FILE from
the environment (see .env.example). The cookie file path can be overridden
with --cookie-file.
"""

from django.core.management.base import BaseCommand

from underground_crm.management.commands.importing import (
    fetch_private_notes_via_cookie,
    require_legacy_admin_env,
    write_jsonl,
)
from underground_crm.management.commands.legacy_api_client import require_env

LEGACY_ADMIN_URL = require_env("LEGACY_ADMIN_URL").rstrip("/")
LEGACY_ADMIN_COOKIE_FILE = require_env("LEGACY_ADMIN_COOKIE_FILE")


class Command(BaseCommand):
    help = "Fetch private notes for a legacy CRM person and save them to a JSON Lines file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--legacy-person-id",
            type=int,
            required=True,
            help="Legacy CRM person ID to fetch notes for.",
        )
        parser.add_argument(
            "--output-file",
            required=True,
            help="Path to write the fetched notes to, as newline-delimited JSON (.jsonl).",
        )
        parser.add_argument(
            "--cookie-file",
            default=None,
            help="Path to a Netscape-format cookie file for the legacy CRM. "
            "Defaults to LEGACY_ADMIN_COOKIE_FILE env var.",
        )

    def handle(self, *args, **options):
        legacy_person_id = options["legacy_person_id"]
        output_file = options["output_file"]
        cookie_file = options["cookie_file"] or LEGACY_ADMIN_COOKIE_FILE

        self.stdout.write(f"Fetching private notes for legacy ID {legacy_person_id}...")

        require_legacy_admin_env(cookie_file, LEGACY_ADMIN_URL)

        self.stdout.write(f"  Legacy CRM: {LEGACY_ADMIN_URL}")
        self.stdout.write(f"  Cookie file: {cookie_file}")

        notes = fetch_private_notes_via_cookie(
            cookie_file=cookie_file,
            legacy_admin_url=LEGACY_ADMIN_URL,
            legacy_person_id=legacy_person_id,
            stdout=self.stdout,
        )

        self.stdout.write(f"  Found {len(notes)} private note(s).")

        write_jsonl(notes, output_file)

        self.stdout.write(self.style.SUCCESS(f"Wrote {len(notes)} note(s) to {output_file}."))
