from typing import Callable

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
)


class UrlRedirectMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from underground_crm.models import UrlRedirect

        try:
            redirect = UrlRedirect.objects.select_related("redirect_page").get(
                old_path=request.path_info
            )
        except UrlRedirect.DoesNotExist:
            return self.get_response(request)

        url = redirect.get_redirect_url()
        if redirect.is_permanent:
            return HttpResponsePermanentRedirect(url)
        return HttpResponseRedirect(url)
