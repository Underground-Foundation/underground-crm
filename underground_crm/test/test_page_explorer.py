"""
Tests for the Wagtail page explorer's "Slug" column (underground_crm/views/page_explorer.py).

The explorer is requested through the test client at the URL an editor visits, so these
tests check that site_urls.py really routes Wagtail's explorer to our view, not merely
that the view class has the column.
"""

from django.test import TestCase
from django.urls import reverse
from wagtail.models import Page, Site

from underground_crm.models import BasicPage, Person
from underground_crm.views.page_explorer import SLUG_COLUMN_NAME, PageExplorerView


def add_page(parent: Page, *, title: str, slug: str) -> BasicPage:
    page = BasicPage(title=title, slug=slug)
    parent.add_child(instance=page)
    return page


class PageExplorerSlugColumnTest(TestCase):
    def setUp(self) -> None:
        editor = Person.objects.create_user(
            email="editor@example.com",
            password="correct horse battery staple",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(editor)
        self.policy = add_page(
            Site.objects.get(is_default_site=True).root_page, title="Policy", slug="policy"
        )

    def test_slug_column_follows_the_title_column(self) -> None:
        # Django's classproperty returns a list here, but pylint can't see through it.
        column_names = [
            column.name for column in PageExplorerView.columns  # pylint: disable=not-an-iterable
        ]

        self.assertEqual(
            column_names[column_names.index("title") + 1],
            SLUG_COLUMN_NAME,
            "The slug identifies a page alongside its title, so it should sit beside it.",
        )

    def test_explorer_lists_each_child_pages_slug(self) -> None:
        climate_slug = "climate_rescue"
        housing_slug = "affordable_housing"
        add_page(self.policy, title="Climate rescue", slug=climate_slug)
        add_page(self.policy, title="Affordable housing", slug=housing_slug)

        response = self.client.get(reverse("wagtailadmin_explore", args=[self.policy.pk]))

        self.assertIsInstance(
            response.context["view"],
            PageExplorerView,
            "site_urls.py must route Wagtail's explorer URL to our view, ahead of Wagtail's own.",
        )
        self.assertContains(response, climate_slug)
        self.assertContains(response, housing_slug)

    def test_explorer_can_sort_by_slug(self) -> None:
        # The titles sort in the opposite order to their slugs, so sorting by slug is
        # distinguishable from the explorer's other orderings.
        first_slug = "affordable_housing"
        second_slug = "climate_rescue"
        add_page(self.policy, title="Zero-emission housing", slug=first_slug)
        add_page(self.policy, title="Climate rescue", slug=second_slug)

        response = self.client.get(
            reverse("wagtailadmin_explore", args=[self.policy.pk]), {"ordering": "slug"}
        )

        listed_slugs = [page.slug for page in response.context["object_list"]]
        self.assertEqual(listed_slugs, [first_slug, second_slug])

    def test_searching_ignores_a_request_to_sort_by_slug(self) -> None:
        add_page(self.policy, title="Climate rescue", slug="climate_rescue")

        response = self.client.get(
            reverse("wagtailadmin_explore", args=[self.policy.pk]),
            {"ordering": "slug", "q": "climate"},
        )

        self.assertEqual(
            response.status_code,
            200,
            "The search backend can't order by slug, so the explorer must not try to.",
        )
        self.assertNotEqual(response.context["view"].ordering, "slug")
