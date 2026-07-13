import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..models.membership import Membership


@login_required
@require_POST
def cancel_membership_view(request: HttpRequest, membership_id: uuid.UUID) -> HttpResponse:
    """
    Cancel one of the requesting visitor's own memberships by bringing its
    expiry forward to today. Keyed on person=request.user as well as the
    membership id, so a membership id alone is never enough to cancel
    somebody else's membership.
    """
    membership = get_object_or_404(Membership, pk=membership_id, person=request.user)
    membership.expires_on = timezone.now().date()
    membership.save(update_fields=["expires_on"])

    messages.success(
        request,
        _("Your %(membership_type)s membership has been cancelled.")
        % {"membership_type": membership.type.name},
    )

    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("/")
