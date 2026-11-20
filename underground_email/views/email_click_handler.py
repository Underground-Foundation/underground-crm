import logging
from typing import Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core import signing
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from wagtail.models import Site

from underground_email.models import EmailCampaign, UnsubscriptionPage
from underground_email.unsubscription import (
    cancel_own_membership,
    decode_unsubscription_token,
    remove_self_queryable_tag,
    unsubscribe_from_tag_via_email_campaign,
)

logger = logging.getLogger(__name__)

Person = get_user_model()


def _find_unsubscription_page(request: HttpRequest) -> Optional[UnsubscriptionPage]:
    """The live UnsubscriptionPage of the site being visited, if it has one."""
    pages = UnsubscriptionPage.objects.live()
    site = Site.find_for_request(request)
    if site is not None:
        pages = pages.descendant_of(site.root_page, inclusive=True)
    return pages.first()


def _press_button(
    request: HttpRequest, page: UnsubscriptionPage, visitor: settings.AUTH_USER_MODEL
) -> None:
    """Carry out a button press on the unsubscription page, reporting the
    outcome to the visitor through the messages framework."""
    action = request.POST.get("action")
    if action == UnsubscriptionPage.REMOVE_TAG_ACTION:
        tag = remove_self_queryable_tag(
            visitor, tag_id=request.POST.get("tag_id", ""), page_title=page.title
        )
        if tag is None:
            messages.info(request, _("You were not subscribed to that list."))
        else:
            messages.success(
                request,
                _("You will no longer receive %(tag)s emails.") % {"tag": tag.name},
            )
    elif action == UnsubscriptionPage.CANCEL_MEMBERSHIP_ACTION:
        membership = cancel_own_membership(
            visitor, membership_id=request.POST.get("membership_id", "")
        )
        if membership is None:
            messages.info(request, _("That membership is not current."))
        else:
            messages.success(
                request,
                _("Your %(membership_type)s membership has been cancelled.")
                % {"membership_type": membership.type.name},
            )


@require_http_methods(["GET", "POST"])
def unsubscription_view(request: HttpRequest) -> HttpResponse:
    """Serve the site's UnsubscriptionPage, after carrying out any unsubscription asked of it.

    The page is about the person named by the 'address' query parameter, if
    there is one, or else the person who is logged in. 'address' is a signed
    token produced by make_unsubscription_url and put into the link at the foot
    of each campaign email. Verifying the signature proves that the request came
    from someone who actually received the email, even if they are not logged in.

    Following such a link (a GET) unsubscribes the recipient from that campaign's
    list straight away, as it always has. A POST carries either a press of one of
    the page's buttons (to cancel a membership or leave a list) or an answer to
    the page's own form questions; neither repeats the campaign's unsubscription.
    When the site has no UnsubscriptionPage, an email's link still unsubscribes
    its recipient, and a plain confirmation is shown instead.
    """
    token = request.GET.get("address", "")
    campaign_title = None
    if token:
        try:
            email_address, utm_id = decode_unsubscription_token(token)
        except (signing.BadSignature, KeyError, ValueError):
            logger.warning("Invalid unsubscription token received from %s: %s", request.user, token)
            return render(request, "underground_email/unsubscribe_invalid.html", status=400)
        visitor = Person.objects.filter(email=email_address).first()
        if visitor is None:
            logger.warning("Nobody has the address %s, so they cannot unsubscribe", email_address)
            return render(request, "underground_email/unsubscribe_invalid.html", status=400)
        if request.method == "GET":
            try:
                campaign_title = unsubscribe_from_tag_via_email_campaign(utm_id, email_address)
            except EmailCampaign.DoesNotExist:
                logger.warning(
                    "No campaign %s was found, for unsubscribing %s", utm_id, email_address
                )
                return render(request, "underground_email/unsubscribe_invalid.html", status=400)
    else:
        visitor = request.user if request.user.is_authenticated else None

    page = _find_unsubscription_page(request)
    if page is None:
        if token:
            return render(
                request,
                "underground_email/unsubscribe_confirmed.html",
                {"campaign_title": campaign_title},
            )
        raise Http404

    if token and request.method == "GET":
        if campaign_title:
            messages.success(
                request,
                _("You will no longer receive %(tag)s emails.") % {"tag": campaign_title},
            )
        else:
            messages.success(request, _("Your unsubscription has been recorded."))
    elif request.method == "POST" and request.POST.get("action") in UnsubscriptionPage.ACTIONS:
        if visitor is None:
            messages.warning(request, _("Please log in to manage your subscriptions."))
        else:
            _press_button(request, page, visitor)

    return page.serve(request, visitor=visitor)
