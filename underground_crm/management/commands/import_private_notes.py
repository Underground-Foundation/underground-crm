"""
Management command to import private notes into PersonNote for a given legacy person ID.

Usage:
    python manage.py import_private_notes --legacy-person-id <id> --from-file notes.jsonl
    python manage.py import_private_notes --legacy-person-id <id>
    python manage.py import_private_notes --legacy-person-id <id> --cookie-file <path>

With --from-file, notes are read from a JSON Lines file previously written by
export_legacy_private_notes. Without it, notes are fetched directly from the
legacy CRM using the same cookie-based authentication as that command —
requires an admin session cookie file (Netscape format); export your cookies
from the browser using a cookie export extension, then point --cookie-file
at the file.

Reads LEGACY_ADMIN_URL, LEGACY_USER_AGENT, and LEGACY_ADMIN_COOKIE_FILE from
the environment (see .env.example). The cookie file path can be overridden
with --cookie-file. Neither is required when --from-file is given.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from underground_crm.management.commands.importing import (
    fetch_private_notes_via_cookie,
    read_jsonl,
    require_legacy_admin_env,
)
from underground_crm.management.commands.legacy_api_client import require_env
from underground_crm.models import PersonNote

LEGACY_ADMIN_URL = require_env("LEGACY_ADMIN_URL").rstrip("/")
LEGACY_ADMIN_COOKIE_FILE = require_env("LEGACY_ADMIN_COOKIE_FILE")


class Command(BaseCommand):
    help = "Import private notes from the legacy CRM into PersonNote for a given legacy person ID."

    def add_arguments(self, parser):
        parser.add_argument(
            "--legacy-person-id",
            type=int,
            required=True,
            help="Legacy CRM person ID to import notes for.",
        )
        parser.add_argument(
            "--from-file",
            default=None,
            help="Path to a .jsonl file previously written by export_legacy_private_notes. "
            "If omitted, notes are fetched directly from the legacy CRM.",
        )
        parser.add_argument(
            "--cookie-file",
            default=None,
            help="Path to a Netscape-format cookie file for the legacy CRM. "
            "Defaults to LEGACY_ADMIN_COOKIE_FILE env var. Ignored when --from-file is given.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be imported without writing to the database.",
        )

    def handle(self, *args, **options):
        legacy_person_id = options["legacy_person_id"]
        from_file = options["from_file"]
        dry_run = options["dry_run"]

        self.stdout.write(f"Importing private notes for legacy ID {legacy_person_id}...")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing will be written."))

        User = get_user_model()

        try:
            user = User.objects.get(legacy_id=legacy_person_id)
        except User.DoesNotExist as exc:
            raise CommandError(
                f"No user with legacy_id={legacy_person_id} found. "
                "Import the person record first."
            ) from exc

        self.stdout.write(f"  Local user: {user} (pk={user.pk})")

        if from_file:
            self.stdout.write(f"  Reading notes from {from_file}...")
            try:
                raw_notes = read_jsonl(from_file)
            except FileNotFoundError as exc:
                raise CommandError(f"File not found: {from_file}") from exc
        else:
            cookie_file = options["cookie_file"] or LEGACY_ADMIN_COOKIE_FILE
            require_legacy_admin_env(cookie_file, LEGACY_ADMIN_URL)
            self.stdout.write(f"  Legacy CRM: {LEGACY_ADMIN_URL}")
            self.stdout.write(f"  Cookie file: {cookie_file}")
            raw_notes = fetch_private_notes_via_cookie(
                cookie_file=cookie_file,
                legacy_admin_url=LEGACY_ADMIN_URL,
                legacy_person_id=legacy_person_id,
                stdout=self.stdout,
            )

        self.stdout.write(f"  Found {len(raw_notes)} private note(s).")

        if not raw_notes:
            return

        imported = 0
        skipped = 0
        for note in raw_notes:
            # Resolve author — may not exist locally yet, that's fine
            author = None
            if note["author_legacy_id"]:
                try:
                    author = User.objects.get(legacy_id=note["author_legacy_id"])
                except User.DoesNotExist:
                    self.stderr.write(
                        f"  Warning: author legacy_id={note['author_legacy_id']}"
                        " not found locally; note will have no author."
                    )

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] Would create PersonNote: "
                    f"author={note['author_legacy_id']} "
                    f"created_at={note['created_at']}\n"
                    f"  text: {note['text'][:120]}"
                )
                imported += 1
                continue

            _, created = PersonNote.objects.get_or_create(
                legacy_activity_id=note["activity_id"],
                defaults={
                    "person": user,
                    "created_by": author,
                    "text": note["text"],
                },
            )
            if created:
                self.stdout.write(f"  Imported note {note['activity_id']} ({note['created_at']})")
                imported += 1
            else:
                self.stdout.write(f"  Skipped note {note['activity_id']} (already exists)")
                skipped += 1

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry run: {imported} note(s) would be imported for {user}.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Imported {imported} note(s) for {user} ({skipped} already existed)."
                )
            )
