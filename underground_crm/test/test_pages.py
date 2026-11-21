import re

import django.test
from django.urls import reverse
from wagtail.images.models import Image
from wagtail.images.tests.utils import get_test_image_file
from wagtail.models import Site as WagtailSite

from underground_crm.models import BasicPage


class OgTypeOverrideTest(django.test.TestCase):
    def test_constructing_with_og_type_override_does_not_raise(self):
        """
        Regression test: og_type used to be a read-only property with no
        backing field, so passing it as a constructor kwarg (as the page
        importer does) raised AttributeError: property 'og_type' has no setter.
        """
        page = BasicPage(title="Volunteer", slug="volunteer", og_type_override="article")
        self.assertEqual(page.og_type, "article")

    def test_og_type_falls_back_to_website_when_no_override_set(self):
        page = BasicPage(title="Volunteer", slug="volunteer", og_type_override=None)
        self.assertEqual(page.og_type, "website")


VOLUNTEER_PAGE_TITLE = "Volunteer"
VOLUNTEER_PAGE_SEO_TITLE = "Volunteer with us"
VOLUNTEER_PAGE_DESCRIPTION = "Help us bring honesty back into politics."


class OpenGraphMetadataTest(django.test.TestCase):
    """The <head> of a served page carries the metadata from its Promote tab,
    as defined by underground_crm/includes/open_graph.html."""

    def setUp(self) -> None:
        # Wagtail caches the site root paths, which page.url depends on, in the shared cache,
        # where a rolled-back test (or a development server) can leave them out of date.
        WagtailSite.clear_site_root_paths_cache()
        self.addCleanup(WagtailSite.clear_site_root_paths_cache)

    def serve(self, parent=None, **fields) -> str:
        if parent is None:
            parent = WagtailSite.objects.get(is_default_site=True).root_page
        page = BasicPage(title=VOLUNTEER_PAGE_TITLE, slug="volunteer", **fields)
        parent.add_child(instance=page)
        response = self.client.get(page.url)
        self.assertEqual(response.status_code, 200)
        self.page = page
        return response.content.decode()

    def test_description_title_type_and_url(self) -> None:
        html = self.serve(
            seo_title=VOLUNTEER_PAGE_SEO_TITLE, search_description=VOLUNTEER_PAGE_DESCRIPTION
        )
        self.assertInHTML(
            f'<meta property="og:description" content="{VOLUNTEER_PAGE_DESCRIPTION}">', html
        )
        self.assertInHTML(f'<meta name="description" content="{VOLUNTEER_PAGE_DESCRIPTION}">', html)
        self.assertInHTML(f'<meta property="og:title" content="{VOLUNTEER_PAGE_SEO_TITLE}">', html)
        self.assertInHTML('<meta property="og:type" content="website">', html)
        self.assertInHTML(f'<meta property="og:url" content="{self.page.full_url}">', html)

    def test_omits_the_description_when_none_was_written(self) -> None:
        html = self.serve()
        self.assertNotIn("og:description", html)
        self.assertNotIn('name="description"', html)

    def test_image_is_an_absolute_url(self) -> None:
        image = Image.objects.create(title="Volunteers", file=get_test_image_file())
        html = self.serve(search_image=image)
        og_image = re.search(r'<meta property="og:image" content="([^"]+)">', html)
        self.assertIsNotNone(og_image, "The search image should be offered as og:image.")
        self.assertRegex(
            og_image.group(1),
            r"^https?://",
            "Crawlers resolve og:image without reference to the page, so it must be absolute.",
        )

    def test_image_falls_back_to_that_of_the_site_root(self) -> None:
        image = Image.objects.create(title="Logo", file=get_test_image_file())
        site = WagtailSite.objects.get(is_default_site=True)
        # A site root's slug is shown as a breadcrumb (see BasicPage.save); this page only
        # becomes the root once it exists, so the flag is set here for its children's URLs.
        home = BasicPage(
            title="Home", slug="welcome", search_image=image, show_slug_as_breadcrumb=True
        )
        site.root_page.get_parent().add_child(instance=home)
        site.root_page = home
        site.save()
        html = self.serve(parent=home)
        self.assertEqual(self.page.og_image, image)
        self.assertIn('<meta property="og:image" content="', html)
        self.assertInHTML('<meta name="twitter:card" content="summary_large_image">', html)

    def test_own_image_takes_precedence_over_that_of_the_site_root(self) -> None:
        image = Image.objects.create(title="Volunteers", file=get_test_image_file())
        self.serve(search_image=image)
        self.assertEqual(self.page.og_image, image)

    def test_summary_card_without_an_image(self) -> None:
        html = self.serve()
        self.assertIsNone(self.page.og_image)
        self.assertNotIn("og:image", html)
        self.assertInHTML('<meta name="twitter:card" content="summary">', html)

    def test_canonical_link_and_site_name(self) -> None:
        html = self.serve()
        self.assertInHTML(f'<link rel="canonical" href="{self.page.full_url}">', html)
        self.assertInHTML(f'<meta property="og:site_name" content="{self.page.site_name}">', html)


class IncludeInSitemapTest(django.test.TestCase):
    """A page whose include_in_sitemap is unset is left out of sitemap.xml."""

    def setUp(self) -> None:
        WagtailSite.clear_site_root_paths_cache()
        self.addCleanup(WagtailSite.clear_site_root_paths_cache)
        self.root_page = WagtailSite.objects.get(is_default_site=True).root_page

    def add_page(self, slug: str, **fields) -> BasicPage:
        page = BasicPage(title=slug.replace("_", " ").capitalize(), slug=slug, **fields)
        self.root_page.add_child(instance=page)
        return page

    def test_only_included_pages_are_listed(self) -> None:
        volunteer = self.add_page("volunteer")
        donation_thanks = self.add_page("donation_thanks", include_in_sitemap=False)
        response = self.client.get(reverse("sitemap"))
        self.assertEqual(response.status_code, 200)
        sitemap = response.content.decode()
        self.assertIn(f"<loc>{volunteer.full_url}</loc>", sitemap)
        self.assertNotIn(donation_thanks.full_url, sitemap)
