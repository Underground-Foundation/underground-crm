"""
This code enables a FeedPage's children to be requested at URLs where the feed's
own slug is not present as a breadcrumb in the path, just as legacy CMS pages
may have been handled.

Wagtail resolves a request by walking the page tree one path segment at a
time (wagtail.models.Page.route()), matching each segment against the slug
of a live child at that level.
"""

from django.http import HttpRequest, Http404
from wagtail.models import Page
from wagtail.url_routing import RouteResult

from .models.pages import FeedPage

_original_route = Page.route


def _route_with_flattened_feed_children(
    self: Page, request: HttpRequest, path_components: list[str]
) -> RouteResult:
    try:
        return _original_route(self, request, path_components)
    except Http404:
        if not path_components:
            raise
        for feed_child in self.get_children().type(FeedPage).specific():
            if feed_child.show_slug_as_breadcrumb:
                continue
            try:
                return feed_child.route(request, path_components)
            except Http404:
                continue
        raise


def patch_page_routing() -> None:
    """Idempotent, so it is safe to call more than once (e.g. if ready() ever
    runs twice under a particular test runner or autoreload setup)."""
    if getattr(Page.route, "_flattens_feed_children", False):
        return
    _route_with_flattened_feed_children._flattens_feed_children = True
    Page.route = _route_with_flattened_feed_children
