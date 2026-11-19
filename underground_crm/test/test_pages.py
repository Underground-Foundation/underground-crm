import re

import django.test
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

    def serve(self, **fields) -> str:
        root_page = WagtailSite.objects.get(is_default_site=True).root_page
        page = BasicPage(title=VOLUNTEER_PAGE_TITLE, slug="volunteer", **fields)
        root_page.add_child(instance=page)
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
