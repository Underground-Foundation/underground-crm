import django.test

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
