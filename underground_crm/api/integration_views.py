"""Read-only person and membership feed for trusted integrations.

The feed is deliberately narrow. It exposes only the generic CRM fields an
external application needs to match a person, decide whether an organisational
membership is active, and resolve a geographic branch from the CRM's existing
electorate fields. Internal CRM data such as notes, tags, donations,
interactions and addresses is not part of this contract.

Clients read incrementally with an opaque cursor. A cursor-based feed is used
rather than webhooks because it is replayable: a consumer that was offline can
resume at its last cursor, and a scheduled reconciliation can read the same
feed again without depending on delivery guarantees.

The feed is generic. It has no knowledge of branch structures, ballots or
governance roles, and can be used by any authorised consumer.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from datetime import datetime

from django.db.models import Prefetch, Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from ..models import Membership, Person
from .integration_auth import PEOPLE_READ_SCOPE, authenticate_integration

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 100
MAXIMUM_PAGE_SIZE = 200
MAXIMUM_EMAIL_LENGTH = 254
CURSOR_VERSION = 1


def _decode_cursor(raw: str) -> tuple[datetime, uuid.UUID] | None:
    """Decode a cursor, returning None when it is not one we issued."""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if payload.get("v") != CURSOR_VERSION:
            return None
        updated_at = parse_datetime(payload["updated_at"])
        person_id = uuid.UUID(payload["id"])
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeDecodeError, binascii.Error):
        return None
    if updated_at is None or timezone.is_naive(updated_at):
        return None
    return updated_at, person_id


def _encode_cursor(person: Person) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "updated_at": person.updated_at.isoformat(),
        "id": str(person.pk),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _parse_since(raw: str) -> datetime | None:
    """Parse an optional lower bound. Naive timestamps are rejected so that a
    caller cannot accidentally read a different window than they intended."""
    parsed = parse_datetime(raw)
    if parsed is None or timezone.is_naive(parsed):
        return None
    return parsed


def _membership_status(membership: Membership) -> str:
    if membership.suspended_at is not None:
        return "suspended"
    if membership.expires_on is not None and membership.expires_on < timezone.localdate():
        return "expired"
    return "active"


def _serialise_membership(membership: Membership) -> dict:
    status = _membership_status(membership)
    return {
        "id": str(membership.pk),
        "type_id": str(membership.type_id),
        "type_name": membership.type.name,
        "started_at": membership.started_at.isoformat(),
        "expires_on": membership.expires_on.isoformat() if membership.expires_on else None,
        "suspended_at": (membership.suspended_at.isoformat() if membership.suspended_at else None),
        "status": status,
        "is_active": status == "active",
    }


def _serialise_person(person: Person) -> dict:
    """Serialise only fields relevant to identification, membership and the
    existing geographic electorate fields. Addresses, notes, tags, donations,
    staff flags and authentication data are intentionally excluded."""
    return {
        "id": str(person.pk),
        "email": person.email,
        "email_is_bad": person.email_is_bad,
        "first_name": person.first_name or "",
        "last_name": person.last_name or "",
        "preferred_name": person.preferred_name or "",
        "full_name": person.full_name,
        "record_type": (
            "organisation" if person.record_type == person.TYPE_ORGANISATION else "person"
        ),
        "is_active": person.is_active,
        "is_deceased": person.is_deceased,
        "federal_district": person.federal_district or "",
        "state_upper_district": person.state_upper_district or "",
        "state_lower_district": person.state_lower_district or "",
        "council_district": person.council_district or "",
        "ward": person.ward or "",
        "updated_at": person.updated_at.isoformat(),
        "memberships": [
            _serialise_membership(membership) for membership in person.memberships.all()
        ],
    }


def _parse_page_size(raw: str | None) -> int | None:
    if raw is None:
        return DEFAULT_PAGE_SIZE
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return None
    if size < 1:
        return None
    return min(size, MAXIMUM_PAGE_SIZE)


def _people_queryset():
    return Person.objects.order_by("updated_at", "id").prefetch_related(
        Prefetch(
            "memberships",
            queryset=Membership.objects.select_related("type").order_by("-started_at", "id"),
        )
    )


def _page_response(page: list[Person], *, has_more: bool, page_size: int) -> JsonResponse:
    return JsonResponse(
        {
            "data": [_serialise_person(person) for person in page],
            "meta": {
                "limit": page_size,
                "has_more": has_more,
                "next_cursor": _encode_cursor(page[-1]) if page else None,
                "generated_at": timezone.now().isoformat(),
            },
        },
        headers={"Cache-Control": "no-store"},
    )


@csrf_exempt
@require_GET
def people(request: HttpRequest) -> JsonResponse:
    """Return people for initial matching or incremental reconciliation.

    ``email`` performs an exact, case-insensitive lookup for initial identity
    matching. It is a convenience for the consumer; it is not a durable
    identifier and consumers must retain the returned person UUID.

    Without ``email``, ``cursor`` or ``since``, the feed contains only people
    with at least one membership. An incremental request contains every person
    changed in the window, so a removed final membership is visible to the
    consumer rather than silently omitted.
    """
    client = authenticate_integration(request, PEOPLE_READ_SCOPE)
    if isinstance(client, JsonResponse):
        return client

    raw_email = request.GET.get("email", "").strip()
    if raw_email:
        if len(raw_email) > MAXIMUM_EMAIL_LENGTH:
            return JsonResponse({"error": "email is too long"}, status=400)
        rows = list(_people_queryset().filter(email__iexact=raw_email)[:2])
        logger.info("Integration client %s looked up %s people by email", client.name, len(rows))
        return _page_response(rows, has_more=False, page_size=max(1, len(rows)))

    page_size = _parse_page_size(request.GET.get("limit"))
    if page_size is None:
        return JsonResponse({"error": "limit must be a positive integer"}, status=400)

    raw_cursor = request.GET.get("cursor", "").strip()
    cursor: tuple[datetime, uuid.UUID] | None = None
    if raw_cursor:
        cursor = _decode_cursor(raw_cursor)
        if cursor is None:
            return JsonResponse({"error": "cursor is invalid"}, status=400)

    raw_since = request.GET.get("since", "").strip()
    since: datetime | None = None
    if raw_since and cursor is None:
        since = _parse_since(raw_since)
        if since is None:
            return JsonResponse(
                {"error": "since must be an ISO 8601 timestamp with a timezone"}, status=400
            )

    incremental = cursor is not None or since is not None
    queryset = _people_queryset()
    if not incremental:
        # Initial reads are limited to the population an integration is for.
        queryset = queryset.filter(memberships__isnull=False).distinct()

    if cursor is not None:
        updated_at, person_id = cursor
        queryset = queryset.filter(
            Q(updated_at__gt=updated_at) | Q(updated_at=updated_at, id__gt=person_id)
        )
    elif since is not None:
        queryset = queryset.filter(updated_at__gte=since)

    # One extra row tells us whether another page exists without a count query.
    rows = list(queryset[: page_size + 1])
    has_more = len(rows) > page_size
    page = rows[:page_size]

    logger.info(
        "Integration client %s read %s people (%s more)",
        client.name,
        len(page),
        "with" if has_more else "no",
    )
    return _page_response(page, has_more=has_more, page_size=page_size)


@csrf_exempt
@require_GET
def person_detail(request: HttpRequest, person_id: uuid.UUID) -> JsonResponse:
    """Return one person by their stable CRM UUID.

    Consumers should use this after they have linked an external identity. It
    deliberately does not accept email as a substitute for the UUID.
    """
    client = authenticate_integration(request, PEOPLE_READ_SCOPE)
    if isinstance(client, JsonResponse):
        return client

    person = _people_queryset().filter(pk=person_id).first()
    if person is None:
        return JsonResponse(
            {"error": "not found"}, status=404, headers={"Cache-Control": "no-store"}
        )
    return JsonResponse({"data": _serialise_person(person)}, headers={"Cache-Control": "no-store"})
