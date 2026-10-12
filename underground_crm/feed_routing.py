"""
This code enables the pages beneath a BasicPage to be addressed at URLs where
that page's own slug is not present as a breadcrumb in the path, just as
legacy CMS pages may have been handled. A BasicPage opts out of appearing in
its children's URLs by leaving show_slug_as_breadcrumb disabled (the default).
Feed pages are a BasicPage, so their items are covered by the same rule.

Wagtail resolves a request by walking the page tree one path segment at a
time (wagtail.models.Page.route()), matching each segment against the slug
of a live child at that level. It builds a page's URL the other way round:
from its url_path, which is the parent's url_path followed by the page's
slug. Three Page methods are therefore patched, each on Page itself because
Wagtail's page-moving action works with plain Page instances rather than the
specific ones:

- route(), so a request for a flattened child finds it one level up;
- set_url_path(), so a flattened child's url_path omits its parent's slug;
- _update_descendant_url_paths(), so renaming or moving a page that hides
  its slug rewrites its descendants' url_paths by the prefix they actually
  share, which is where the page's own parent sits.
"""

from django.http import HttpRequest, Http404
from wagtail.models import Page
from wagtail.url_routing import RouteResult

from .models.pages import BasicPage

_original_route = Page.route
_original_set_url_path = Page.set_url_path
_original_update_descendant_url_paths = Page._update_descendant_url_paths


def _hides_slug(page: Page) -> bool:
    """
    Whether the pages beneath `page` are addressed without its slug. Only a
    BasicPage can choose this; every other page type is always a breadcrumb.
    """
    show_slug = getattr(page, "show_slug_as_breadcrumb", None)
    if show_slug is None:
        show_slug = (
            BasicPage.objects.filter(pk=page.pk)
            .values_list("show_slug_as_breadcrumb", flat=True)
            .first()
        )
    return show_slug is False


def _url_path_of_parent(url_path: str) -> str:
    """The url_path of whatever precedes the last slug in `url_path`, e.g.
    "/policy/climate-rescue/" gives "/policy/"."""
    return url_path.rstrip("/").rpartition("/")[0] + "/"


def _route_with_flattened_children(
    self: Page, request: HttpRequest, path_components: list[str]
) -> RouteResult:
    try:
        return _original_route(self, request, path_components)
    except Http404:
        if not path_components:
            raise
        # A page with no children has nothing to offer, which also keeps the
        # lookup cheap on the many 404s that never involved a hidden slug.
        hiding_children = (
            BasicPage.objects.child_of(self)
            .filter(show_slug_as_breadcrumb=False, numchild__gt=0)
            .specific()
        )
        for hiding_child in hiding_children:
            try:
                return hiding_child.route(request, path_components)
            except Http404:
                continue
        raise


def _set_url_path_beneath_visible_ancestor(self: Page, parent: Page | None) -> str:
    if parent is None or not _hides_slug(parent):
        return _original_set_url_path(self, parent)
    # The parent's url_path ends in its own slug, which is what is left out.
    # A parent that is itself hidden has already left out its own parent's
    # slug, so this nests to any depth.
    self.url_path = parent.url_path.removesuffix(f"{parent.slug}/") + f"{self.slug}/"
    return self.url_path


def _update_descendant_url_paths_beneath_visible_ancestor(
    self: Page, old_url_path: str, new_url_path: str
) -> None:
    if _hides_slug(self):
        # This page's slug is in none of its descendants' url_paths, so
        # renaming it changes nothing for them. Moving it does: they share
        # their old parent's url_path as a prefix.
        old_url_path = _url_path_of_parent(old_url_path)
        new_url_path = _url_path_of_parent(new_url_path)
        if old_url_path == new_url_path:
            return
    _original_update_descendant_url_paths(self, old_url_path, new_url_path)


def patch_page_routing() -> None:
    """Idempotent, so it is safe to call more than once (e.g. if ready() ever
    runs twice under a particular test runner or autoreload setup)."""
    if getattr(Page.route, "_flattens_hidden_slugs", False):
        return
    _route_with_flattened_children._flattens_hidden_slugs = True
    Page.route = _route_with_flattened_children
    Page.set_url_path = _set_url_path_beneath_visible_ancestor
    Page._update_descendant_url_paths = _update_descendant_url_paths_beneath_visible_ancestor
