"""
The Cache-Control header of pages that serve a visitor form. FormServingPage and
PaymentPage give them a calculated cache time of 0 (see their _calculated_cache_time for
why); these tests fetch pages through the full request cycle so that they check the header
a shared cache actually receives.

Each page is added under the default Site's root so that it has a URL: without one,
PageWithMetadata._calculated_cache_time returns 0 for every page type, and the assertions
would pass whatever FormServingPage did.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import django.test
from wagtail.models import Site

from underground_crm.models.pages import EventPage, FormPage, PageWithMetadata
from underground_payments.models import PaymentPage

EVENT_TIMEZONE = ZoneInfo("Australia/Melbourne")
EVENT_DURATION = timedelta(hours=3)
# An editor's explicit choice, which is arbitrary but distinct from the library's
# DEFAULT_CACHE_TTL so that the assertion can only pass by reading it.
EDITOR_CACHE_TTL = PageWithMetadata.DEFAULT_CACHE_TTL + 60


class FormPageCacheControlTest(django.test.TestCase):
    def setUp(self) -> None:
        self.site_root = Site.objects.get(is_default_site=True).root_page

    def assert_not_stored(self, page) -> None:
        response = self.client.get(page.url)
        self.assertEqual(page.cache_time, 0)
        self.assertIn(
            "no-store",
            response["Cache-Control"],
            "The form's CSRF token is valid only alongside the cookie of the visitor that "
            "it was issued to, so a shared cache must never keep a copy of the page.",
        )

    def test_form_page_is_not_cached_by_default(self) -> None:
        page = FormPage(title="Volunteer with us", slug="volunteer-with-us")
        self.site_root.add_child(instance=page)
        self.assert_not_stored(page)

    def test_event_page_is_not_cached_by_default(self) -> None:
        event_start = datetime(2026, 12, 5, 18, 30, tzinfo=EVENT_TIMEZONE)
        page = EventPage(
            title="End of year fundraising dinner",
            slug="end-of-year-fundraising-dinner",
            start_time=event_start,
            end_time=event_start + EVENT_DURATION,
        )
        self.site_root.add_child(instance=page)
        self.assert_not_stored(page)

    def test_payment_page_is_not_cached_by_default(self) -> None:
        page = PaymentPage(title="Donate to Fusion", slug="donate")
        self.site_root.add_child(instance=page)
        self.assert_not_stored(page)

    def test_editor_can_override_the_default_cache_time(self) -> None:
        page = FormPage(
            title="Volunteer with us",
            slug="volunteer-with-us",
            cache_ttl_override=EDITOR_CACHE_TTL,
        )
        self.site_root.add_child(instance=page)

        response = self.client.get(page.url)

        self.assertIn(f"max-age={EDITOR_CACHE_TTL}", response["Cache-Control"])
