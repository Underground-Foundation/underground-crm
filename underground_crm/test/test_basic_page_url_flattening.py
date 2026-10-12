"""
Tests for BasicPage.show_slug_as_breadcrumb: when disabled (the default), the
pages beneath a BasicPage are addressed directly beneath its own parent,
omitting its slug from their URL — the way the legacy CMS lays out its pages.
See underground_crm/feed_routing.py for the routing and URL generation that
this relies on; test_feed_page_url_flattening.py covers feeds specifically.

Routing is checked with Page.find_for_request rather than a full
self.client.get(), so these tests exercise the same tree-walking machinery a
real request goes through without depending on a downstream theme's own
page templates being complete.
"""

from django.test import RequestFactory, TestCase
from wagtail.models import Page, Site

from underground_crm.models import BasicPage, FeedPage, UndergroundBasicPage


def home_page() -> Page:
    return Site.objects.get(is_default_site=True).root_page


def add_page(parent: Page, *, title: str, slug: str, **kwargs) -> BasicPage:
    page = BasicPage(title=title, slug=slug, **kwargs)
    parent.add_child(instance=page)
    return page


def resolve_page(path: str) -> Page | None:
    """The live page a request for `path` would be routed to, found through
    the same Page.route() tree walk a real request uses (see
    wagtail.views.serve)."""
    request = RequestFactory().get(path)
    resolved = Page.find_for_request(request, path)
    return resolved.specific if resolved is not None else None


class HiddenSlugByDefaultTest(TestCase):
    def setUp(self) -> None:
        self.policy = add_page(home_page(), title="Policy", slug="policy")

    def test_show_slug_as_breadcrumb_defaults_to_false(self) -> None:
        self.assertFalse(
            self.policy.show_slug_as_breadcrumb,
            "Existing pages migrate to False, so new ones must agree with them.",
        )

    def test_child_url_omits_the_parents_slug(self) -> None:
        climate = add_page(self.policy, title="Climate rescue", slug="climate_rescue")

        self.assertEqual(climate.url, "/climate_rescue/")

    def test_child_is_routed_to_at_the_flattened_url(self) -> None:
        climate = add_page(self.policy, title="Climate rescue", slug="climate_rescue")

        self.assertEqual(resolve_page(climate.url), climate)

    def test_nested_url_still_resolves_the_same_page(self) -> None:
        climate = add_page(self.policy, title="Climate rescue", slug="climate_rescue")

        self.assertEqual(
            resolve_page("/policy/climate_rescue/"),
            climate,
            "Routing matches slugs level by level, so the real position in "
            "the tree keeps working alongside the flattened URL.",
        )

    def test_parent_itself_keeps_its_own_url(self) -> None:
        self.assertEqual(self.policy.url, "/policy/")
        self.assertEqual(resolve_page("/policy/"), self.policy)

    def test_unknown_path_still_does_not_resolve(self) -> None:
        add_page(self.policy, title="Climate rescue", slug="climate_rescue")

        self.assertIsNone(resolve_page("/tax_reform/"))

    def test_slug_hidden_through_two_levels_of_hidden_parents(self) -> None:
        energy = add_page(self.policy, title="Energy", slug="energy")
        solar = add_page(energy, title="Rooftop solar", slug="rooftop_solar")

        self.assertEqual(solar.url, "/rooftop_solar/")
        self.assertEqual(resolve_page(solar.url), solar)

    def test_visible_page_beneath_a_hidden_one_nests_under_it(self) -> None:
        energy = add_page(self.policy, title="Energy", slug="energy", show_slug_as_breadcrumb=True)
        solar = add_page(energy, title="Rooftop solar", slug="rooftop_solar")

        self.assertEqual(
            solar.url,
            "/energy/rooftop_solar/",
            "Energy's slug is shown, and Policy above it is hidden only from "
            "Energy's own children, not from Energy's position.",
        )
        self.assertEqual(resolve_page(solar.url), solar)


class ShowSlugAsBreadcrumbEnabledTest(TestCase):
    def setUp(self) -> None:
        self.policy = add_page(
            home_page(), title="Policy", slug="policy", show_slug_as_breadcrumb=True
        )

    def test_child_url_is_nested_under_the_parent(self) -> None:
        climate = add_page(self.policy, title="Climate rescue", slug="climate_rescue")

        self.assertEqual(climate.url, "/policy/climate_rescue/")
        self.assertEqual(resolve_page(climate.url), climate)

    def test_flattened_style_url_does_not_resolve(self) -> None:
        add_page(self.policy, title="Climate rescue", slug="climate_rescue")

        self.assertIsNone(resolve_page("/climate_rescue/"))


class TogglingShowSlugAsBreadcrumbTest(TestCase):
    """Changing the flag on an existing page must realign its existing
    children's URLs, not just apply to children created afterwards."""

    def setUp(self) -> None:
        self.policy = add_page(home_page(), title="Policy", slug="policy")
        self.climate = add_page(self.policy, title="Climate rescue", slug="climate_rescue")
        self.carbon = add_page(self.climate, title="Carbon price", slug="carbon_price")

    def test_enabling_the_flag_nests_existing_children(self) -> None:
        self.policy.show_slug_as_breadcrumb = True
        self.policy.save()

        self.climate.refresh_from_db()
        self.assertEqual(self.climate.url, "/policy/climate_rescue/")
        self.assertEqual(resolve_page(self.climate.url), self.climate)

    def test_enabling_the_flag_moves_grandchildren_along_with_their_parent(self) -> None:
        self.policy.show_slug_as_breadcrumb = True
        self.policy.save()

        self.carbon.refresh_from_db()
        self.assertEqual(
            self.carbon.url,
            "/policy/carbon_price/",
            "Climate rescue still hides its slug from its own children, so "
            "Carbon price sits beside it; but Policy now shows above Climate "
            "rescue, so both of them moved beneath /policy/.",
        )

    def test_disabling_the_flag_flattens_existing_children_again(self) -> None:
        self.policy.show_slug_as_breadcrumb = True
        self.policy.save()
        self.policy.show_slug_as_breadcrumb = False
        self.policy.save()

        self.climate.refresh_from_db()
        self.assertEqual(self.climate.url, "/climate_rescue/")

    def test_saving_without_changing_the_flag_leaves_urls_untouched(self) -> None:
        original_url_path = self.climate.url_path

        self.policy.title = "Policy platform"
        self.policy.save()

        self.climate.refresh_from_db()
        self.assertEqual(self.climate.url_path, original_url_path)


class RenamingAndMovingHiddenPageTest(TestCase):
    def setUp(self) -> None:
        self.policy = add_page(home_page(), title="Policy", slug="policy")
        self.climate = add_page(self.policy, title="Climate rescue", slug="climate_rescue")

    def test_renaming_a_hidden_page_leaves_its_children_untouched(self) -> None:
        self.policy.slug = "platform"
        self.policy.save()

        self.climate.refresh_from_db()
        self.assertEqual(self.climate.url, "/climate_rescue/")

    def test_renaming_a_shown_page_renames_its_children_paths(self) -> None:
        self.policy.show_slug_as_breadcrumb = True
        self.policy.save()
        self.policy.slug = "platform"
        self.policy.save()

        self.climate.refresh_from_db()
        self.assertEqual(self.climate.url, "/platform/climate_rescue/")

    def test_moving_a_hidden_page_moves_its_children_with_it(self) -> None:
        about = add_page(home_page(), title="About", slug="about", show_slug_as_breadcrumb=True)

        self.policy.move(about, pos="last-child")

        self.climate.refresh_from_db()
        self.assertEqual(
            self.climate.url,
            "/about/climate_rescue/",
            "Policy's slug stays hidden, so Climate rescue sits where Policy "
            "was placed: beneath About.",
        )
        self.assertEqual(resolve_page(self.climate.url), self.climate)


class SiteRootTest(TestCase):
    """A site root's slug is never in a URL, so it cannot hide its slug
    from its children without taking them out of the site altogether."""

    def setUp(self) -> None:
        self.home = add_page(
            Page.get_first_root_node(),
            title="Fusion Party",
            slug="fusion_party",
            show_slug_as_breadcrumb=False,
        )
        site = Site.objects.get(is_default_site=True)
        site.root_page = self.home
        site.save()

    def test_saving_a_site_root_forces_its_slug_to_be_a_breadcrumb(self) -> None:
        self.home.save()

        self.home.refresh_from_db()
        self.assertTrue(self.home.show_slug_as_breadcrumb)

    def test_children_of_a_site_root_stay_within_the_site(self) -> None:
        self.home.save()
        policy = add_page(self.home, title="Policy", slug="policy")

        self.assertEqual(policy.url, "/policy/")
        self.assertEqual(resolve_page(policy.url), policy)


class EditorTabTest(TestCase):
    """Editors reach the flag through the Visibility tab of the page editor."""

    def visibility_field_names(self, page_class: type[Page]) -> list[str]:
        tabs = page_class.edit_handler.children
        visibility_tab = next(tab for tab in tabs if str(tab.heading) == "Visibility")
        return [
            panel.field_name for panel in visibility_tab.children if hasattr(panel, "field_name")
        ]

    def test_basic_page_offers_the_flag_in_the_visibility_tab(self) -> None:
        self.assertIn("show_slug_as_breadcrumb", self.visibility_field_names(BasicPage))

    def test_underground_basic_page_offers_the_flag_in_the_visibility_tab(self) -> None:
        self.assertIn("show_slug_as_breadcrumb", self.visibility_field_names(UndergroundBasicPage))

    def test_feed_page_offers_the_flag_in_the_visibility_tab(self) -> None:
        self.assertIn("show_slug_as_breadcrumb", self.visibility_field_names(FeedPage))
