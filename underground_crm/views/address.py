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
    as ``{"suggestions": [{"sla": "1 COOK RD, LINDFIELD NSW 2070", "gnaf_id":
    "GANSW705239062"}, ...]}`` — the single-line address the datalist presents,
    plus the G-NAF Address Detail PID identifying that exact address (null in
    the unexpected case of a match without a well-formed self link), which the
    autocomplete script stores in the form's hidden companion field so the
    submission resolves to the address the visitor picked rather than to a
    fresh text search's best guess.

    Queries shorter than addressr.MINIMUM_QUERY_LENGTH yield an empty list
    without contacting Addressr at all.
    """
    query = (request.GET.get("q") or "").strip()
    if len(query) < addressr.MINIMUM_QUERY_LENGTH:
        return JsonResponse({"suggestions": []})

    suggestions: list[dict[str, str | None]] = [
        {"sla": sla, "gnaf_id": addressr.gnaf_id_from_search_entry(entry)}
        for entry in addressr.search(query)
        if isinstance(sla := entry.get("sla"), str)
    ]
    return JsonResponse({"suggestions": suggestions})
