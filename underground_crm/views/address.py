"""
Public endpoints for address lookup.

The browser cannot query the Addressr container directly (it is only
reachable from the server, and exposes no CORS headers), so the autocomplete
widget calls this thin proxy instead.
"""

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from underground_crm import addressr


@require_GET
def address_suggestion_view(request: HttpRequest) -> JsonResponse:
    """
    Return address suggestions for the free-text query in the ``q`` parameter,
    as ``{"suggestions": ["1 COOK RD, LINDFIELD NSW 2070", ...]}``.

    Queries shorter than addressr.MINIMUM_QUERY_LENGTH yield an empty list
    without contacting Addressr at all.
    """
    query = (request.GET.get("q") or "").strip()
    if len(query) < addressr.MINIMUM_QUERY_LENGTH:
        return JsonResponse({"suggestions": []})

    suggestions: list[str] = [
        sla for entry in addressr.search(query) if isinstance(sla := entry.get("sla"), str)
    ]
    return JsonResponse({"suggestions": suggestions})
