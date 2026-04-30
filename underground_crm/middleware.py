from typing import Callable

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
)


class UrlRedirectionMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from underground_crm.models import UrlRedirection

        try:
            redirection = UrlRedirection.objects.select_related("destination_page").get(
                old_path=request.path_info
            )
        except UrlRedirection.DoesNotExist:
            return self.get_response(request)

        url = redirection.get_destination_url()
        if redirection.is_permanent:
            return HttpResponsePermanentRedirect(url)
        return HttpResponseRedirect(url)
