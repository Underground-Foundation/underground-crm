"""
Thin client for the Addressr geocoding API (https://github.com/mountain-pass/addressr).

Addressr must be running and seeded with GNAF data before these functions will
return results. See docker-compose.yml for the service definition.

Notice that the API spec at http://localhost:8080/api-docs might differ from https://addressr.io/api-docs/
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import NamedTuple

from django.conf import settings

logger = logging.getLogger(__name__)

# Queries shorter than this return far too many low-quality matches to be
# useful, so both the autocomplete widget and the suggestion view refuse to
# search until the visitor has typed at least this many characters.
MINIMUM_QUERY_LENGTH: int = 5

# A G-NAF Address Detail PID, e.g. "GANSW705239062" — the stable identifier
# G-NAF assigns to each address, which Addressr uses as its address ID.
# Anything not matching this shape is refused before it can reach a URL path,
# because visitor-supplied IDs flow through geocode_by_id().
GNAF_ID_PATTERN = re.compile(r"[A-Z0-9_]{1,32}")


class StructuredAddress(NamedTuple):
    """The structured address components of an Addressr match, suitable for
    overwriting the free-text fields on our own Address model."""

    line1: str | None
    city: str | None
    state: str | None
    postcode: str | None


class Geocode(NamedTuple):
    latitude: Decimal
    longitude: Decimal
    # Spatial precision: 1 (surveyed/exact) to 6 (postcode-region level).
    reliability: int | None
    # Number of source databases that contain this address, minus one.
    # 0 means one database; higher values mean more sources corroborate the address.
    confidence: int | None
    # The matched address's own structured components, or None if Addressr
    # returned no structured breakdown for it.
    address: StructuredAddress | None
    # The matched address's G-NAF Address Detail PID, e.g. "GANSW705239062".
    gnaf_id: str | None = None
    # The matched address's single-line form, e.g. "1 COOK RD, LINDFIELD NSW 2070".
    sla: str | None = None


def _get(path: str) -> dict | None:
    url = settings.ADDRESSR_BASE_URL.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read())
    except urllib.error.URLError as exc:
        logger.warning("Addressr request failed for %s: %s", url, exc)
        return None


def search(query: str) -> list[dict]:
    """Return raw address suggestions for a free-text query string.
    The response format is eg:
    [{'sla': '1 COOK RD, LINDFIELD NSW 2070', 'score': 264.7019, 'pid': 'GANSW705239062'}]

    Older Addressr releases carried the same identifier as a HAL-style self
    link instead, and no 'pid':
    [{'sla': '1 COOK RD, LINDFIELD NSW 2070', 'score': 264.7019, 'links': {'self': {'href': '/addresses/GANSW705239062'}}}]
    """
    encoded = urllib.parse.urlencode({"q": query})
    result = _get(f"/addresses?{encoded}")
    return result if isinstance(result, list) else []


def gnaf_id_from_search_entry(entry: dict) -> str | None:
    """The G-NAF Address Detail PID of one search() entry, or None when the
    entry carries no well-formed identifier.

    Both response shapes in search()'s docstring are accepted. Addressr 3.3.x
    returns the PID as a plain "pid" field; earlier releases returned no "pid"
    and instead only a self link to "/addresses/<pid>". Reading just the link
    fails in a way that is easy to miss on an upgrade, because nothing errors:
    search() still returns correctly-scored matches, so a geocoding run reports
    every address as simply "no result", and the autocomplete in
    views/address.py hands the form a null gnaf_id for a suggestion the visitor
    can see is right.
    """
    pid = entry.get("pid")
    if not isinstance(pid, str):
        href = ((entry.get("links") or {}).get("self") or {}).get("href")
        if not isinstance(href, str):
            return None
        pid = href.rsplit("/", 1)[-1]
    return pid if GNAF_ID_PATTERN.fullmatch(pid) else None


def _extract_structured_address(detail: dict) -> StructuredAddress | None:
    """
    Build a StructuredAddress from an Addressr address detail response.

    ``mla`` (multi-line address) always ends with a "LOCALITY STATE POSTCODE"
    line preceded by a "NUMBER STREET" line — a leading flat/unit line, if
    present, comes before that — so the street line is always the second-last
    entry regardless of whether a flat/unit is present.
    """
    structured = detail.get("structured")
    mla = detail.get("mla")
    if not structured or not isinstance(mla, list) or len(mla) < 2:
        return None

    state = structured.get("state") or {}
    locality = structured.get("locality") or {}
    return StructuredAddress(
        line1=mla[-2] or None,
        city=locality.get("name"),
        state=state.get("abbreviation"),
        postcode=structured.get("postcode"),
    )


def _geocode_from_detail(detail: dict, gnaf_id: str) -> Geocode | None:
    """Build a Geocode from an Addressr address detail response, or None when
    the detail carries no usable geocode."""
    geocodes = detail.get("geocoding", {}).get("geocodes", [])
    if not geocodes:
        logger.debug("No geocodes available for address %s", gnaf_id)
        return None

    # Prefer the entry marked as default; fall back to the first entry.
    best = next((g for g in geocodes if g.get("default")), geocodes[0])
    try:
        reliability = best.get("reliability", {}).get("code")
        # Addressr nests confidence under "structured", not at the top level.
        confidence = (detail.get("structured") or {}).get("confidence")
        sla = detail.get("sla")
        return Geocode(
            latitude=Decimal(str(best["latitude"])),
            longitude=Decimal(str(best["longitude"])),
            reliability=int(reliability) if reliability is not None else None,
            confidence=confidence,
            address=_extract_structured_address(detail),
            gnaf_id=gnaf_id,
            sla=sla if isinstance(sla, str) else None,
        )
    except (KeyError, ValueError) as exc:
        logger.warning("Addressr returned unexpected geocode shape: %s", exc)
        return None


def geocode(query: str) -> Geocode | None:
    """
    Return the best geocode for a free-text address string, or None if Addressr
    cannot find a match or is unavailable.
    """
    suggestions = search(query)
    if not suggestions:
        logger.debug("No address suggestions for %s", query)
        return None

    gnaf_id = gnaf_id_from_search_entry(suggestions[0])
    if not gnaf_id:
        logger.debug("No ID for the first address suggestion")
        return None

    return geocode_by_id(gnaf_id)


def geocode_by_id(gnaf_id: str) -> Geocode | None:
    """
    Return the geocode of the address carrying the given G-NAF Address Detail
    PID, or None if the PID is malformed or unknown, or Addressr is
    unavailable. Unlike geocode(), this resolves one exact address rather than
    the best-scoring match for a piece of text.
    """
    if not GNAF_ID_PATTERN.fullmatch(gnaf_id):
        logger.warning("Refusing to look up the malformed G-NAF ID %r", gnaf_id)
        return None

    detail = _get(f"/addresses/{gnaf_id}")
    if not detail:
        logger.debug("No detail available for address %s", gnaf_id)
        return None

    return _geocode_from_detail(detail, gnaf_id)
