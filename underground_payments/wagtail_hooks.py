from wagtail import hooks
from wagtail.snippets.views.snippets import SnippetViewSet


@hooks.register("construct_page_listing_buttons")
def add_payment_page_help(buttons, page, user, **kwargs):
    pass  # Placeholder for future admin customisations.
