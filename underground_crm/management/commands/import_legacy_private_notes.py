"""
Management command to import private notes from the legacy CRM into PersonNote.

Usage:
    python manage.py import_legacy_private_notes <legacy_person_id> --cookie-file <path>

Requires an admin session cookie file (Netscape format) for the legacy CRM.
Export your cookies from the browser using a cookie export extension,
then point --cookie-file at the file.

Reads LEGACY_WEBSITE_URL, LEGACY_USER_AGENT, and LEGACY_ADMIN_COOKIE_FILE from
the environment (see .env.example). The cookie file path can be overridden
with --cookie-file.

# TODO: Accept a pre-exported CSV file as an alternative input source,
#       for bulk import without requiring live legacy CRM access.
"""

import html
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from underground_crm.models import PersonNote

LEGACY_WEBSITE_URL = os.environ.get("LEGACY_WEBSITE_URL", "").rstrip("/")
LEGACY_USER_AGENT = os.environ.get("LEGACY_USER_AGENT", "")
LEGACY_ADMIN_COOKIE_FILE = os.environ.get("LEGACY_ADMIN_COOKIE_FILE", "")


def _build_opener(cookie_file):
    jar = http.cookiejar.MozillaCookieJar(cookie_file)
    jar.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", LEGACY_USER_AGENT),
        ("Accept", "application/json, text/javascript, */*; q=0.01"),
        ("X-Requested-With", "XMLHttpRequest"),
    ]
    return opener


def _fetch_activities(opener, legacy_person_id, page=1):
    params = urllib.parse.urlencode(
        {
            "id": legacy_person_id,
            "page": page,
            "range": "All time",
            "type_id": "",
        }
    )
    url = f"{LEGACY_WEBSITE_URL}/admin/activities/signup.json?{params}"
    req = urllib.request.Request(
        url,
        headers={"Referer": f"{LEGACY_WEBSITE_URL}/admin/signups/{legacy_person_id}"},
    )
    with opener.open(req) as resp:
        return json.loads(resp.read())


def _strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _extract_note_text(oneliner):
    match = re.search(
        r'<div class="activity_content_text">(.*?)</div>',
        oneliner,
        re.DOTALL,
    )
    if match:
        return _strip_html(match.group(1))
    return ""


def fetch_private_notes(opener, legacy_person_id, stdout=None):
    """Return all private note activities for a person in the legacy CRM."""
    notes = []
    page = 1
    while True:
        if stdout:
            stdout.write(f"  Fetching page {page}...")
        data = _fetch_activities(opener, legacy_person_id, page)
        activities = data.get("activities", [])
        if stdout:
            stdout.write(f"  Page {page}: {len(activities)} activity/activities returned.")
        if not activities:
            break
        for act in activities:
            if act.get("type") == "profile_private_note":
                related = act.get("relatedSignups", {})
                notes.append(
                    {
                        "activity_id": act["id"],
                        "person_legacy_id": related.get("signup", {}).get("id") or legacy_person_id,
                        "author_legacy_id": related.get("author", {}).get("id"),
                        "text": _extract_note_text(act.get("oneliner", "")),
                        "created_at": act.get("timestamp", ""),
                    }
                )
        if len(activities) < 20:
            break
        page += 1
        time.sleep(0.1)
    return notes


class Command(BaseCommand):
    help = "Import private notes from the legacy CRM into PersonNote for a given legacy person ID."

    def add_arguments(self, parser):
        parser.add_argument(
            "legacy_person_id",
            type=int,
            help="Legacy CRM person ID to import notes for.",
        )
        parser.add_argument(
            "--cookie-file",
            default=None,
            help="Path to a Netscape-format cookie file for the legacy CRM. "
            "Defaults to LEGACY_ADMIN_COOKIE_FILE env var.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be imported without writing to the database.",
        )

    def handle(self, *args, **options):
        legacy_person_id = options["legacy_person_id"]
        cookie_file = options["cookie_file"] or LEGACY_ADMIN_COOKIE_FILE
        dry_run = options["dry_run"]

        self.stdout.write(f"Importing private notes for legacy ID {legacy_person_id}...")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing will be written."))

        for var, val in [
            ("LEGACY_WEBSITE_URL", LEGACY_WEBSITE_URL),
            ("LEGACY_USER_AGENT", LEGACY_USER_AGENT),
            ("LEGACY_ADMIN_COOKIE_FILE", cookie_file),
        ]:
            if not val:
                raise CommandError(f"{var} is not set. Add it to .env or pass --cookie-file.")

        self.stdout.write(f"  Legacy CRM: {LEGACY_WEBSITE_URL}")
        self.stdout.write(f"  Cookie file: {cookie_file}")
        User = get_user_model()

        try:
            user = User.objects.get(legacy_id=legacy_person_id)
        except User.DoesNotExist as exc:
            raise CommandError(
                f"No user with legacy_id={legacy_person_id} found. "
                "Import the person record first."
            ) from exc

        self.stdout.write(f"  Local user: {user} (pk={user.pk})")

        try:
            opener = _build_opener(cookie_file)
        except FileNotFoundError as exc:
            raise CommandError(f"Cookie file not found: {cookie_file}") from exc

        self.stdout.write("  Cookie file loaded. Fetching activities...")

        try:
            raw_notes = fetch_private_notes(opener, legacy_person_id, self.stdout)
        except urllib.error.HTTPError as e:
            raise CommandError(f"Legacy CRM request failed: {e.code} {e.reason} — {e.url}") from e
        except urllib.error.URLError as e:
            raise CommandError(f"Network error reaching legacy CRM: {e.reason}") from e

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
                    f"Imported {imported} note(s) for {user} " f"({skipped} already existed)."
                )
            )
