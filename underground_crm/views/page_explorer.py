"""
Wagtail's page explorer (/cms/pages/<id>/), with a "Slug" column beside the title.

Wagtail offers no hook for adding columns to the explorer, so this module subclasses its
view and `explorer_urlpatterns` routes the explorer's URLs to the subclass. Those
patterns must come before Wagtail's admin URLs (see site_urls.py); they reuse Wagtail's
URL names, so everything that reverses them keeps working unchanged.
"""

from django.urls import URLPattern, path
from django.utils.functional import classproperty
from django.utils.translation import gettext_lazy as _
from wagtail.admin.ui.tables import BaseColumn, Column
from wagtail.admin.views.pages.listing import ExplorableIndexView

SLUG_COLUMN_NAME = "slug"


class PageExplorerView(ExplorableIndexView):
    @classproperty
    def columns(cls) -> list[BaseColumn]:  # pylint: disable=no-self-argument
        columns = list(super().columns)
        title_position = next(
            position for position, column in enumerate(columns) if column.name == "title"
        )
        columns.insert(
            title_position + 1,
            Column(SLUG_COLUMN_NAME, label=_("Slug"), sort_key="slug", width="15%"),
        )
        return columns

    def get_valid_orderings(self) -> list[str]:
        valid_orderings = super().get_valid_orderings()
        if self.is_searching:
            # Page.search_fields doesn't index the slug as a FilterField, so a search
            # backend can't order by it. Wagtail drops "content_type" for the same reason.
            valid_orderings = [
                ordering for ordering in valid_orderings if ordering.lstrip("-") != "slug"
            ]
        return valid_orderings


explorer_urlpatterns: list[URLPattern] = [
    path("pages/", PageExplorerView.as_view(), name="wagtailadmin_explore_root"),
    path(
        "pages/<int:parent_page_id>/",
        PageExplorerView.as_view(),
        name="wagtailadmin_explore",
    ),
    path(
        "pages/<int:parent_page_id>/results/",
        PageExplorerView.as_view(results_only=True),
        name="wagtailadmin_explore_results",
    ),
]
