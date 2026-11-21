"""Scoped bearer-token authentication for external integration endpoints.

Underground CRM's staff API uses a staff account and a JWT or session. That is
the wrong credential for a server-to-server integration: it grants the whole
staff surface, it cannot be scoped to one read-only feed, and revoking it means
disabling a person. This module gives deployments a separate, least-privilege
credential instead.

Tokens are configured out-of-band in ``INTEGRATION_API_TOKENS`` as JSON::

    {"tokens": [
        {"name": "member-portal", "token": "<at least 32 characters>",
         "scopes": ["people:read"]}
    ]}

Each token is independent, so one integration can be rotated or revoked without
affecting another. The token is never logged. Only the token's configured
``name`` is used in operational logs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.http import HttpRequest, JsonResponse

logger = logging.getLogger(__name__)

PEOPLE_READ_SCOPE = "people:read"
MINIMUM_TOKEN_LENGTH = 32


@dataclass(frozen=True)
class IntegrationClient:
    """The configured identity behind a validated integration token."""

    name: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _json_error(message: str, status: int, *, authenticate: bool = False) -> JsonResponse:
    response = JsonResponse({"error": message}, status=status)
    response["Cache-Control"] = "no-store"
    if authenticate:
        response["WWW-Authenticate"] = "Bearer"
    return response


def _configured_clients() -> list[tuple[str, IntegrationClient]]:
    """Return configured tokens and their clients, skipping invalid entries.

    A malformed configuration fails closed: the endpoint is then treated as
    unconfigured rather than accepting a token whose scope we could not read.
    """
    raw = getattr(settings, "INTEGRATION_API_TOKENS", "")
    if not raw:
        return []

    try:
        payload: Any = json.loads(raw)
    except (TypeError, ValueError):
        logger.error("INTEGRATION_API_TOKENS is not valid JSON; integrations are disabled")
        return []

    entries = payload.get("tokens") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        logger.error("INTEGRATION_API_TOKENS must contain a list of tokens")
        return []

    clients: list[tuple[str, IntegrationClient]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            logger.error("Skipping a non-object entry in INTEGRATION_API_TOKENS")
            continue
        name = entry.get("name")
        token = entry.get("token")
        scopes = entry.get("scopes")
        if not isinstance(name, str) or not name.strip():
            logger.error("Skipping an integration token without a name")
            continue
        if not isinstance(token, str) or len(token) < MINIMUM_TOKEN_LENGTH:
            logger.error(
                "Skipping integration token %s: tokens must be at least %s characters",
                name,
                MINIMUM_TOKEN_LENGTH,
            )
            continue
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            logger.error("Skipping integration token %s: scopes must be a list of strings", name)
            continue
        clients.append((token, IntegrationClient(name=name.strip(), scopes=frozenset(scopes))))
    return clients


def authenticate_integration(
    request: HttpRequest, required_scope: str
) -> IntegrationClient | JsonResponse:
    """Validate the request's bearer token and required scope.

    Returns the client on success, or a ready-to-return JSON error response.
    Comparisons run over SHA-256 digests so neither the token's length nor a
    matching prefix can be inferred from response timing.
    """
    clients = _configured_clients()
    if not clients:
        logger.error("An integration endpoint was called without any configured tokens")
        return _json_error("Integration API is not configured.", 503)

    scheme, _separator, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        logger.warning("Refusing an integration request without a bearer token")
        return _json_error("Unauthorized", 401, authenticate=True)

    presented = hashlib.sha256(token.encode()).digest()
    matched: IntegrationClient | None = None
    for configured_token, client in clients:
        expected = hashlib.sha256(configured_token.encode()).digest()
        if hmac.compare_digest(presented, expected):
            matched = client

    if matched is None:
        logger.warning("Refusing an integration request with an unknown bearer token")
        return _json_error("Unauthorized", 401, authenticate=True)

    if not matched.has_scope(required_scope):
        logger.warning(
            "Integration client %s attempted to use scope %s without holding it",
            matched.name,
            required_scope,
        )
        return _json_error("Forbidden", 403)

    return matched
