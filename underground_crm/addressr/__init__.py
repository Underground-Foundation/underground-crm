"""
Address search and geocoding — dispatcher module.

Selects the active backend based on ADDRESSR_BACKEND setting:
  "auto" — try PostgreSQL/G-NAF first, fall back to HTTP Addressr
  "postgres" — PostgreSQL/G-NAF only
  "addressr" — HTTP Addressr only (original behaviour)

All public symbols are re-exported from the active backend so that
consumers just do ``from underground_crm import addressr`` as before.
"""

import logging
import os
import re
from decimal import Decimal
from typing import NamedTuple

from django.db import connection, ProgrammingError

logger = logging.getLogger(__name__)

# These types are defined at this level so consumers can import them
# without knowing which backend is active.
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


MINIMUM_QUERY_LENGTH: int = 5
GNAF_ID_PATTERN = re.compile(r"[A-Z0-9_]{1,32}")


def _select_backend() -> str:
    backend = os.environ.get("ADDRESSR_BACKEND", "auto")
    if backend == "postgres":
        return "postgres"
    if backend == "addressr":
        return "addressr"
    # "auto" — probe for G-NAF table
    if backend == "auto":
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_schema = 'gnaf' AND table_name = 'address')"
                )
                (exists,) = cursor.fetchone()
                if exists:
                    return "postgres"
        except Exception:
            pass
    return "addressr"


_active_backend = _select_backend()
logger.info("Address search backend: %s", _active_backend)

if _active_backend == "postgres":
    from ._postgres import search, geocode, geocode_by_id, gnaf_id_from_search_entry
else:
    from ._http import search, geocode, geocode_by_id, gnaf_id_from_search_entry


__all__ = [
    "MINIMUM_QUERY_LENGTH",
    "GNAF_ID_PATTERN",
    "StructuredAddress",
    "Geocode",
    "search",
    "geocode",
    "geocode_by_id",
    "gnaf_id_from_search_entry",
]
