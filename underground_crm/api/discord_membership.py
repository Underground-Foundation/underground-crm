"""Opt-in, minimal membership eligibility lookup for Discord integrations."""

from uuid import UUID

from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from underground_crm.models import Membership, MembershipType


@never_cache
@require_GET
def discord_membership(request: HttpRequest, discord_id: str) -> JsonResponse:
    """Reveal only eligibility to a dedicated integration credential."""
    token = getattr(settings, "DISCORD_MEMBERSHIP_INTEGRATION_TOKEN", "")
    membership_type = getattr(settings, "DISCORD_MEMBERSHIP_TYPE_ID", "")
    try:
        membership_type = UUID(str(membership_type))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({"error": "integration unavailable"}, status=503)
    if not isinstance(token, str) or len(token) < 32:
        return JsonResponse({"error": "integration unavailable"}, status=503)

    authorization = request.headers.get("Authorization", "")
    if not constant_time_compare(authorization, f"Bearer {token}"):
        response = JsonResponse({"error": "unauthorized"}, status=401)
        response["WWW-Authenticate"] = "Bearer"
        return response

    # A deleted or mistyped membership type is an outage, never a reason for
    # the downstream integration to revoke every member's role.
    if not MembershipType.objects.filter(pk=membership_type).exists():
        return JsonResponse({"error": "integration unavailable"}, status=503)

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
    return JsonResponse({"eligible": eligible})
