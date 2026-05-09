from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from django.contrib.auth.models import Group

from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.admin.panels import FieldPanel
from wagtail.contrib.redirects.permissions import permission_policy as redirects_permission_policy
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import Engagement, Tag


@hooks.register("register_admin_menu_item")
def register_redirects_menu_item():
    class RedirectsMenuItem(MenuItem):
        def is_shown(self, request):
            return redirects_permission_policy.user_has_any_permission(
                request.user, ["add", "change", "delete"]
            )

    return RedirectsMenuItem(
        _("Redirects"),
        reverse("wagtailredirects:index"),
        name="redirects",
        icon_name="redirect",
        order=150,
    )


class TagViewSet(SnippetViewSet):
    model = Tag
    icon = "tag"
    menu_label = _("Tags")
    menu_order = 300
    list_display = ["name"]
    search_fields = ["name"]


register_snippet(TagViewSet)


class BuzzViewSet(SnippetViewSet):
    model = Engagement
    icon = "radio-empty"
    menu_label = _("Buzz")
    menu_order = 50
    add_to_admin_menu = True
    list_display = ["person", "action_type", "page_title", "created_at"]
    list_filter = ["action_type"]
    search_fields = [
        "person__email",
        "person__first_name",
        "person__last_name",
        "page_title",
    ]


register_snippet(BuzzViewSet)


class GroupViewSet(SnippetViewSet):
    model = Group
    icon = "group"
    menu_label = _("Groups")
    menu_order = 900
    list_display = ["name"]
    search_fields = ["name"]
    # Restrict to name only — Group.permissions is a M2M that Wagtail cannot
    # auto-widget-ify, which suppresses the "New" button in choosers.
    # Full permission management remains available under Settings > Groups.
    panels = [FieldPanel("name")]


register_snippet(GroupViewSet)
