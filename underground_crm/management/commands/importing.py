"""
Shared utilities for legacy CRM import management commands.
"""

import html
import http.cookiejar
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from django.core.management.base import CommandError

from underground_crm.management.commands.legacy_api_client import require_env

LEGACY_USER_AGENT: str = require_env("LEGACY_USER_AGENT")


def build_cookie_opener(cookie_file: str) -> urllib.request.OpenerDirector:
    """Build a urllib opener authenticated via a Mozilla-format cookie file."""
    jar = http.cookiejar.MozillaCookieJar(cookie_file)
    jar.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", LEGACY_USER_AGENT),
        ("Accept", "application/json, text/javascript, */*; q=0.01"),
        ("X-Requested-With", "XMLHttpRequest"),
    ]
    return opener


def strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def extract_note_text(oneliner: str) -> str:
    """Extract plain-text note content from a legacy CRM activity oneliner HTML fragment."""
    match = re.search(
        r'<div class="activity_content_text">(.*?)</div>',
        oneliner,
        re.DOTALL,
    )
    return strip_html(match.group(1)) if match else ""


def fetch_private_notes(
    opener: urllib.request.OpenerDirector,
    legacy_admin_url: str,
    legacy_person_id: int,
    stdout=None,
) -> list[dict]:
    """
    Return all private note activities for a person from the legacy CRM admin endpoint.

    Each returned dict has keys: activity_id, person_legacy_id, author_legacy_id,
    text, created_at.
    """
    notes: list[dict] = []
    page = 1
    while True:
        if stdout:
            stdout.write(f"  Fetching page {page}...")
        params = urllib.parse.urlencode(
            {
                "id": legacy_person_id,
                "page": page,
                "range": "All time",
                "type_id": "",
            }
        )
        url = f"{legacy_admin_url}/admin/activities/signup.json?{params}"
        req = urllib.request.Request(
            url,
            headers={"Referer": f"{legacy_admin_url}/admin/signups/{legacy_person_id}"},
        )
        with opener.open(req) as resp:
            data = json.loads(resp.read())
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
                        "text": extract_note_text(act.get("oneliner", "")),
                        "created_at": act.get("timestamp", ""),
                    }
                )
        if len(activities) < 20:
            break
        page += 1
        time.sleep(random.uniform(0.1, 0.3))
    return notes


def require_legacy_admin_env(cookie_file: str, legacy_admin_url: str) -> None:
    """Raise CommandError if either legacy admin credential is missing."""
    for var, val in [
        ("LEGACY_ADMIN_URL", legacy_admin_url),
        ("LEGACY_ADMIN_COOKIE_FILE", cookie_file),
    ]:
        if not val:
            raise CommandError(f"{var} is not set. Add it to .env or pass --cookie-file.")


def fetch_private_notes_via_cookie(
    cookie_file: str,
    legacy_admin_url: str,
    legacy_person_id: int,
    stdout=None,
) -> list[dict]:
    """
    Authenticate to the legacy CRM with a cookie file and fetch all private note
    activities for a person, translating connection failures into CommandError.
    """
    try:
        opener = build_cookie_opener(cookie_file)
    except FileNotFoundError as exc:
        raise CommandError(f"Cookie file not found: {cookie_file}") from exc

    if stdout:
        stdout.write("  Cookie file loaded. Fetching activities...")

    try:
        return fetch_private_notes(opener, legacy_admin_url, legacy_person_id, stdout)
    except urllib.error.HTTPError as e:
        raise CommandError(f"Legacy CRM request failed: {e.code} {e.reason} — {e.url}") from e
    except urllib.error.URLError as e:
        raise CommandError(f"Network error reaching legacy CRM: {e.reason}") from e


def write_jsonl(records: list[dict], file_path: str) -> None:
    """Write a list of dicts to a file as newline-delimited JSON."""
    with open(file_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def read_jsonl(file_path: str) -> list[dict]:
    """Read a list of dicts from a newline-delimited JSON file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def make_legacy_api_headers(api_token: str, user_agent: str) -> dict:
    """Build bearer-token headers for the legacy CRM's plain JSON API."""
    return {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }


def _normalize_interaction(contact: dict) -> dict:
    return {
        "contact_id": contact.get("contact_id"),
        "person_legacy_id": contact.get("person_id") or contact.get("recipient_id"),
        "author_legacy_id": contact.get("author_id") or contact.get("sender_id"),
        "method": contact.get("method", "") or "",
        "note": contact.get("note", "") or "",
        "status": contact.get("status", "") or "",
        "created_at": contact.get("created_at", ""),
    }


def _get_legacy_api_json(
    legacy_admin_url: str, path: str, api_headers: dict, params: dict | None = None
):
    url = f"{legacy_admin_url}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=api_headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read()), resp.status


def fetch_interactions_for_person(
    legacy_admin_url: str, api_headers: dict, legacy_person_id: int
) -> tuple[list[dict] | None, int]:
    """Return (normalized interactions, status) for a single legacy person ID."""
    data, status = _get_legacy_api_json(
        legacy_admin_url, f"/api/v1/people/{legacy_person_id}/contacts", api_headers, {"limit": 100}
    )
    if status != 200:
        return None, status
    return [_normalize_interaction(c) for c in data.get("results", [])], status


def fetch_all_interactions(legacy_admin_url: str, api_headers: dict, stdout=None) -> list[dict]:
    """Page through the legacy CRM's contacts API and return all normalized interactions."""
    interactions: list[dict] = []
    next_cursor = None
    page = 0

    while True:
        params = {"limit": 100}
        if next_cursor:
            params["next"] = next_cursor

        data, status = _get_legacy_api_json(
            legacy_admin_url, "/api/v1/contacts", api_headers, params
        )
        if status != 200:
            raise CommandError(f"Legacy CRM request failed on page {page + 1}: HTTP {status}")

        results = data.get("results", [])
        next_cursor = data.get("next")
        page += 1

        for contact in results:
            interactions.append(_normalize_interaction(contact))

        if stdout and page % 5 == 0:
            stdout.write(f"  Fetched {len(interactions)} interactions so far (page {page})...")

        if not results or not next_cursor:
            break

        time.sleep(0.1)

    return interactions


def fetch_interactions_via_api(
    legacy_admin_url: str,
    api_headers: dict,
    legacy_person_id: int | None = None,
    stdout=None,
) -> list[dict]:
    """
    Fetch interactions from the legacy CRM's bearer-token API — for one person if
    legacy_person_id is given, otherwise for everyone — translating connection
    failures into CommandError.
    """
    try:
        if legacy_person_id:
            raw, status = fetch_interactions_for_person(
                legacy_admin_url, api_headers, legacy_person_id
            )
            if raw is None:
                raise CommandError(
                    f"Legacy CRM returned HTTP {status} for person {legacy_person_id}."
                )
            return raw
        return fetch_all_interactions(legacy_admin_url, api_headers, stdout)
    except urllib.error.HTTPError as e:
        raise CommandError(f"Legacy CRM request failed: {e.code} {e.reason} — {e.url}") from e
    except urllib.error.URLError as e:
        raise CommandError(f"Network error reaching legacy CRM: {e.reason}") from e
