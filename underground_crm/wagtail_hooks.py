from django.urls import reverse

from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.contrib.redirects.permissions import permission_policy as redirects_permission_policy
from wagtail.images.formats import Format, register_image_format
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
        "Redirects",
        reverse("wagtailredirects:index"),
        name="redirects",
        icon_name="redirect",
        order=150,
    )


# Register a half-width image format for use in RichTextBlock image insertions.
# The CSS class "richtext-image w-50" can be styled in the site's stylesheet.
register_image_format(Format("w-50", "Half width", "richtext-image w-50", "width-800"))


class TagViewSet(SnippetViewSet):
    model = Tag
    icon = "tag"
    menu_label = "Tags"
    menu_order = 300
    list_display = ["name"]
    search_fields = ["name"]


register_snippet(TagViewSet)


class BuzzViewSet(SnippetViewSet):
    model = Engagement
    icon = "radio-empty"
    menu_label = "Buzz"
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
