import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..models.membership import Membership
from ..models.pages import RegistrationPage

Person = get_user_model()


def _redirect_to_next(request: HttpRequest) -> HttpResponse:
    """Return to the page the visitor came from, provided that it is on this
    site, so that neither membership view can be used as an open redirect."""
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("/")


@login_required
@require_POST
def add_membership_view(request: HttpRequest, page_id: int) -> HttpResponse:
    """
    Grant the requesting visitor the membership type that a live registration
    page offers. The type is taken from the page rather than from the request,
    so a visitor can only take up a membership that some page actually offers,
    and only through a page they are permitted to view.

    Nobody may hold two current memberships of the same type, so a visitor
    who already holds one (approved or still awaiting approval) is left as
    they are. An expired or cancelled membership is left in place as a record
    of the earlier term, and a new one is started beside it. The new
    membership awaits approval by staff.
    """
    page = get_object_or_404(RegistrationPage.objects.live(), pk=page_id)
    for restriction in page.get_view_restrictions():
        if not restriction.accept_request(request):
            raise Http404
    if page.membership is None:
        raise Http404

    with transaction.atomic():
        # Locking the visitor's row serializes their concurrent requests, so a
        # double click cannot pass the check below twice before either creates
        # a membership.
        person = Person.objects.select_for_update().get(pk=request.user.pk)
        offered = page.offers_membership_to(person)
        if offered:
            Membership.objects.create(
                person=person, type=page.membership, started_at=timezone.now()
            )
    if offered:
        messages.success(
            request,
            _(
                "Thank you for applying for a %(membership_type)s membership. "
                "It will become active once our staff have approved it."
            )
            % {"membership_type": page.membership.name},
        )
    else:
        messages.info(
            request,
            _("You already hold a %(membership_type)s membership.")
            % {"membership_type": page.membership.name},
        )
    return _redirect_to_next(request)


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
    return _redirect_to_next(request)
