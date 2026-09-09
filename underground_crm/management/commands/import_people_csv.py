"""
Management command to import people from a legacy CRM CSV export into Person records.

Usage:
    python manage.py import_people_csv people.csv
    python manage.py import_people_csv people.csv --with-interactions --with-notes
    python manage.py import_people_csv people.csv --dry-run

Each row is matched on the legacy numeric ID (nationbuilder_id column). Existing
records are updated in place; new records are created. The command is idempotent
and safe to run multiple times.

Memberships are seeded from the membership_names column, a comma-separated list of
MembershipType names, positionally aligned with the memberships_started_at,
memberships_expires_on, and memberships_suspended_at columns (one entry per
membership held by that person). Both MembershipType and Membership are looked up
with get_or_create, so re-running the import never creates duplicates.

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
import json
import logging
from typing import Optional, Tuple

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from phonenumbers import PhoneNumberType
from phonenumbers.phonenumber import PhoneNumber

from underground_crm.management.commands.importing import build_cookie_opener, fetch_private_notes
from underground_crm.management.commands.legacy_api_client import require_env
from underground_crm.models import Interaction, Membership, MembershipType, Person, PersonNote, Tag
from underground_crm.models.address import Address
from underground_crm.contactability import (
    MOBILE_CAPABLE_PHONE_TYPES,
    InvalidPhoneNumberError,
    get_validated_domain_name,
    get_validated_email_address,
    parse_verified_phone_number,
    parse_phone_number_with_verified_type,
)

logger = logging.getLogger(__name__)

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

    The structured columns are the legacy system's own parse of whatever the
    person typed, which it keeps in {prefix}_submitted_address. When that
    parse failed and every structured column is blank, the raw string is kept
    as line1 on an unverified record — the same recovery path the
    registration form uses — so the address survives the import and the
    geocoding backlog (geocode_addresses --correct-address-fields) can repair
    it later.
    """
    line1 = row.get(f"{prefix}_address1", "").strip() or None
    line2 = row.get(f"{prefix}_address2", "").strip() or None
    line3 = row.get(f"{prefix}_address3", "").strip() or None
    city = row.get(f"{prefix}_city", "").strip() or None
    state = row.get(f"{prefix}_state", "").strip() or None
    postcode = row.get(f"{prefix}_zip", "").strip() or None
    country_code = (row.get(f"{prefix}_country_code", "") or "AU").strip() or None
    submitted = row.get(f"{prefix}_submitted_address", "").strip() or None

    if not any([line1, line2, city, postcode]):
        if not submitted:
            return None
        address = Address(line1=submitted)
        address._skip_geocoding = True
        return address

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


# Legacy CSV address prefixes and the Person field each maps to. The legacy
# system exports its home address under the bare "address" prefix. Its "work"
# addresses are deliberately not imported: Person carries no work-address
# role, because collecting more of a person's data than the movement needs
# erodes their trust.
ADDRESS_PREFIX_TO_PERSON_FIELD: list[tuple[str, str]] = [
    ("address", "home_address"),
    ("mailing", "mailing_address"),
    ("registered", "registered_address"),
    ("billing", "billing_address"),
]

# Order in which the legacy "primary" address claims a Person field when it
# matches none of the addresses imported through the mapping above.
PRIMARY_ADDRESS_FALLBACK_FIELDS: tuple[str, ...] = (
    "home_address",
    "registered_address",
    "mailing_address",
    "billing_address",
)


def assign_addresses(person: Person, row: dict) -> None:
    """
    Set the person's address fields from a legacy CSV row, without saving the
    person. Addresses the person already holds are preserved.

    The legacy "primary" address is not a role of its own — it duplicates
    whichever of the other addresses the legacy system considered primary — so
    it is only placed when it matches none of the person's addresses: it then
    claims the first open field of home, registered, mailing, billing. When
    every one of those is taken, it overrides the home address, with a
    warning.
    """
    for prefix, field_name in ADDRESS_PREFIX_TO_PERSON_FIELD:
        address = _build_address(row, prefix)
        if address is not None and getattr(person, field_name) is None:
            address.save()
            setattr(person, field_name, address)

    primary = _build_address(row, "primary")
    if primary is None:
        return
    held_addresses = (
        getattr(person, field_name) for _, field_name in ADDRESS_PREFIX_TO_PERSON_FIELD
    )
    if any(held is not None and held.is_equivalent(primary) for held in held_addresses):
        return

    for field_name in PRIMARY_ADDRESS_FALLBACK_FIELDS:
        if getattr(person, field_name) is None:
            primary.save()
            setattr(person, field_name, primary)
            return

    logger.warning(
        "Legacy primary address %r of person %s (legacy ID %s) matches none of their "
        "other addresses, and every address field is taken; overriding their home address.",
        str(primary),
        person.pk,
        person.legacy_id,
    )
    primary.save()
    person.home_address = primary


# ---------------------------------------------------------------------------
# Membership builder
# ---------------------------------------------------------------------------


def _parse_membership_date(value: str) -> Optional[date]:
    """Parse a memberships_expires_on cell, which may be date-only or a full timestamp."""
    value = value.strip()
    if not value:
        return None
    parsed_date = _parse_date(value)
    if parsed_date:
        return parsed_date
    parsed_datetime = _parse_datetime(value)
    return parsed_datetime.date() if parsed_datetime else None


def parse_memberships(row: dict) -> list[tuple[str, datetime, Optional[date], Optional[datetime]]]:
    """
    Return (name, started_at, expires_on, suspended_at) tuples from the legacy CSV's
    parallel comma-separated membership_names / memberships_started_at /
    memberships_expires_on / memberships_suspended_at columns — a person can hold
    more than one membership (e.g. a state branch and the federal party), and the
    legacy export lines them up positionally across the four columns rather than
    repeating a row per membership.

    An entry is dropped if it has no name or no parseable started_at: the name is
    what seeds the MembershipType, and Membership.started_at is a required field.
    """
    raw_names = row.get("membership_names", "")
    if not raw_names.strip():
        return []

    names = raw_names.split(",")
    started_ats = row.get("memberships_started_at", "").split(",")
    expires_ons = row.get("memberships_expires_on", "").split(",")
    suspended_ats = row.get("memberships_suspended_at", "").split(",")

    if not len(names) == len(started_ats) == len(expires_ons) == len(suspended_ats):
        logger.warning(
            "Legacy membership columns for nationbuilder_id %r have mismatched list "
            "lengths (%d names, %d started_at, %d expires_on, %d suspended_at); skipping.",
            row.get("nationbuilder_id", "?"),
            len(names),
            len(started_ats),
            len(expires_ons),
            len(suspended_ats),
        )
        return []

    memberships = []
    for name, started_at_raw, expires_on_raw, suspended_at_raw in zip(
        names, started_ats, expires_ons, suspended_ats
    ):
        name = name.strip()
        started_at = _parse_datetime(started_at_raw)
        if not name or not started_at:
            continue
        memberships.append(
            (
                name,
                started_at,
                _parse_membership_date(expires_on_raw),
                _parse_datetime(suspended_at_raw),
            )
        )
    return memberships


def _get_email_with_is_bad(row: dict) -> Tuple[Optional[str], Optional[bool]]:
    """Return the first valid email address with an is_bad=False flag.

    Tries email1 through email4 in order. The legacy system may export the primary
    email as the bare "email" column rather than "email1"; both are checked for
    slot 1 so either CSV format is handled.
    """
    fallback = (None, None)
    for n in range(1, 5):
        raw = row.get(f"email{n}", "") or (row.get("email", "") if n == 1 else "")
        email = get_validated_email_address(raw.strip())
        if email:
            is_bad_raw = row.get(f"email{n}_is_bad", "").strip()
            is_bad = _bool(is_bad_raw)
            if is_bad:
                fallback = (email, is_bad)
                continue
            return email, is_bad
    return fallback


def _row_label(row) -> str:
    """A short "Row <legacy id> (<name>)" tag for error messages about a CSV row."""
    legacy_id = row.get("nationbuilder_id", "").strip() or "?"
    name = (
        row.get("full_name", "").strip()
        or f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
        or "?"
    )
    return f"Row {legacy_id} ({name})"


def _phone_error(row, column: str, exc: InvalidPhoneNumberError) -> CommandError:
    """Turn an InvalidPhoneNumberError into a CommandError that names the row and
    column, so one bad number aborts the import with a legible message pointing
    at the record to fix rather than a bare traceback."""
    return CommandError(f"{_row_label(row)}: {column} {exc}. Fix it in the CSV and re-run.")


def get_mobile_and_phone_numbers(row) -> Tuple[Optional[PhoneNumber], Optional[PhoneNumber]]:
    try:
        mobile_number, mobile_type = parse_phone_number_with_verified_type(
            row.get("mobile_number", "").strip() or None
        )
    except InvalidPhoneNumberError as exc:
        raise _phone_error(row, "mobile_number", exc) from exc
    try:
        phone_number, phone_type = parse_phone_number_with_verified_type(
            row.get("phone_number", "").strip() or None
        )
    except InvalidPhoneNumberError as exc:
        raise _phone_error(row, "phone_number", exc) from exc

    if mobile_number:
        if mobile_type == PhoneNumberType.MOBILE:
            return mobile_number, phone_number
        elif mobile_type not in (PhoneNumberType.FIXED_LINE, PhoneNumberType.TOLL_FREE) and (
            not phone_number or phone_type in (PhoneNumberType.FIXED_LINE,)
        ):
            # The input mobile_number is indeed more likely than the phone_number to really be a mobile.
            return mobile_number, phone_number
    elif phone_number:
        if phone_type in MOBILE_CAPABLE_PHONE_TYPES:
            # The input phone_number could be a mobile number
            return phone_number, None
    return mobile_number, phone_number


# ---------------------------------------------------------------------------
# Person field mapper
# ---------------------------------------------------------------------------


def _resolve_first_and_preferred_name(row: dict) -> Tuple[Optional[str], Optional[str]]:
    """Return (first_name, preferred_name) for Person, reconciling the legacy columns.

    Person.first_name is the name used for the electoral roll, i.e. the
    legally correct one. The legacy CSV instead exports the person's
    colloquial name as first_name and their formal name as legal_name, with
    no separate preferred_name of its own in that case. So when a row has
    both a first_name and a legal_name but no preferred_name, the legacy
    first_name becomes the preferred_name and the legal_name is promoted
    into first_name.
    """
    first_name = row.get("first_name", "").strip() or None
    legal_name = row.get("legal_name", "").strip() or None
    preferred_name = row.get("preferred_name", "").strip() or None

    if first_name and legal_name and not preferred_name:
        return legal_name, first_name
    return first_name, preferred_name


def _person_fields(row, is_email_bad: bool):
    """Map a CSV row to a dict of Person field values (excluding FKs and M2M)."""
    mobile_number, phone_number = get_mobile_and_phone_numbers(row)
    try:
        work_phone_number = parse_verified_phone_number(row.get("work_phone_number", "").strip())
    except InvalidPhoneNumberError as exc:
        raise _phone_error(row, "work_phone_number", exc) from exc
    first_name, preferred_name = _resolve_first_and_preferred_name(row)
    return {
        "prefix": row.get("prefix", "").strip() or None,
        "first_name": first_name,
        "middle_name": row.get("middle_name", "").strip() or None,
        "last_name": row.get("last_name", "").strip() or None,
        "suffix": row.get("suffix", "").strip() or None,
        "legal_name": row.get("legal_name", "").strip() or None,
        "preferred_name": preferred_name,
        "phone_number": phone_number,
        "work_phone_number": work_phone_number,
        "mobile_number": mobile_number,
        "mobile_opt_in": _bool(row.get("mobile_opt_in", "")),
        "is_mobile_bad": _bool(row.get("is_mobile_bad", "")) or not mobile_number,
        "twitter_login": row.get("twitter_login", "").strip() or None,
        "facebook_username": row.get("facebook_username", "").strip() or None,
        "website": get_validated_domain_name(row.get("website", "").strip()),
        "submitted_address": row.get("primary_submitted_address", "").strip() or None,
        "gender": row.get("sex", "").strip() or None,
        "date_of_birth": _parse_date(row.get("born_at", "")),
        "email_is_bad": is_email_bad,
        "email_opt_in": _bool(row.get("email_opt_in", "")),
        "unsubscribed_at": _parse_datetime(row.get("unsubscribed_at", "")),
        "is_supporter": _bool(row.get("is_supporter", "")),
        "support_level": _int_or_none(row.get("support_level", "")),
        "inferred_support_level": _int_or_none(row.get("inferred_support_level", "")),
        "priority_level": _int_or_none(row.get("priority_level", "")),
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
        "federal_district": row.get("federal_district", "").strip() or None,
        "state_upper_district": row.get("state_upper_district", "").strip() or None,
        "state_lower_district": row.get("state_lower_district", "").strip() or None,
        "council_district": row.get("county_district", "").strip() or None,
        "ward": row.get("ward", "").strip() or None,
        "membership_number": row.get("legacy_membership_number", "").strip() or None,
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


def _import_notes(person, legacy_person_id, opener, dry_run, stderr):
    try:
        raw_notes = fetch_private_notes(opener, _LEGACY_ADMIN_URL, legacy_person_id)
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
                note_opener = build_cookie_opener(_LEGACY_ADMIN_COOKIE_FILE)
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

            email, is_email_bad = _get_email_with_is_bad(row)
            if not email:
                # Generate a stable placeholder so the record can exist without a real email.
                email = f"no-email-{legacy_id}@import.invalid"

            fields = _person_fields(row, is_email_bad=is_email_bad)

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] {legacy_id}: {row.get('first_name', None)} {row.get('last_name', None)} <{email}>"
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
                assign_addresses(person, row)

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

        # ---- Pass 3: tags and memberships ----

        if not dry_run:
            volunteer_tag = None
            for row in rows:
                legacy_id = _int_or_none(row.get("nationbuilder_id", ""))
                if not legacy_id:
                    continue
                person = legacy_id_to_person.get(legacy_id)
                if not person:
                    continue

                raw_tags = row.get("tag_list", "").strip()
                tag_names = (
                    [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else []
                )
                for name in tag_names:
                    tag, created = Tag.objects.get_or_create(name=name)
                    self.stdout.write(
                        f"{'Created' if created else 'Found'} tag {tag.name} for person"
                    )
                    person.tags.add(tag)

                # There's no Person.is_volunteer field — a "Volunteer" Tag (seeded by
                # migration 0008) is used instead, so this reuses the same tagging
                # mechanism as tag_list rather than a dedicated boolean column.
                if _bool(row.get("is_volunteer", "")):
                    if volunteer_tag is None:
                        volunteer_tag, _created = Tag.objects.get_or_create(name="Volunteer")
                    person.tags.add(volunteer_tag)

                for name, started_at, expires_on, suspended_at in parse_memberships(row):
                    membership_type, type_created = MembershipType.objects.get_or_create(name=name)
                    self.stdout.write(
                        f"{'Created' if type_created else 'Found'} membership type {membership_type.name} for person"
                    )
                    Membership.objects.get_or_create(
                        person=person,
                        type=membership_type,
                        started_at=started_at,
                        defaults={
                            "expires_on": expires_on,
                            "suspended_at": suspended_at,
                        },
                    )

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
