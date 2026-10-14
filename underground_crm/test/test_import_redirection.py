"""
Tests for the Redirects that import_pages makes from legacy "Redirect" pages
(build_redirection).

A legacy redirect page is identified only by its slug (e.g. "blog"), and the
page it points at is named by the "parent_slug" attribute. Wagtail's
RedirectMiddleware matches a request against the normalized path ("/blog"),
so these tests request the old URL through the real middleware rather than
inspecting the saved field alone.
"""

from django.test import TestCase
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Page, Site

from underground_crm.management.commands.import_pages import build_redirection
from underground_crm.models import UndergroundBasicPage

LEGACY_REDIRECT_SLUG = "blog"
DESTINATION_SLUG = "news"
DESTINATION_TITLE = "News"


class BuildRedirectionTests(TestCase):
    def setUp(self) -> None:
        self.site = Site.objects.get(is_default_site=True)
        self.destination = UndergroundBasicPage(title=DESTINATION_TITLE, slug=DESTINATION_SLUG)
        Page.objects.get(pk=self.site.root_page_id).add_child(instance=self.destination)

    def build_and_save(self, slug: str) -> Redirect:
        redirection = build_redirection(
            document_soup=None,
            importable_html=None,
            attributes={"parent_slug": DESTINATION_SLUG},
            slug=slug,
            site=self.site,
        )
        # import_pages saves the redirect directly, without full_clean(),
        # so this test must do the same to reproduce what it stores.
        redirection.save()
        return redirection

    def test_old_path_is_stored_normalized(self) -> None:
        redirection = self.build_and_save(LEGACY_REDIRECT_SLUG)
        self.assertEqual(
            redirection.old_path,
            Redirect.normalise_path(LEGACY_REDIRECT_SLUG),
            "RedirectMiddleware only finds redirects whose old_path is in normalized form",
        )

    def test_legacy_url_redirects_to_destination(self) -> None:
        self.build_and_save(LEGACY_REDIRECT_SLUG)
        response = self.client.get(f"/{LEGACY_REDIRECT_SLUG}/", HTTP_HOST=self.site.hostname)
        self.assertRedirects(
            response,
            self.destination.url,
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_legacy_url_without_trailing_slash_reaches_destination(self) -> None:
        # CommonMiddleware's APPEND_SLASH answers "/blog" with a redirect to
        # "/blog/" before RedirectMiddleware sees a 404, so the chain has two hops.
        self.build_and_save(LEGACY_REDIRECT_SLUG)
        response = self.client.get(
            f"/{LEGACY_REDIRECT_SLUG}", HTTP_HOST=self.site.hostname, follow=True
        )
        self.assertEqual(
            [url for url, _status in response.redirect_chain][-1],
            self.destination.url,
            "Following every redirect from the legacy URL should end at the destination page",
        )
