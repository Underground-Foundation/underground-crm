from wagtail.images.formats import Format, register_image_format
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import Tag

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
