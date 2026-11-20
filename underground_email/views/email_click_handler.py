from typing import Optional

from django.http import Http404, HttpRequest, HttpResponse
from django.views.decorators.http import require_http_methods
from wagtail.models import Site

from underground_email.models import UnsubscriptionPage


def _find_unsubscription_page(request: HttpRequest) -> Optional[UnsubscriptionPage]:
    """The live UnsubscriptionPage of the site being visited, or else any live
    one, for a site added after migration 0018 gave each site its own."""
    pages = UnsubscriptionPage.objects.live().specific()
    site = Site.find_for_request(request)
    if site is not None:
        page = pages.descendant_of(site.root_page, inclusive=True).first()
        if page is not None:
            return page
    return pages.first()


@require_http_methods(["GET", "POST"])
def unsubscription_view(request: HttpRequest) -> HttpResponse:
    """Serve the UnsubscriptionPage at /unsubscribe, the address that the link at
    the foot of each campaign email points to (see make_unsubscription_url).

    Following the link (a GET) changes nothing: it only shows the page, whose
    form carries the link's signed token in a hidden field. We have the page,
    rather than unsubscribing people here and now, to get around over-eager
    email clients and security scanners, which follow the links in a message
    before its recipient has read it. People unsubscribe by submitting the form
    (a POST), which UnsubscriptionPage hands to UnsubscriptionForm.
    """
    page = _find_unsubscription_page(request)
    if page is None:
        raise Http404
    return page.serve(request)
