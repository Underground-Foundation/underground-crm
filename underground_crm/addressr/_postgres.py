"""
PostgreSQL/G-NAF backend for address search and geocoding.

Queries the gnaf.address table (created by the import_gnaf management command)
using pg_trgm similarity search. No external services required.

Imported by underground_crm.addressr as the primary backend when
ADDRESSR_BACKEND is \"auto\" or \"postgres\".
"""

import logging
import re
from decimal import Decimal
from typing import NamedTuple

from django.db import connection, ProgrammingError

logger = logging.getLogger(__name__)

MINIMUM_QUERY_LENGTH: int = 5

GNAF_ID_PATTERN = re.compile(r"[A-Z0-9_]{1,32}")


class StructuredAddress(NamedTuple):
    line1: str | None
    city: str | None
    state: str | None
    postcode: str | None


class Geocode(NamedTuple):
    latitude: Decimal
    longitude: Decimal
    reliability: int | None
    confidence: int | None
    address: StructuredAddress | None
    gnaf_id: str | None = None
    sla: str | None = None


def search(query: str) -> list[dict]:
    if len(query) < MINIMUM_QUERY_LENGTH:
        return []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT gnaf_pid, sla
                FROM gnaf.address
                WHERE sla % %s
                ORDER BY similarity(sla, %s) DESC
                LIMIT 20
                """,
                [query, query],
            )
            return [
                {"sla": sla, "links": {"self": {"href": f"/addresses/{gnaf_pid}"}}}
                for gnaf_pid, sla in cursor.fetchall()
            ]
    except ProgrammingError:
        logger.warning("G-NAF schema not available, returning empty results")
        return []


def gnaf_id_from_search_entry(entry: dict) -> str | None:
    href = ((entry.get("links") or {}).get("self") or {}).get("href")
    if not isinstance(href, str):
        return None
    gnaf_id = href.rsplit("/", 1)[-1]
    return gnaf_id if GNAF_ID_PATTERN.fullmatch(gnaf_id) else None


def geocode(query: str) -> Geocode | None:
    suggestions = search(query)
    if not suggestions:
        return None
    gnaf_id = gnaf_id_from_search_entry(suggestions[0])
    if not gnaf_id:
        return None
    return geocode_by_id(gnaf_id)


def geocode_by_id(gnaf_id: str) -> Geocode | None:
    if not GNAF_ID_PATTERN.fullmatch(gnaf_id):
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT gnaf_pid, latitude, longitude, geocode_type, confidence,
                   street_number, street_name, street_type,
                   locality_name, state_abbreviation, postcode, sla
            FROM gnaf.address
            WHERE gnaf_pid = %s
            """,
            [gnaf_id],
        )
        row = cursor.fetchone()
        if not row:
            return None
        (pid, lat, lng, geocode_type, confidence,
         street_number, street_name, street_type,
         locality, state, postcode, sla) = row
        line1 = " ".join(p for p in [street_number, street_name, street_type] if p) or None
        structured = StructuredAddress(
            line1=line1,
            city=locality,
            state=state,
            postcode=postcode,
        )
        reliability_map = {
            "BUILDING CENTROID": 1,
            "PROPERTY CENTROID": 2,
            "STREET LOCALITY": 3,
            "LOCALITY": 4,
            "POSTCODE": 5,
        }
        return Geocode(
            latitude=Decimal(str(lat)) if lat is not None else Decimal("0"),
            longitude=Decimal(str(lng)) if lng is not None else Decimal("0"),
            reliability=reliability_map.get(geocode_type) if geocode_type else None,
            confidence=confidence,
            address=structured,
            gnaf_id=pid,
            sla=sla,
        )
