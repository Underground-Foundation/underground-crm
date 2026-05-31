"""
Shared utilities for legacy CRM import management commands.
"""

import html
import http.cookiejar
import json
import random
import re
import time
import urllib.parse
import urllib.request

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
