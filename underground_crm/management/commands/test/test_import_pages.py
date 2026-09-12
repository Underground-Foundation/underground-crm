import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import django.test
from django.core.management.base import CommandError
from wagtail.models import Page, Site

from underground_crm.contactability import get_validated_email_address
from underground_crm.management.commands.import_pages import (
    PAGE_BUILDING_MAP,
    Command,
    build_form_page,
    build_registration_page,
    extract_donation_frequency,
    extract_event_population,
    extract_event_time,
    extract_event_venue,
    extract_form_inputs,
    extract_host_attributes,
    extract_importable_html,
    extract_page_size,
    get_event_detail_pairs,
    get_host_by_email_address,
    is_site_root,
    parse_event_datetime,
)
from underground_crm.models import UndergroundBasicPage
from underground_crm.models.pages import FormPage, RegistrationPage


class TestParseEventDatetime(unittest.TestCase):

    def test_multi_state_label_uses_first_state(self):
        """NSW/ACT/VIC/TAS label resolves to Australia/Sydney via NSW."""
        start, end = parse_event_datetime(
            "        May 16, 2026 at 18:00 - 11pm (NSW/ACT/VIC/TAS timezone)"
        )
        nsw = ZoneInfo("Australia/Sydney")
        self.assertEqual(start, datetime(2026, 5, 16, 18, 0, tzinfo=nsw))
        self.assertEqual(end, datetime(2026, 5, 16, 23, 0, tzinfo=nsw))

    def test_single_state_vic(self):
        start, end = parse_event_datetime("June 1, 2026 at 09:00 - 5pm (VIC timezone)")
        vic = ZoneInfo("Australia/Melbourne")
        self.assertEqual(start, datetime(2026, 6, 1, 9, 0, tzinfo=vic))
        self.assertEqual(end, datetime(2026, 6, 1, 17, 0, tzinfo=vic))

    def test_qld_timezone(self):
        start, end = parse_event_datetime("July 4, 2026 at 14:00 - 6pm (QLD timezone)")
        qld = ZoneInfo("Australia/Brisbane")
        self.assertEqual(start, datetime(2026, 7, 4, 14, 0, tzinfo=qld))
        self.assertEqual(end, datetime(2026, 7, 4, 18, 0, tzinfo=qld))

    def test_wa_timezone(self):
        start, end = parse_event_datetime("March 15, 2026 at 10:00 - 2pm (WA timezone)")
        wa = ZoneInfo("Australia/Perth")
        self.assertEqual(start, datetime(2026, 3, 15, 10, 0, tzinfo=wa))
        self.assertEqual(end, datetime(2026, 3, 15, 14, 0, tzinfo=wa))

    def test_midnight_crossing_rolls_end_to_next_day(self):
        """When the end time is earlier in the day than the start time, one day is added."""
        start, end = parse_event_datetime("May 16, 2026 at 22:00 - 1am (NSW/ACT/VIC/TAS timezone)")
        nsw = ZoneInfo("Australia/Sydney")
        self.assertEqual(start, datetime(2026, 5, 16, 22, 0, tzinfo=nsw))
        self.assertEqual(end, datetime(2026, 5, 17, 1, 0, tzinfo=nsw))

    def test_unknown_state_falls_back_to_settings_timezone(self):
        with patch("underground_crm.management.commands.import_pages.settings") as mock_settings:
            mock_settings.TIME_ZONE = "UTC"
            start, _ = parse_event_datetime("January 1, 2026 at 10:00 - 2pm (ZZZ timezone)")
        self.assertEqual(start.utcoffset().total_seconds(), 0)

    def test_leading_whitespace_is_stripped(self):
        clean = parse_event_datetime("May 16, 2026 at 18:00 - 11pm (VIC timezone)")
        padded = parse_event_datetime("    May 16, 2026 at 18:00 - 11pm (VIC timezone)")
        self.assertEqual(clean, padded)


class TestEventDetailExtraction(unittest.TestCase):
    event_html_file = Path(__file__).parent / "event_sample.html"

    @classmethod
    def setUpClass(cls):
        cls.event_soup, _ = extract_importable_html(cls.event_html_file, importable_dir=None)
        cls.assertTrue(cls, cls.event_soup)

    def test_get_event_detail_pairs(self):
        event_detail_pairs = get_event_detail_pairs(self.event_soup)
        self.assertTrue(event_detail_pairs)
        self.assertGreaterEqual(len(event_detail_pairs), 4)

    def test_extract_host_parts(self):
        host_attributes = extract_host_attributes(self.event_soup)
        self.assertTrue(host_attributes)
        self.assertIsInstance(host_attributes, list)
        self.assertEqual(
            len(host_attributes),
            2,
            msg="A host name and email address were expected for this sample",
        )
        self.assertIsNone(
            get_host_by_email_address(host_attributes[0]),
            msg="The first attribute was expected to be a name, not an email address",
        )
        self.assertTrue(host_attributes[0].strip())

    def test_extract_event_time(self):
        start, end = extract_event_time(self.event_soup)
        self.assertTrue(start)
        self.assertTrue(end)
        self.assertIsInstance(start, datetime)
        self.assertIsInstance(end, datetime)
        self.assertLess(start, end)
        self.assertEqual(
            start, datetime(2026, 5, 16, 18, 0, 0, 0, tzinfo=ZoneInfo("Australia/Sydney"))
        )
        self.assertEqual(
            end, datetime(2026, 5, 16, 23, 0, 0, 0, tzinfo=ZoneInfo("Australia/Sydney"))
        )

    def test_extract_event_population(self):
        population = extract_event_population(self.event_soup)
        self.assertEqual(
            8, population, msg="The sample event had 8 people who had RSVP'd as coming"
        )

    def test_extract_event_venue(self):
        venue = extract_event_venue(self.event_soup, create_if_not_found=False)
        self.assertTrue(venue)
        self.assertEqual(venue.country_code, "AU")
        self.assertEqual(venue.city, "Brunswick")
        self.assertEqual(venue.postcode, "3056")
        self.assertEqual(venue.line1, "Hanging Gardens of Brunswick")
        self.assertEqual(venue.line2, "Unit 701")
        self.assertEqual(venue.line3, "5 Ovens Street")


class TestBlogExtraction(unittest.TestCase):
    blog_html_file = Path(__file__).parent / "blog_sample.html"

    @classmethod
    def setUpClass(cls):
        cls.blog_soup, _ = extract_importable_html(cls.blog_html_file, importable_dir=None)
        cls.assertTrue(cls, cls.blog_soup)

    def test_extract_page_size(self):
        page_size = extract_page_size(self.blog_soup)
        self.assertEqual(10, page_size)


class TestDonationExtraction(unittest.TestCase):
    donate_html_file = Path(__file__).parent / "donate_sample.html"

    @classmethod
    def setUpClass(cls):
        cls.donate_soup, _ = extract_importable_html(cls.donate_html_file, importable_dir=None)
        cls.assertTrue(cls, cls.donate_soup)

    def test_monthly_payments_allowed(self):
        allow_monthly, _ = extract_donation_frequency(self.donate_soup)
        self.assertTrue(allow_monthly, "Monthly radio button was present on the donate page")

    def test_annual_payments_not_offered(self):
        _, allow_annual = extract_donation_frequency(self.donate_soup)
        self.assertFalse(allow_annual, "No annual radio button was present on the donate page")

    def test_frequency_when_only_one_time_present(self):
        html = """
        <fieldset>
          <legend>Donation frequency</legend>
          <input type="radio" value="one-time"/>
        </fieldset>
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        allow_monthly, allow_annual = extract_donation_frequency(soup)
        self.assertFalse(allow_monthly)
        self.assertFalse(allow_annual)

    def test_frequency_when_all_three_present(self):
        html = """
        <fieldset>
          <legend>Donation frequency</legend>
          <input type="radio" value="one-time"/>
          <input type="radio" value="monthly"/>
          <input type="radio" value="annual"/>
        </fieldset>
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        allow_monthly, allow_annual = extract_donation_frequency(soup)
        self.assertTrue(allow_monthly)
        self.assertTrue(allow_annual)

    def test_frequency_yearly_alias_counts_as_annual(self):
        html = """
        <fieldset>
          <legend>Donation frequency</legend>
          <input type="radio" value="one-time"/>
          <input type="radio" value="monthly"/>
          <input type="radio" value="yearly"/>
        </fieldset>
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        _, allow_annual = extract_donation_frequency(soup)
        self.assertTrue(allow_annual)

    def test_frequency_returns_false_false_when_no_frequency_fieldset(self):
        html = "<div><input type='radio' value='monthly'/></div>"
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(extract_donation_frequency(soup), (False, False))


class TestVolunteerSignupExtraction(unittest.TestCase):
    """
    volunteer_sample.html is a real fetched copy of fusionparty.org.au's
    "Volunteer Signup" page (legacy page id 3358). The fetched snapshot
    reflects one already-signed-up member's current answers (e.g. "Blog
    writer" is checked) — extract_form_inputs() must ignore that state and
    only pull out the field descriptions, not that member's answers.
    """

    volunteer_html_file = Path(__file__).parent / "volunteer_sample.html"
    expected_checkbox_count = 17

    @classmethod
    def setUpClass(cls):
        cls.volunteer_soup, _ = extract_importable_html(
            cls.volunteer_html_file, importable_dir=None
        )
        cls.form_tag = cls.volunteer_soup.find(id="content").find("form")
        cls.assertTrue(cls, cls.form_tag)

    def test_extracts_one_entry_per_checkbox_plus_two_free_text_fields(self):
        inputs = extract_form_inputs(self.form_tag)
        checkbox_count = sum(1 for i in inputs if i.input_type == "checkbox")
        text_count = sum(1 for i in inputs if i.input_type == "text")
        self.assertEqual(
            checkbox_count,
            self.expected_checkbox_count,
            msg="The sample page has one checkbox per volunteer_type_ids[] option",
        )
        self.assertEqual(
            text_count,
            2,
            msg="The sample page has one text input (availability) and one textarea (comments)",
        )

    def test_extracts_expected_checkbox_label(self):
        inputs = extract_form_inputs(self.form_tag)
        descriptions = [i.description for i in inputs]
        self.assertIn("Party engagement (calling members)", descriptions)

    def test_extracts_expected_text_field_labels(self):
        inputs = extract_form_inputs(self.form_tag)
        descriptions = [i.description for i in inputs]
        self.assertIn("When are you available? (optional)", descriptions)
        self.assertIn("Comments, other ideas, etc. (optional)", descriptions)

    def test_hidden_bookkeeping_fields_are_excluded(self):
        descriptions = [i.description for i in extract_form_inputs(self.form_tag)]
        # authenticity_token, page_id, return_to, activity_is_private, and the
        # Rails-style empty-array fallback are all type="hidden" — none of them
        # are real questions.
        self.assertNotIn("authenticity_token", descriptions)
        self.assertNotIn("page_id", descriptions)

    def test_honeypot_email_code_field_is_excluded(self):
        """
        The "Optional email code" field is wrapped in a div with
        aria-hidden="true" and style="display:none" — it's used to recognise
        an already-logged-in visitor via a code, not a real question.
        """
        descriptions = [i.description for i in extract_form_inputs(self.form_tag)]
        self.assertNotIn("Optional email code", descriptions)


class TestBuildVolunteerSignupPage(django.test.TestCase):
    volunteer_html_file = Path(__file__).parent / "volunteer_sample.html"
    expected_checkbox_count = 17

    def setUp(self):
        self.document_soup, self.importable_html = extract_importable_html(
            self.volunteer_html_file, importable_dir=None
        )
        self.attributes = {
            "slug": "volunteer",
            "name": "Volunteer",
            "headline": "Become a volunteer",
            "title": "Volunteer - Fusion Party",
            "excerpt": "Fusion is staffed entirely by volunteers.",
            "page_type_name": "Volunteer Signup",
            "published_at": "2024-07-29T08:49:30+10:00",
        }
        self.site = Site.objects.first()

    def _build_and_save(self) -> FormPage:
        page = build_form_page(
            document_soup=self.document_soup,
            importable_html=self.importable_html,
            attributes=self.attributes,
            slug="volunteer",
            site=self.site,
        )
        Page.objects.get(id=1).add_child(instance=page)
        page.save()
        page.refresh_from_db()
        return page

    def test_creates_an_input_block_per_checkbox_and_free_text_field(self):
        page = self._build_and_save()

        self.assertEqual(
            len(page.inputs),
            self.expected_checkbox_count + 2,
            msg="Every checkbox plus the availability and comments fields should become an input block",
        )
        question_html = " ".join(
            block.value for block in list(page.body) if block.block_type == "html"
        )
        self.assertIn(
            "Doorknocking",
            question_html,
            msg="Each legacy question's text should be kept as an html block beside its input block",
        )

    def test_form_html_is_removed_from_the_html_block(self):
        page = self._build_and_save()
        html_blocks = [block.value for block in page.body if block.block_type == "html"]
        self.assertTrue(html_blocks, msg="The intro copy ahead of the form should still be kept")
        combined_html = " ".join(html_blocks)
        self.assertNotIn(
            "<form",
            combined_html,
            msg="The raw <form> markup must not be duplicated alongside the input blocks",
        )

    def test_reimporting_builds_the_same_input_blocks(self):
        """A --replace re-import must extract the same set of inputs each time."""
        first_page = self._build_and_save()

        second_page = build_form_page(
            document_soup=self.document_soup,
            importable_html=self.importable_html,
            attributes=self.attributes,
            slug="volunteer-2",
            site=self.site,
        )
        Page.objects.get(id=1).add_child(instance=second_page)
        second_page.save()
        second_page.refresh_from_db()

        self.assertEqual(
            [block.block_type for block in first_page.inputs],
            [block.block_type for block in second_page.inputs],
            msg="Re-running the importer against the same source page must extract identical inputs",
        )

    def test_volunteer_signup_page_applies_the_volunteer_tag(self):
        volunteer_tag_name = "Volunteer"
        page = self._build_and_save()
        tag_names = set(page.tags_to_apply.values_list("name", flat=True))
        self.assertEqual(
            tag_names,
            {volunteer_tag_name},
            msg=(
                "A 'Volunteer Signup' page should tag authenticated submitters as "
                f"'{volunteer_tag_name}' via tags_to_apply"
            ),
        )


class TestBuildSignupPage(django.test.TestCase):
    """
    signup_sample.html models the legacy "Signup" page type: a <form> whose
    fields address the visitor's own record. build_registration_page() must
    turn it into a RegistrationPage of person_field blocks — mapping legacy
    field names onto whitelisted Person fields, keeping the legacy question
    text as each block's label, and dropping identity fields (which
    RegistrationForm renders by itself) plus anything unmappable.
    """

    signup_html_file = Path(__file__).parent / "signup_sample.html"

    def setUp(self):
        self.document_soup, self.importable_html = extract_importable_html(
            self.signup_html_file, importable_dir=None
        )
        self.attributes = {
            "slug": "join",
            "name": "Join",
            "headline": "Join Fusion and make a difference!",
            "title": "Join Fusion and make a difference!",
            "excerpt": "Fusion is dedicated to thinking of long-term solutions.",
            "page_type_name": "Signup",
            "published_at": "2017-04-09T07:30:00+10:00",
        }
        self.site = Site.objects.first()

    def _build_and_save(self) -> RegistrationPage:
        page = build_registration_page(
            document_soup=self.document_soup,
            importable_html=self.importable_html,
            attributes=self.attributes,
            slug="join",
            site=self.site,
        )
        Page.objects.get(id=1).add_child(instance=page)
        page.save()
        page.refresh_from_db()
        return page

    def test_signup_page_type_maps_to_registration_page(self):
        self.assertIs(PAGE_BUILDING_MAP["Signup"], build_registration_page)

    def test_whitelisted_fields_become_person_field_blocks_with_legacy_labels(self):
        page = self._build_and_save()
        blocks_by_person_field = {
            block.value["field"]: block.value["label_override"] for block in page.inputs
        }
        self.assertEqual(
            blocks_by_person_field,
            {
                "mobile_number": "Mobile phone",
                "home_address": "Address",
                "email_opt_in": "Send me email updates",
            },
            msg=(
                "Each legacy signup field whose name matches a whitelisted Person "
                "field should become a person_field block labelled with the legacy "
                "question text; the legacy submitted_address question maps to the "
                "home-address role; identity fields (first/last name, email) are "
                "rendered by RegistrationForm itself, and the employer field has "
                "no Person counterpart to write to"
            ),
        )

    def test_intro_copy_is_kept_without_the_form_markup(self):
        page = self._build_and_save()
        html_blocks = [block.value for block in page.body if block.block_type == "html"]
        combined_html = " ".join(html_blocks)
        self.assertIn(
            "long-term solutions",
            combined_html,
            msg="The intro copy ahead of the form should still be kept",
        )
        self.assertNotIn(
            "<form",
            combined_html,
            msg="The raw <form> markup must not be duplicated alongside the person_field blocks",
        )


class TestBuildFormPageTagging(django.test.TestCase):
    """
    "Feedback" and "Suggestion Box" share build_form_page's <form>-extraction
    logic with "Volunteer Signup", but submitting them says nothing about
    whether the submitter volunteers, so they must not pick up the
    "Volunteer" tag.
    """

    feedback_html_file = Path(__file__).parent / "volunteer_sample.html"

    def setUp(self):
        self.document_soup, self.importable_html = extract_importable_html(
            self.feedback_html_file, importable_dir=None
        )
        self.site = Site.objects.first()

    def _build_and_save(self, page_type_name: str, slug: str) -> FormPage:
        attributes = {
            "slug": slug,
            "name": page_type_name,
            "headline": page_type_name,
            "title": f"{page_type_name} - Fusion Party",
            "page_type_name": page_type_name,
            "published_at": "2024-07-29T08:49:30+10:00",
        }
        page = build_form_page(
            document_soup=self.document_soup,
            importable_html=self.importable_html,
            attributes=attributes,
            slug=slug,
            site=self.site,
        )
        Page.objects.get(id=1).add_child(instance=page)
        page.save()
        page.refresh_from_db()
        return page

    def test_feedback_page_has_no_tags_to_apply(self):
        page = self._build_and_save("Feedback", slug="feedback")
        self.assertFalse(
            page.tags_to_apply.exists(),
            msg="A 'Feedback' submission doesn't indicate the submitter is a volunteer",
        )

    def test_suggestion_box_page_has_no_tags_to_apply(self):
        page = self._build_and_save("Suggestion Box", slug="suggestion-box")
        self.assertFalse(
            page.tags_to_apply.exists(),
            msg="A 'Suggestion Box' submission doesn't indicate the submitter is a volunteer",
        )


class TestIsSiteRoot(unittest.TestCase):
    def test_url_path_slash_is_the_site_root(self):
        self.assertTrue(is_site_root({"url_path": "/"}))

    def test_other_url_paths_are_not_the_site_root(self):
        self.assertFalse(is_site_root({"url_path": "/some-page"}))

    def test_missing_url_path_is_not_the_site_root(self):
        self.assertFalse(is_site_root({}))


@django.test.override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class TestReplaceRootPage(django.test.TestCase):
    """
    A legacy page whose own url_path was "/" must become the site's root
    (home) page in place of whatever page is there already, rather than
    being imported underneath it — see Command._replace_root_page().

    Uses a local-memory cache instead of the project's configured (Redis)
    cache: moving a page fires Wagtail's post_page_move signal, which reads
    through the default cache to resolve site root paths, and that lookup
    shouldn't require a real cache backend to be running for this test.
    """

    def setUp(self):
        self.site = Site.objects.first()
        self.old_root = self.site.root_page.specific
        self.child = UndergroundBasicPage(title="Existing subpage", slug="existing-subpage")
        self.old_root.add_child(instance=self.child)

    def test_new_page_becomes_the_site_root(self):
        new_page = UndergroundBasicPage(title="home4", slug="home4")
        updated = Command()._replace_root_page(self.site, self.old_root, new_page)

        self.site.refresh_from_db()
        self.assertEqual(self.site.root_page_id, updated.pk)

    def test_existing_subpages_stay_in_place_under_the_new_root(self):
        new_page = UndergroundBasicPage(title="home4", slug="home4")
        updated = Command()._replace_root_page(self.site, self.old_root, new_page)

        self.child.refresh_from_db()
        self.assertEqual(self.child.get_parent().pk, updated.pk)

    def test_old_root_page_no_longer_exists(self):
        old_root_pk = self.old_root.pk
        new_page = UndergroundBasicPage(title="home4", slug="home4")
        Command()._replace_root_page(self.site, self.old_root, new_page)

        self.assertFalse(Page.objects.filter(pk=old_root_pk).exists())


@django.test.override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class TestRootPageValidation(django.test.TestCase):
    """
    A Site's root_page must point at an ordinary page beneath Wagtail's own
    internal tree root, never at that tree root itself — the tree root has
    no parent, and treebeard's own add_child()/move() calls in
    _replace_root_page() assume the page they're operating on does. A
    misconfigured Site (root_page pointing at the tree root) used to surface
    as a bare `AttributeError: 'NoneType' object has no attribute
    'add_child'`; this checks the command instead raises a clear,
    actionable CommandError before doing any work.

    Uses a local-memory cache instead of the project's configured (Redis)
    cache, same as TestReplaceRootPage above: creating a Site fires
    Wagtail's post_save signal handler, which clears a site-root-paths cache
    entry, and that shouldn't require a real cache backend to be running.
    """

    def test_site_root_page_with_no_parent_raises_a_clear_error(self):
        tree_root = Page.objects.get(id=1)
        misconfigured_site = Site.objects.create(
            hostname="misconfigured.example.com",
            root_page=tree_root,
        )
        with tempfile.TemporaryDirectory() as domain_dir:
            with self.assertRaises(CommandError) as context:
                Command().create_pages_from_path(
                    domain_dir=Path(domain_dir),
                    should_replace=False,
                    page_building_map=PAGE_BUILDING_MAP,
                    site=misconfigured_site,
                    slug=None,
                )
        self.assertIn(
            "has no parent",
            str(context.exception),
            msg=(
                "The error should name the actual misconfiguration (the site's "
                "root page has no parent) instead of surfacing treebeard's own "
                "AttributeError from deep inside _replace_root_page()"
            ),
        )


class TestFormPageTypeMapping(unittest.TestCase):
    """
    "Volunteer Signup", "Feedback", and "Suggestion Box" are all legacy page
    types backed by a plain <form>, so they must all be routed to the same
    FormPage builder rather than being skipped as unsupported.
    """

    form_backed_legacy_types = ["Volunteer Signup", "Feedback", "Suggestion Box"]

    def test_form_backed_legacy_types_map_to_build_form_page(self):
        for legacy_type in self.form_backed_legacy_types:
            with self.subTest(legacy_type=legacy_type):
                self.assertEqual(
                    PAGE_BUILDING_MAP.get(legacy_type),
                    build_form_page,
                    msg=f"'{legacy_type}' is a form-only legacy page type and should build a FormPage",
                )
