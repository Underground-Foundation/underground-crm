"""
Management command to import people from a legacy CRM CSV export into Person records.

Usage:
    python manage.py import_people_csv people.csv
    python manage.py import_people_csv people.csv --with-interactions --with-notes
    python manage.py import_people_csv people.csv --dry-run

Each row is matched on the legacy numeric ID (nationbuilder_id column). Existing
records are updated in place; new records are created. The command is idempotent
and safe to run multiple times.

Optional flags:
  --with-interactions   After importing each person, fetch their interactions from
                        the legacy CRM API and import them (requires LEGACY_API_TOKEN).
  --with-notes          After importing each person, fetch their private notes from
                        the legacy CRM admin endpoint and import them (requires
                        LEGACY_ADMIN_COOKIE_FILE and browser session cookies).
  --dry-run             Parse and validate the CSV without writing anything to the
                        database.

The legacy CRM connection is configured via environment variables (see .env.example):
  LEGACY_ADMIN_URL, LEGACY_API_URL, LEGACY_API_TOKEN, LEGACY_USER_AGENT,
  LEGACY_ADMIN_COOKIE_FILE
"""

import csv
import html
import http.cookiejar
import json
from typing import Optional, Tuple

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from phonenumbers import PhoneNumberType
from phonenumbers.phonenumber import PhoneNumber

from underground_crm.management.commands.legacy_api_client import require_env
from underground_crm.models import Interaction, Person, PersonNote, Tag
from underground_crm.models.address import Address
from underground_crm.contactability import (
    get_validated_domain_name,
    get_validated_email_address,
    parse_verified_phone_number,
    parse_phone_number_with_verified_type,
)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

_LEGACY_ADMIN_URL = require_env("LEGACY_ADMIN_URL").rstrip("/")
_LEGACY_API_TOKEN = require_env("LEGACY_API_TOKEN")
_LEGACY_USER_AGENT = require_env("LEGACY_USER_AGENT")
_LEGACY_ADMIN_COOKIE_FILE = require_env("LEGACY_ADMIN_COOKIE_FILE")

_LOCAL_TZ = ZoneInfo("Australia/Melbourne")

# ---------------------------------------------------------------------------
# Date / value helpers
# ---------------------------------------------------------------------------

_DT_FORMATS = [
    "%m/%d/%Y %I:%M %p",  # 05/26/2020  6:51 AM  (with extra space handled below)
    "%m/%d/%Y",
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d",
]


def _parse_datetime(value):
    """Parse a date/time string from the legacy CSV into an aware datetime, or None."""
    if not value:
        return None
    value = re.sub(r"\s+", " ", value.strip())
    for fmt in _DT_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_LOCAL_TZ)
            return dt
        except ValueError:
            continue
    return None


def _parse_date(value):
    """Parse a date-only string (MM/DD/YYYY or YYYY-MM-DD) into a date, or None."""
    if not value:
        return None
    value = value.strip()
    # Notice that the American format is attempted first. Our legacy system is assumed to be US-centric.
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _bool(value):
    return value.strip().lower() == "true"


def _int_or_none(value):
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return None


def _money(value):
    """Parse '$2,535.00' into a Decimal, or None."""
    if not value:
        return None
    cleaned = value.strip().lstrip("$").replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


# ---------------------------------------------------------------------------
# Address builder
# ---------------------------------------------------------------------------


def _build_address(row, prefix):
    """
    Create and return an Address from CSV columns like {prefix}_address1, {prefix}_city, etc.
    Returns None if all address fields are blank.
    """
    line1 = row.get(f"{prefix}_address1", "").strip()
    line2 = row.get(f"{prefix}_address2", "").strip()
    line3 = row.get(f"{prefix}_address3", "").strip()
    city = row.get(f"{prefix}_city", "").strip()
    state = row.get(f"{prefix}_state", "").strip()
    postcode = row.get(f"{prefix}_zip", "").strip()
    country_code = (row.get(f"{prefix}_country_code", "") or "AU").strip()
    _submitted = row.get(f"{prefix}_submitted_address", "").strip()

    if not any([line1, line2, city, postcode]):
        return None

    address = Address(
        line1=line1,
        line2=line2,
        line3=line3,
        city=city,
        state=state,
        postcode=postcode,
        country_code=country_code[:2] if country_code else "AU",
    )
    address._skip_geocoding = True
    return address


def get_mobile_and_phone_numbers(row) -> Tuple[Optional[PhoneNumber], Optional[PhoneNumber]]:
    mobile_number, mobile_type = parse_phone_number_with_verified_type(
        row.get("mobile_number", "").strip()
    )
    phone_number, phone_type = parse_phone_number_with_verified_type(
        row.get("phone_number", "").strip()
    )

    if mobile_number:
        if mobile_type == PhoneNumberType.MOBILE:
            return mobile_number, phone_number
        elif mobile_type not in (PhoneNumberType.FIXED_LINE, PhoneNumberType.TOLL_FREE) and (
            not phone_number or phone_type in (PhoneNumberType.FIXED_LINE,)
        ):
            # The input mobile_number is indeed more likely than the phone_number to really be a mobile.
            return mobile_number, phone_number
    elif phone_number:
        if phone_type in (
            PhoneNumberType.MOBILE,
            PhoneNumberType.FIXED_LINE_OR_MOBILE,
            PhoneNumberType.UNKNOWN,
        ):
            # The input phone_number could be a mobile number
            return phone_number, None
    return mobile_number, phone_number


# ---------------------------------------------------------------------------
# Person field mapper
# ---------------------------------------------------------------------------


def _person_fields(row):
    """Map a CSV row to a dict of Person field values (excluding FKs and M2M)."""
    mobile_number, phone_number = get_mobile_and_phone_numbers(row)
    return {
        "prefix": row.get("prefix", "").strip(),
        "first_name": row.get("first_name", "").strip(),
        "middle_name": row.get("middle_name", "").strip(),
        "last_name": row.get("last_name", "").strip(),
        "suffix": row.get("suffix", "").strip(),
        "legal_name": row.get("legal_name", "").strip(),
        "preferred_name": row.get("preferred_name", "").strip(),
        "mailing_name": row.get("mailing_name", "").strip(),
        "phone_number": phone_number,
        "work_phone_number": parse_verified_phone_number(row.get("work_phone_number", "").strip()),
        "mobile_number": mobile_number,
        "mobile_opt_in": _bool(row.get("mobile_opt_in", "")),
        "is_mobile_bad": _bool(row.get("is_mobile_bad", "")) or not mobile_number,
        "twitter_login": row.get("twitter_login", "").strip(),
        "facebook_username": row.get("facebook_username", "").strip(),
        "website": get_validated_domain_name(row.get("website", "").strip()),
        "submitted_address": row.get("primary_submitted_address", "").strip(),
        "gender": row.get("sex", "").strip(),
        "date_of_birth": _parse_date(row.get("born_at", "")),
        "email_opt_in": _bool(row.get("email_opt_in", "")),
        "unsubscribed_at": _parse_datetime(row.get("unsubscribed_at", "")),
        "is_supporter": _bool(row.get("is_supporter", "")),
        "support_level": _int_or_none(row.get("support_level", "")),
        "inferred_support_level": _int_or_none(row.get("inferred_support_level", "")),
        "priority_level": _int_or_none(row.get("priority_level", "")),
        "is_volunteer": _bool(row.get("is_volunteer", "")),
        "is_prospect": _bool(row.get("is_prospect", "")),
        "is_deceased": _bool(row.get("is_deceased", "")),
        "is_donor": _bool(row.get("is_donor", "")),
        "is_fundraiser": _bool(row.get("is_fundraiser", "")),
        "donations_count": _int_or_none(row.get("donations_count", "")) or 0,
        "donations_amount": _money(row.get("donations_amount", "")) or Decimal("0"),
        "first_donated_at": _parse_datetime(row.get("first_donated_at", "")),
        "last_donated_at": _parse_datetime(row.get("last_donated_at", "")),
        "do_not_call": _bool(row.get("do_not_call", "")),
        "do_not_contact": _bool(row.get("do_not_contact", "")),
        "federal_district": row.get("federal_district", "").strip(),
        "state_upper_district": row.get("state_upper_district", "").strip(),
        "state_lower_district": row.get("state_lower_district", "").strip(),
        "council_district": row.get("county_district", "").strip(),
        "ward": row.get("ward", "").strip(),
        "membership_number": row.get("legacy_membership_number", "").strip(),
    }


# ---------------------------------------------------------------------------
# Interaction fetching
# ---------------------------------------------------------------------------

_LEGACY_API_HEADERS = {
    "Authorization": f"Bearer {_LEGACY_API_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": _LEGACY_USER_AGENT,
}

# Map legacy contact method names to our Interaction.METHOD_* constants.
# Unmapped values fall back to "other".
_METHOD_MAP = {
    "door_knock": Interaction.METHOD_DOOR_KNOCK,
    "phone_call": Interaction.METHOD_PHONE_CALL,
    "face_to_face": Interaction.METHOD_FACE_TO_FACE,
    "email": Interaction.METHOD_EMAIL,
    "sms": Interaction.METHOD_SMS,
    "text_blast": Interaction.METHOD_TEXT_BLAST,
    "letter": Interaction.METHOD_LETTER,
    "social_media": Interaction.METHOD_SOCIAL_MEDIA,
}


def _api_get(path, params=None):
    url = f"{_LEGACY_ADMIN_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_LEGACY_API_HEADERS)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        return {"error": str(e)}, e.code


def _fetch_interactions_for_person(legacy_person_id):
    """Return normalized interaction dicts for a legacy person ID."""
    data, status = _api_get(f"/api/v1/people/{legacy_person_id}/contacts", {"limit": 100})
    if status != 200:
        return None, f"HTTP {status}"
    contacts = data.get("results", [])
    normalized = []
    for c in contacts:
        normalized.append(
            {
                "contact_id": c.get("contact_id"),
                "person_legacy_id": c.get("person_id") or c.get("recipient_id"),
                "author_legacy_id": c.get("author_id") or c.get("sender_id"),
                "method": c.get("method", ""),
                "note": c.get("note", "") or "",
                "status": c.get("status", "") or "",
                "created_at": c.get("created_at", ""),
            }
        )
    return normalized, None


def _import_interactions(person, legacy_person_id, dry_run, stderr):
    contacts, error = _fetch_interactions_for_person(legacy_person_id)
    if error:
        print(f"  [warn] interactions for {legacy_person_id}: {error}", file=stderr)
        return 0, 0
    imported = skipped = 0
    for c in contacts:
        method = _METHOD_MAP.get(c["method"], Interaction.METHOD_OTHER)
        created_at = _parse_datetime(c["created_at"])
        if not created_at:
            continue
        author = None
        if c["author_legacy_id"]:
            try:
                author = Person.objects.get(legacy_id=c["author_legacy_id"])
            except Person.DoesNotExist:
                pass
        if dry_run:
            imported += 1
            continue
        _, created = Interaction.objects.get_or_create(
            legacy_contact_id=c["contact_id"],
            defaults={
                "person": person,
                "author": author,
                "method": method,
                "note": c["note"],
                "status": c["status"],
                "created_at": created_at,
            },
        )
        if created:
            imported += 1
        else:
            skipped += 1
    return imported, skipped


# ---------------------------------------------------------------------------
# Private note fetching (mirrors logic in import_legacy_private_notes.py)
# ---------------------------------------------------------------------------


def _build_cookie_opener(cookie_file):
    jar = http.cookiejar.MozillaCookieJar(cookie_file)
    jar.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", _LEGACY_USER_AGENT),
        ("Accept", "application/json, text/javascript, */*; q=0.01"),
        ("X-Requested-With", "XMLHttpRequest"),
    ]
    return opener


def _fetch_private_notes(opener, legacy_person_id):
    notes = []
    page = 1
    while True:
        params = urllib.parse.urlencode(
            {
                "id": legacy_person_id,
                "page": page,
                "range": "All time",
                "type_id": "",
            }
        )
        url = f"{_LEGACY_ADMIN_URL}/admin/activities/signup.json?{params}"
        req = urllib.request.Request(
            url,
            headers={"Referer": f"{_LEGACY_ADMIN_URL}/admin/signups/{legacy_person_id}"},
        )
        with opener.open(req) as resp:
            data = json.loads(resp.read())
        activities = data.get("activities", [])
        if not activities:
            break
        for act in activities:
            if act.get("type") == "profile_private_note":
                related = act.get("relatedSignups", {})
                oneliner = act.get("oneliner", "")
                match = re.search(
                    r'<div class="activity_content_text">(.*?)</div>', oneliner, re.DOTALL
                )
                text = ""
                if match:
                    text = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
                    text = re.sub(r"\s+", " ", text).strip()
                notes.append(
                    {
                        "activity_id": act["id"],
                        "author_legacy_id": related.get("author", {}).get("id"),
                        "text": text,
                        "created_at": act.get("timestamp", ""),
                    }
                )
        if len(activities) < 20:
            break
        page += 1
        time.sleep(0.1)
    return notes


def _import_notes(person, legacy_person_id, opener, dry_run, stderr):
    try:
        raw_notes = _fetch_private_notes(opener, legacy_person_id)
    except urllib.error.HTTPError as e:
        print(f"  [warn] notes for {legacy_person_id}: HTTP {e.code}", file=stderr)
        return 0, 0
    imported = skipped = 0
    for note in raw_notes:
        author = None
        if note["author_legacy_id"]:
            try:
                author = Person.objects.get(legacy_id=note["author_legacy_id"])
            except Person.DoesNotExist:
                pass
        if dry_run:
            imported += 1
            continue
        _, created = PersonNote.objects.get_or_create(
            legacy_activity_id=note["activity_id"],
            defaults={"person": person, "created_by": author, "text": note["text"]},
        )
        if created:
            imported += 1
        else:
            skipped += 1
    return imported, skipped


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = "Import people from a legacy CRM CSV export, optionally including interactions and private notes."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            help="Path to the legacy CRM CSV export file.",
        )
        parser.add_argument(
            "--with-interactions",
            action="store_true",
            default=False,
            help="Also import interactions for each person from the legacy CRM API.",
        )
        parser.add_argument(
            "--with-notes",
            action="store_true",
            default=False,
            help="Also import private notes for each person from the legacy CRM.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and validate the CSV without writing to the database.",
        )

    def handle(
        self, *args, **options
    ):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        csv_file = options["csv_file"]
        with_interactions = options["with_interactions"]
        with_notes = options["with_notes"]
        dry_run = options["dry_run"]

        if with_interactions and not _LEGACY_API_TOKEN:
            raise CommandError("LEGACY_API_TOKEN is required for --with-interactions.")
        if with_notes and not _LEGACY_ADMIN_COOKIE_FILE:
            raise CommandError("LEGACY_ADMIN_COOKIE_FILE is required for --with-notes.")
        if (with_interactions or with_notes) and not _LEGACY_ADMIN_URL:
            raise CommandError(
                "LEGACY_ADMIN_URL is required for --with-interactions / --with-notes."
            )

        try:
            csv_fh = open(csv_file, newline="", encoding="utf-8-sig")
        except FileNotFoundError as exc:
            raise CommandError(f"File not found: {csv_file}") from exc

        note_opener = None
        if with_notes:
            try:
                note_opener = _build_cookie_opener(_LEGACY_ADMIN_COOKIE_FILE)
            except FileNotFoundError as exc:
                raise CommandError(f"Cookie file not found: {_LEGACY_ADMIN_COOKIE_FILE}") from exc

        rows = list(csv.DictReader(csv_fh))
        csv_fh.close()

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no database writes will occur."))

        # ---- Pass 1: create / update Person records and their addresses ----

        # Track legacy_id → person for the FK back-fill pass.
        legacy_id_to_person = {}
        # Track recruiter / point_person legacy IDs for back-fill.
        pending_recruiter = {}  # {person.pk: recruiter_legacy_id}
        pending_point_person = {}  # {person.pk: point_person_legacy_id}

        created_count = updated_count = skipped_count = 0

        for row in rows:
            legacy_id = _int_or_none(row.get("nationbuilder_id", ""))
            if not legacy_id:
                self.stderr.write(
                    f"  [skip] row has no nationbuilder_id: {row.get('full_name', '?')}"
                )
                skipped_count += 1
                continue

            email = get_validated_email_address(row.get("email", "").strip())
            if not email:
                # Generate a stable placeholder so the record can exist without a real email.
                email = f"no-email-{legacy_id}@import.invalid"

            fields = _person_fields(row)

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] {legacy_id}: {row.get('first_name', '')} {row.get('last_name', '')} <{email}>"
                )
                legacy_id_to_person[legacy_id] = None
                created_count += 1
                continue

            with transaction.atomic():
                person, created = Person.objects.get_or_create(
                    legacy_id=legacy_id,
                    defaults={"email": email},
                )

                # Update all mapped fields.
                for field, value in fields.items():
                    setattr(person, field, value)

                # Email may have changed (handle email uniqueness conflicts gracefully).
                if person.email != email and not email.endswith("@import.invalid"):
                    if not Person.objects.filter(email=email).exclude(pk=person.pk).exists():
                        person.email = email

                # Addresses: create new ones; preserve existing if they already exist.
                for attr, prefix in [
                    ("primary_address", "primary"),
                    ("mailing_address", "mailing"),
                    ("registered_address", "registered"),
                    ("billing_address", "billing"),
                    ("work_address", "work"),
                ]:
                    addr = _build_address(row, prefix)
                    if addr is not None and getattr(person, attr) is None:
                        addr.save()
                        setattr(person, attr, addr)

                person.save()

            legacy_id_to_person[legacy_id] = person

            # Record recruiter / point_person for back-fill.
            recruiter_id = _int_or_none(row.get("recruiter_id", ""))
            if recruiter_id:
                pending_recruiter[person.pk] = recruiter_id

            point_person_email = row.get("point_person_name_or_email", "").strip()
            if point_person_email and "@" in point_person_email:
                # Store email for lookup after all people are imported.
                pending_point_person[person.pk] = ("email", point_person_email)

            if created:
                created_count += 1
            else:
                updated_count += 1

        # ---- Pass 2: back-fill recruiter and point_person FKs ----

        if not dry_run:
            for person_pk, recruiter_legacy_id in pending_recruiter.items():
                recruiter = legacy_id_to_person.get(recruiter_legacy_id)
                if recruiter:
                    Person.objects.filter(pk=person_pk).update(recruiter=recruiter)

            for person_pk, (lookup_type, lookup_value) in pending_point_person.items():
                try:
                    pp = None
                    if lookup_type == "email":
                        pp = Person.objects.get(email=lookup_value)
                    Person.objects.filter(pk=person_pk).update(point_person=pp)
                except Person.DoesNotExist:
                    pass

        # ---- Pass 3: tags ----

        if not dry_run:
            for row in rows:
                legacy_id = _int_or_none(row.get("nationbuilder_id", ""))
                if not legacy_id:
                    continue
                person = legacy_id_to_person.get(legacy_id)
                if not person:
                    continue
                raw_tags = row.get("tag_list", "").strip()
                if not raw_tags:
                    continue
                tag_names = [t.strip() for t in raw_tags.split(",") if t.strip()]
                for name in tag_names:
                    tag, _ = Tag.objects.get_or_create(name=name)
                    self.stdout.write(f"Found tag {tag.id} for person")
                    person.tags.add(tag)

        # ---- Pass 4: interactions and notes (optional) ----

        interaction_imported = interaction_skipped = 0
        note_imported = note_skipped = 0

        if with_interactions or with_notes:
            for row in rows:
                legacy_id = _int_or_none(row.get("nationbuilder_id", ""))
                if not legacy_id:
                    continue
                person = legacy_id_to_person.get(legacy_id)
                if not person and not dry_run:
                    continue

                if with_interactions:
                    ii, is_ = _import_interactions(person, legacy_id, dry_run, self.stderr)
                    interaction_imported += ii
                    interaction_skipped += is_
                    time.sleep(0.05)

                if with_notes:
                    ni, ns = _import_notes(person, legacy_id, note_opener, dry_run, self.stderr)
                    note_imported += ni
                    note_skipped += ns
                    time.sleep(0.05)

        # ---- Summary ----

        action = "Would import" if dry_run else "Imported"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {created_count} new, {updated_count} updated, {skipped_count} skipped."
            )
        )
        if with_interactions:
            self.stdout.write(
                f"  Interactions: {interaction_imported} imported, {interaction_skipped} already existed."
            )
        if with_notes:
            self.stdout.write(f"  Notes: {note_imported} imported, {note_skipped} already existed.")
