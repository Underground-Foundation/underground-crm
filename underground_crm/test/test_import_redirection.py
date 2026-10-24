"""
Tests for the Redirects that import_pages makes from legacy "Redirect" pages
(build_redirection).

A legacy redirection is identified only by its slug (e.g. "blog"). Fetching it
follows the redirect, so its saved HTML is that of its destination, which names
its own URL in a canonical link. The redirection's "parent_slug" is only its place
in the legacy page tree: unlisted redirections in particular sit beneath a
"redirects" folder page, which is not where they lead.

Wagtail's RedirectMiddleware matches a request against the normalized path
("/blog"), so these tests request the old URL through the real middleware rather
than inspecting the saved field alone.
"""

from typing import Optional

from bs4 import BeautifulSoup
from django.test import TestCase
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Page, Site

from underground_crm.management.commands.import_pages import build_redirection
from underground_crm.models import UndergroundBasicPage

LEGACY_DOMAIN = "fusionparty.org.au"
LEGACY_REDIRECT_SLUG = "blog"
DESTINATION_SLUG = "news"
DESTINATION_TITLE = "News"
# An unlisted redirection, filed beneath the legacy site's folder of redirections.
UNLISTED_REDIRECT_SLUG = "aston"
REDIRECTIONS_FOLDER_SLUG = "redirects"
CANDIDATE_SLUG = "owen_miller_wills"
CANDIDATE_TITLE = "Owen Miller for Wills"
EXTERNAL_REDIRECT_SLUG = "discord"
EXTERNAL_DESTINATION = "https://discord.com/invite/fusionparty"


def destination_html(canonical_url: Optional[str], title: str) -> BeautifulSoup:
    """The HTML saved for a legacy redirection: that of the page it leads to."""
    canonical = f'<link rel="canonical" href="{canonical_url}" />' if canonical_url else ""
    return BeautifulSoup(
        f"<html><head><title>{title}</title>{canonical}</head><body></body></html>",
        "html.parser",
    )


class BuildRedirectionTests(TestCase):
    def setUp(self) -> None:
        self.site = Site.objects.get(is_default_site=True)
        root = Page.objects.get(pk=self.site.root_page_id)
        self.destination = UndergroundBasicPage(title=DESTINATION_TITLE, slug=DESTINATION_SLUG)
        root.add_child(instance=self.destination)
        self.candidate = UndergroundBasicPage(title=CANDIDATE_TITLE, slug=CANDIDATE_SLUG)
        root.add_child(instance=self.candidate)

    def build(
        self, slug: str, canonical_url: Optional[str], parent_slug: str, title: str
    ) -> Optional[Redirect]:
        return build_redirection(
            document_soup=destination_html(canonical_url, title),
            importable_html=None,
            attributes={"parent_slug": parent_slug, "status": "unlisted"},
            slug=slug,
            site=self.site,
            legacy_domain=LEGACY_DOMAIN,
        )

    def build_and_save(self, slug: str) -> Redirect:
        redirection = self.build(
            slug,
            canonical_url=f"https://www.{LEGACY_DOMAIN}/{DESTINATION_SLUG}",
            parent_slug=DESTINATION_SLUG,
            title=DESTINATION_TITLE,
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

    def test_unlisted_redirection_in_folder_leads_to_its_canonical_page(self) -> None:
        redirection = self.build(
            UNLISTED_REDIRECT_SLUG,
            canonical_url=f"https://www.{LEGACY_DOMAIN}/{CANDIDATE_SLUG}",
            parent_slug=REDIRECTIONS_FOLDER_SLUG,
            title=CANDIDATE_TITLE,
        )
        self.assertIsNotNone(redirection, "An unlisted redirection should still be migrated")
        self.assertEqual(
            redirection.redirect_page_id,
            self.candidate.pk,
            "The destination is the page that the saved HTML is canonically for, "
            "not the folder that the redirection was filed under",
        )

    def test_redirection_off_the_legacy_site_is_kept_as_a_link(self) -> None:
        redirection = self.build(
            EXTERNAL_REDIRECT_SLUG,
            canonical_url=EXTERNAL_DESTINATION,
            parent_slug=REDIRECTIONS_FOLDER_SLUG,
            title="Fusion Party Australia",
        )
        redirection.save()
        response = self.client.get(f"/{EXTERNAL_REDIRECT_SLUG}/", HTTP_HOST=self.site.hostname)
        self.assertRedirects(
            response, EXTERNAL_DESTINATION, status_code=301, fetch_redirect_response=False
        )

    def test_redirection_without_a_known_destination_is_not_built(self) -> None:
        redirection = self.build(
            UNLISTED_REDIRECT_SLUG,
            canonical_url=None,
            parent_slug=REDIRECTIONS_FOLDER_SLUG,
            title=CANDIDATE_TITLE,
        )
        self.assertIsNone(
            redirection,
            "Without a canonical link or og:url, nothing names where the redirection led, "
            "and its parent in the legacy tree is no substitute",
        )
