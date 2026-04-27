"""
Management command to import interactions (contacts) from the legacy CRM into Interaction.

Usage:
    python manage.py import_legacy_interactions [<legacy_person_id>]

Without a person ID, imports interactions for all people in the legacy CRM.
With a person ID, imports only that person's interactions.

Reads LEGACY_WEBSITE_URL, LEGACY_API_TOKEN, and LEGACY_USER_AGENT from
the environment (see .env.example).
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from underground_crm.models import Interaction, Person

LEGACY_WEBSITE_URL = os.environ.get("LEGACY_WEBSITE_URL", "").rstrip("/")
LEGACY_API_TOKEN = os.environ.get("LEGACY_API_TOKEN", "")
LEGACY_USER_AGENT = os.environ.get("LEGACY_USER_AGENT", "")


def _make_headers():
    return {
        "Authorization": f"Bearer {LEGACY_API_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": LEGACY_USER_AGENT,
    }


def _get(path, params=None):
    url = f"{LEGACY_WEBSITE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_make_headers())
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read()), resp.status


def _normalize(contact):
    return {
        "contact_id": contact.get("contact_id"),
        "person_legacy_id": contact.get("person_id") or contact.get("recipient_id"),
        "author_legacy_id": contact.get("author_id") or contact.get("sender_id"),
        "method": contact.get("method", "") or "",
        "note": contact.get("note", "") or "",
        "status": contact.get("status", "") or "",
        "created_at": contact.get("created_at", ""),
    }


def fetch_interactions_for_person(person_id):
    data, status = _get(f"/api/v1/people/{person_id}/contacts", {"limit": 100})
    if status != 200:
        return None, status
    return [_normalize(c) for c in data.get("results", [])], status


def fetch_all_interactions(stdout):
    interactions = []
    next_cursor = None
    page = 0

    while True:
        params = {"limit": 100}
        if next_cursor:
            params["next"] = next_cursor

        data, status = _get("/api/v1/contacts", params)
        if status != 200:
            raise CommandError(f"Legacy CRM request failed on page {page + 1}: HTTP {status}")

        results = data.get("results", [])
        next_cursor = data.get("next")
        page += 1

        for contact in results:
            interactions.append(_normalize(contact))

        if page % 5 == 0:
            stdout.write(f"  Fetched {len(interactions)} interactions so far (page {page})...")

        if not results or not next_cursor:
            break

        time.sleep(0.1)

    return interactions


class Command(BaseCommand):
    help = (
        "Import interactions from the legacy CRM into Interaction for a given person or all people."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "legacy_person_id",
            type=int,
            nargs="?",
            default=None,
            help="Legacy CRM person ID to import interactions for. Omit to import all.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be imported without writing to the database.",
        )

    def handle(self, *args, **options):
        legacy_person_id = options["legacy_person_id"]
        dry_run = options["dry_run"]

        if legacy_person_id:
            self.stdout.write(f"Importing interactions for legacy ID {legacy_person_id}...")
        else:
            self.stdout.write("Importing interactions for all people...")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing will be written."))

        for var, val in [
            ("LEGACY_WEBSITE_URL", LEGACY_WEBSITE_URL),
            ("LEGACY_API_TOKEN", LEGACY_API_TOKEN),
            ("LEGACY_USER_AGENT", LEGACY_USER_AGENT),
        ]:
            if not val:
                raise CommandError(f"{var} is not set. Add it to .env.")

        self.stdout.write(f"  Legacy CRM: {LEGACY_WEBSITE_URL}")

        # --- Fetch raw interactions from legacy CRM ---
        try:
            if legacy_person_id:
                raw, status = fetch_interactions_for_person(legacy_person_id)
                if raw is None:
                    raise CommandError(
                        f"Legacy CRM returned HTTP {status} for person {legacy_person_id}."
                    )
                self.stdout.write(f"  Fetched {len(raw)} interaction(s) from legacy CRM.")
            else:
                self.stdout.write("  Fetching all interactions (this may take a while)...")
                raw = fetch_all_interactions(self.stdout)
                self.stdout.write(f"  Fetched {len(raw)} interaction(s) total.")
        except urllib.error.HTTPError as e:
            raise CommandError(f"Legacy CRM request failed: {e.code} {e.reason} — {e.url}") from e
        except urllib.error.URLError as e:
            raise CommandError(f"Network error reaching legacy CRM: {e.reason}") from e

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
