from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from django.contrib.auth.models import Group

import wagtail.admin.rich_text.editors.draftail.features as draftail_features
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.admin.panels import FieldPanel
from wagtail.admin.rich_text.converters.html_to_contentstate import InlineStyleElementHandler
from wagtail.contrib.redirects.permissions import permission_policy as redirects_permission_policy
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import Engagement, Tag


def _register_inline_style_feature(features, feature_name, type_, tag, label, description):
    """
    Register `tag` as an opt-in Draftail inline style named `feature_name`.

    The feature is not added to ``features.default_features``, so it stays
    opt-in: a RichTextField/RichTextBlock only gets the toolbar button and
    the `tag` <-> `type_` conversion when it names `feature_name` in its own
    features list (see BASIC_PAGE_BLOCKS in underground_crm/models/pages.py).
    """
    features.register_editor_plugin(
        "draftail",
        feature_name,
        draftail_features.InlineStyleFeature(
            {
                "type": type_,
                "label": label,
                "description": description,
            }
        ),
    )
    features.register_converter_rule(
        "contentstate",
        feature_name,
        {
            "from_database_format": {tag: InlineStyleElementHandler(type_)},
            "to_database_format": {"style_map": {type_: tag}},
        },
    )


@hooks.register("register_rich_text_features")
def register_underline_feature(features):
    """
    Register ``<u>`` as the Draftail feature "underline". Wagtail does not ship
    it — only "bold" and "italic" are built in.

    underground_crm/legacy_html.py's decomposition relies on it when it
    re-expresses a legacy ``text-decoration: underline`` as <u>.
    """
    _register_inline_style_feature(features, "underline", "UNDERLINE", "u", "U", _("Underline"))


@hooks.register("register_rich_text_features")
def register_subscript_feature(features):
    """
    Register ``<sub>`` as the Draftail feature "subscript", for chemical
    formulas such as CO<sub>2</sub>. Draftail styles the SUBSCRIPT type itself.
    """
    _register_inline_style_feature(features, "subscript", "SUBSCRIPT", "sub", "x₂", _("Subscript"))


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
    # Tags are now django-taggit-backed (see underground_crm.models.person.Tag);
    # wherever a page has a "tags to apply" field, editors get a native
    # autocomplete/create-in-place input for free (FieldPanel on a TagBase
    # field), so this listing exists only for browsing/renaming/deleting tags
    # directly. slug is excluded since it's auto-generated from name.
    model = Tag
    icon = "tag"
    menu_label = _("Tags")
    menu_order = 300
    list_display = ["name", "is_protected"]
    search_fields = ["name"]
    panels = [FieldPanel("name"), FieldPanel("is_protected")]


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
