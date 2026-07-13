"""
Management command to import interactions from the legacy CRM into Interaction.

Usage:
    python manage.py import_interactions --from-file interactions.jsonl
    python manage.py import_interactions --legacy-person-id <id>
    python manage.py import_interactions

With --from-file, reads interactions from a file previously written by
export_legacy_interactions. Without it, interactions are fetched directly from
the legacy CRM — for one person with --legacy-person-id, or for everyone if
that is omitted too.

Reads LEGACY_ADMIN_URL, LEGACY_API_TOKEN, and LEGACY_USER_AGENT from
the environment (see .env.example). Not required when --from-file is given.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from underground_crm.management.commands.importing import (
    fetch_interactions_via_api,
    make_legacy_api_headers,
    read_jsonl,
)
from underground_crm.management.commands.legacy_api_client import require_env
from underground_crm.models import Interaction, Person

LEGACY_ADMIN_URL = require_env("LEGACY_ADMIN_URL").rstrip("/")
LEGACY_API_TOKEN = require_env("LEGACY_API_TOKEN")
LEGACY_USER_AGENT = require_env("LEGACY_USER_AGENT")


class Command(BaseCommand):
    help = (
        "Import interactions from the legacy CRM into Interaction for a given person or all people."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--legacy-person-id",
            type=int,
            default=None,
            help="Legacy CRM person ID to import interactions for. Omit to import all. "
            "Ignored when --from-file is given.",
        )
        parser.add_argument(
            "--from-file",
            default=None,
            help="Path to a .jsonl file previously written by export_legacy_interactions. "
            "If omitted, interactions are fetched directly from the legacy CRM.",
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

        if from_file:
            self.stdout.write(f"Reading interactions from {from_file}...")
            try:
                raw = read_jsonl(from_file)
            except FileNotFoundError as exc:
                raise CommandError(f"File not found: {from_file}") from exc
        else:
            if legacy_person_id:
                self.stdout.write(f"Importing interactions for legacy ID {legacy_person_id}...")
            else:
                self.stdout.write("Importing interactions for all people...")
            self.stdout.write(f"  Legacy CRM: {LEGACY_ADMIN_URL}")
            api_headers = make_legacy_api_headers(LEGACY_API_TOKEN, LEGACY_USER_AGENT)
            raw = fetch_interactions_via_api(
                legacy_admin_url=LEGACY_ADMIN_URL,
                api_headers=api_headers,
                legacy_person_id=legacy_person_id,
                stdout=self.stdout,
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing will be written."))

        self.stdout.write(f"  Found {len(raw)} interaction(s).")

        if not raw:
            self.stdout.write("No interactions found.")
            return

        # --- Import into local DB ---
        imported = 0
        skipped = 0
        warn_no_person = 0
        warn_no_author = 0

        for contact in raw:
            person_lid = contact["person_legacy_id"]
            try:
                person = Person.objects.get(legacy_id=person_lid)
            except Person.DoesNotExist:
                self.stderr.write(
                    f"  Warning: person legacy_id={person_lid} not found locally; skipping contact {contact['contact_id']}."
                )
                warn_no_person += 1
                continue

            author = None
            if contact["author_legacy_id"]:
                try:
                    author = Person.objects.get(legacy_id=contact["author_legacy_id"])
                except Person.DoesNotExist:
                    self.stderr.write(
                        f"  Warning: author legacy_id={contact['author_legacy_id']} not found locally; interaction will have no author."
                    )
                    warn_no_author += 1

            created_at = parse_datetime(contact["created_at"]) if contact["created_at"] else None
            if not created_at:
                self.stderr.write(
                    f"  Warning: contact {contact['contact_id']} has no valid created_at ({contact['created_at']!r}); skipping."
                )
                continue

            # Normalise method to a known choice, falling back to "other"
            method = contact["method"]
            valid_methods = {m for m, _ in Interaction.METHOD_CHOICES}
            if method not in valid_methods:
                if method:
                    self.stderr.write(
                        f"  Warning: unknown method {method!r} for contact {contact['contact_id']}; storing as 'other'."
                    )
                method = Interaction.METHOD_OTHER

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] Would create Interaction: person={person_lid} "
                    f"method={method} created_at={contact['created_at']}"
                )
                imported += 1
                continue

            _, created = Interaction.objects.get_or_create(
                legacy_contact_id=contact["contact_id"],
                defaults={
                    "person": person,
                    "author": author,
                    "method": method,
                    "note": contact["note"],
                    "status": contact["status"],
                    "created_at": created_at,
                },
            )
            if created:
                self.stdout.write(
                    f"  Imported interaction {contact['contact_id']} "
                    f"({method}, {contact['created_at']})"
                )
                imported += 1
            else:
                self.stdout.write(f"  Skipped interaction {contact['contact_id']} (already exists)")
                skipped += 1

        summary = f"imported={imported}, skipped={skipped}"
        if warn_no_person:
            summary += f", skipped_no_local_person={warn_no_person}"
        if warn_no_author:
            summary += f", warned_no_author={warn_no_author}"

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run complete: {summary}."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done: {summary}."))
