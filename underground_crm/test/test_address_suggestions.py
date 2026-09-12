import json
import logging
import urllib.error
import urllib.request
from unittest import mock, skipUnless

import django.test
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse
from wagtail.models import Page

from underground_crm import addressr
from underground_crm.forms.form_submission import FormSubmissionForm
from underground_crm.models.pages import FormPage

logger = logging.getLogger(__name__)

# A real Sydney address that exists in the G-NAF dataset, matching the example
# response documented on addressr.search().
KNOWN_ADDRESS_QUERY = "1 Cook Rd, Lindfield"


def _addressr_is_available() -> bool:
    """Whether the local Addressr container is up and answering requests."""
    try:
        urllib.request.urlopen(settings.ADDRESSR_BASE_URL + "/addresses?q=test", timeout=2)
        return True
    except (urllib.error.URLError, ValueError):
        return False


class AddressSuggestionViewTest(django.test.SimpleTestCase):
    def _get_suggestions(self, query: str) -> list:
        response = self.client.get(reverse("underground_crm:address_suggestions"), {"q": query})
        self.assertEqual(
            response.status_code,
            200,
            "The suggestion endpoint is public and must answer every GET request, "
            "even when it has nothing to suggest.",
        )
        return json.loads(response.content)["suggestions"]

    def test_short_query_returns_no_suggestions(self):
        # One character below the threshold at which the view starts
        # consulting Addressr.
        short_query = KNOWN_ADDRESS_QUERY[: addressr.MINIMUM_QUERY_LENGTH - 1]
        self.assertEqual(
            self._get_suggestions(short_query),
            [],
            "Queries below MINIMUM_QUERY_LENGTH must be answered with an empty "
            "list without consulting Addressr, because very short queries only "
            "produce noise.",
        )

    def test_suggestions_carry_address_text_and_gnaf_id(self):
        # Addressr is an external service, so this test substitutes a canned
        # response in the documented shape (see addressr.search()); the
        # companion live test below exercises the real container when it is up.
        canned_response = [
            {
                "sla": "1 COOK RD, LINDFIELD NSW 2070",
                "score": 264.7019,
                "pid": "GANSW705239062",
            }
        ]
        with mock.patch.object(addressr, "search", return_value=canned_response):
            suggestions = self._get_suggestions(KNOWN_ADDRESS_QUERY)
        self.assertEqual(
            suggestions,
            [{"sla": "1 COOK RD, LINDFIELD NSW 2070", "gnaf_id": "GANSW705239062"}],
            "Each suggestion must pair the single-line address the datalist "
            "presents with the G-NAF ID of that exact address, which the "
            "autocomplete script stores in the form's hidden companion field "
            "so the submission resolves to the address the visitor picked.",
        )

    def test_suggestions_accept_the_older_self_link_shape(self):
        # Addressr releases before 3.3 carried the PID only as a HAL-style self
        # link. Both shapes stay supported because the deployed query service
        # and a developer's local container are pinned separately (see
        # ADDRESSR_VERSION in fusion-underground's compose files against the
        # image in this repository's docker-compose.yml), so they can differ.
        canned_response = [
            {
                "sla": "1 COOK RD, LINDFIELD NSW 2070",
                "score": 264.7019,
                "links": {"self": {"href": "/addresses/GANSW705239062"}},
            }
        ]
        with mock.patch.object(addressr, "search", return_value=canned_response):
            suggestions = self._get_suggestions(KNOWN_ADDRESS_QUERY)
        self.assertEqual(
            suggestions,
            [{"sla": "1 COOK RD, LINDFIELD NSW 2070", "gnaf_id": "GANSW705239062"}],
            "An entry carrying no 'pid' must still yield the G-NAF ID from its "
            "self link, so an older Addressr container keeps working.",
        )

    def test_suggestion_without_any_identifier_yields_none(self):
        canned_response = [{"sla": "1 COOK RD, LINDFIELD NSW 2070", "score": 264.7019}]
        with mock.patch.object(addressr, "search", return_value=canned_response):
            suggestions = self._get_suggestions(KNOWN_ADDRESS_QUERY)
        self.assertEqual(
            suggestions,
            [{"sla": "1 COOK RD, LINDFIELD NSW 2070", "gnaf_id": None}],
            "A suggestion Addressr gives no identifier for must still be "
            "offered as text, with a null ID rather than a fabricated one.",
        )

    def test_endpoint_rejects_post_requests(self):
        response = self.client.post(
            reverse("underground_crm:address_suggestions"), {"q": KNOWN_ADDRESS_QUERY}
        )
        self.assertEqual(
            response.status_code,
            405,
            "The suggestion endpoint is read-only, so it must only accept GET.",
        )

    @skipUnless(_addressr_is_available(), "The local Addressr container is not running.")
    def test_live_addressr_returns_suggestions(self):
        suggestions = self._get_suggestions(KNOWN_ADDRESS_QUERY)
        self.assertTrue(
            all(
                isinstance(suggestion.get("sla"), str)
                and suggestion["sla"]
                and isinstance(suggestion.get("gnaf_id"), str)
                and suggestion["gnaf_id"]
                for suggestion in suggestions
            ),
            "Every suggestion must carry a non-empty single-line address and "
            "the G-NAF ID identifying that exact address, but the live "
            f"container returned: {suggestions!r}",
        )


class AddressBlockRenderingTest(django.test.TestCase):
    """The visitor-facing form must wire the address input up to the
    autocomplete script: the widget's data attributes tell the script where to
    fetch suggestions from and when to start, and the form's media pulls the
    script itself in (form_page.html renders {{ form.media }})."""

    def setUp(self):
        root = Page.objects.get(id=1)
        self.page = FormPage(title="Join the doorknocking team", slug="doorknocking")
        self.page.body = [("address", "")]
        root.add_child(instance=self.page)

        request = RequestFactory().get(self.page.url)
        request.user = AnonymousUser()
        self.form = FormSubmissionForm(request=request, page=self.page)

    def test_address_input_carries_autocomplete_attributes(self):
        rendered = self.form.as_p()
        self.assertIn(
            "data-address-autocomplete",
            rendered,
            "The autocomplete script finds address inputs by this attribute.",
        )
        self.assertIn(
            f'data-suggestion-url="{reverse("underground_crm:address_suggestions")}"',
            rendered,
            "The script reads the endpoint to query from this attribute rather "
            "than hardcoding a URL.",
        )
        self.assertIn(
            f'data-minimum-length="{addressr.MINIMUM_QUERY_LENGTH}"',
            rendered,
            "The script waits for this many characters before querying, "
            "mirroring the view's own threshold.",
        )

    def test_form_media_includes_autocomplete_script(self):
        self.assertIn(
            "underground_crm/js/address_autocomplete.js",
            str(self.form.media),
            "form_page.html renders {{ form.media }}, which is how the "
            "autocomplete script reaches the visitor's browser.",
        )
