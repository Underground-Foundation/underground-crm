"""Opt-in, minimal membership eligibility lookup for Discord integrations."""

import logging
from uuid import UUID

from allauth.socialaccount.models import SocialAccount
from cachetools import cached
from django.conf import settings
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from underground_crm.models import Membership, MembershipType

logger = logging.getLogger(__name__)


@cached({})
def get_membership_configuration_or_error():
    token = getattr(settings, "DISCORD_MEMBERSHIP_INTEGRATION_TOKEN", "")
    if not token or not isinstance(token, str):
        logger.error(
            "No valid DISCORD_MEMBERSHIP_INTEGRATION_TOKEN is configured for validating this request"
        )
        return None, None, JsonResponse({"error": "Unavailable"}, status=503)
    elif len(token) < 32:
        logger.warning(
            "This token for DISCORD_MEMBERSHIP_INTEGRATION_TOKEN is too short to be secure: %s",
            token,
        )
        return None, None, JsonResponse({"error": "Misconfigured"}, status=503)
    membership_type = getattr(settings, "DISCORD_MEMBERSHIP_TYPE_ID", "")
    if not membership_type:
        logger.error("No DISCORD_MEMBERSHIP_TYPE_ID is configured for checking this membership")
        return None, None, JsonResponse({"error": "Unavailable"}, status=503)
    try:
        membership_type = UUID(str(membership_type))
    except (ValueError, TypeError, AttributeError):
        logger.error("The configured membership type %s is not a UUID", membership_type)
        return None, None, JsonResponse({"error": "integration unavailable"}, status=503)
    if not MembershipType.objects.filter(pk=membership_type).exists():
        logger.error("Membership type %s does not exist", membership_type)
        return JsonResponse({"error": "integration unavailable"}, status=503)
    return token, membership_type, None


@never_cache
@require_GET
def discord_membership(request: HttpRequest, discord_id: str) -> JsonResponse:
    """Reveal only eligibility to a dedicated integration credential."""
    token, membership_type, error = get_membership_configuration_or_error()
    if error:
        return error

    authorization = request.headers.get("Authorization", "")
    if not authorization or not constant_time_compare(authorization, f"Bearer {token}"):
        logger.error(
            "A request for checking Discord membership was unauthorized: %s", authorization
        )
        response = JsonResponse({"error": "unauthorized"}, status=401)
        response["WWW-Authenticate"] = "Bearer"
        return response

    # Do not accept display names or ambiguous provider identities.
    if not discord_id.isascii() or not discord_id.isdecimal() or not 17 <= len(discord_id) <= 20:
        return JsonResponse({"error": "invalid Discord ID"}, status=400)
    people = SocialAccount.objects.filter(provider="discord", uid=discord_id).values("user_id")
    now = timezone.now()
    eligible = (
        Membership.objects.filter(
            person_id__in=people,
            type_id=membership_type,
            started_at__lte=now,
            suspended_at__isnull=True,
        )
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=timezone.localdate(now)))
        .exists()
    )
    logger.info(
        "A request to check the membership of %s revealed that they are %s a member",
        discord_id,
        "indeed" if eligible else "not",
    )
    return JsonResponse({"eligible": eligible})
