"""
Tests for the markup of images placed in rich text, which is sent in email campaigns.

A full-width photo used to be emitted with its natural `height` attribute and a CSS min()
cap on its width.  When a recipient replied, their mail client quoted the message and
stripped the inline style, leaving the image shrunk to fit the reply's width but still at
its full height, so it was stretched vertically.  These tests check that the markup keeps
an image's proportions even when the style is thrown away, and that an email template's
`cap_image_widths` filter keeps a wide image within the template's column in Outlook for
Windows, which sizes images from the `width` attribute alone.
"""

import shutil
import tempfile

from bs4 import BeautifulSoup, Tag
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.safestring import mark_safe
from wagtail.images import get_image_model
from wagtail.images.formats import get_image_format
from wagtail.images.tests.utils import get_test_image_file

from underground_email.templatetags.email_images import cap_image_widths

# A banner photo much wider than any email's column.
WIDE_PHOTO_SIZE = (2000, 1000)
# A logo narrower than half of any email's column, which must never be upscaled.
SMALL_LOGO_SIZE = (200, 120)
# The column that cap_image_widths is told about in the filter's own tests.
COLUMN_WIDTH = 550
HALF_WIDTH_PERCENT = 50
ALT_TEXT = "Election watch party"
CAMPAIGN_TEMPLATE = "underground_email/smtp2go_base.html"
# Wagtail caches renditions in the default cache, which is Redis in the base settings.
# That cache has no bearing on the markup under test, so keep it in memory.
IN_MEMORY_CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _assert_only_one_img(html: str) -> Tag:
    images = BeautifulSoup(html, "html.parser").find_all("img")
    assert len(images) == 1, f"Expected exactly one <img> in: {html}"
    return images[0]


class IntrinsicFormatTests(TestCase):
    def setUp(self) -> None:
        self.media_root = tempfile.mkdtemp()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_root, CACHES=IN_MEMORY_CACHES
        )
        self.settings_override.enable()
        Image = get_image_model()
        self.wide_photo = Image.objects.create(
            title="Election watch party",
            file=get_test_image_file(filename="election_party.png", size=WIDE_PHOTO_SIZE),
        )
        self.small_logo = Image.objects.create(
            title="Party logo",
            file=get_test_image_file(filename="party_logo.png", size=SMALL_LOGO_SIZE),
        )

    def tearDown(self) -> None:
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _render(self, format_name: str, image) -> str:
        return get_image_format(format_name).image_to_html(image, ALT_TEXT)

    def _assert_proportions_survive_style_stripping(self, img: Tag) -> None:
        self.assertNotIn(
            "height",
            img.attrs,
            "A height attribute keeps the image at its full height when a mail client "
            "strips the inline style and shrinks the width to fit.",
        )
        self.assertIn("height: auto", img["style"])
        self.assertNotIn(
            "min(",
            img["style"],
            "Outlook and many webmail sanitizers discard a declaration that uses CSS min().",
        )

    def test_every_format_keeps_proportions_at_natural_width(self) -> None:
        for format_name in ("fullwidth", "w-50", "left", "right"):
            for image, size in (
                (self.wide_photo, WIDE_PHOTO_SIZE),
                (self.small_logo, SMALL_LOGO_SIZE),
            ):
                with self.subTest(format_name=format_name, image=image.title):
                    img = _assert_only_one_img(self._render(format_name, image))
                    self._assert_proportions_survive_style_stripping(img)
                    self.assertEqual(
                        int(img["width"]),
                        size[0],
                        "The format cannot know the column width, so it leaves the cap to "
                        "the email template's cap_image_widths filter.",
                    )
                    self.assertIn(f"max-width: {size[0]}px", img["style"])

    def test_half_width_style_asks_for_half_the_container(self) -> None:
        img = _assert_only_one_img(self._render("w-50", self.wide_photo))
        self.assertIn(f"width: {HALF_WIDTH_PERCENT}%", img["style"])

    def test_campaign_template_caps_a_wide_photo_at_its_column(self) -> None:
        email_html = render_to_string(
            CAMPAIGN_TEMPLATE, {"body": mark_safe(self._render("fullwidth", self.wide_photo))}
        )
        body_images = [
            img
            for img in BeautifulSoup(email_html, "html.parser").find_all("img")
            if img.get("alt") == ALT_TEXT
        ]
        self.assertEqual(len(body_images), 1, "The body holds the one photo it was given.")
        img = body_images[0]
        self.assertLess(
            int(img["width"]),
            WIDE_PHOTO_SIZE[0],
            "The template must cap a photo wider than any email's column, or Outlook for "
            "Windows will draw it at its full width.",
        )
        self._assert_proportions_survive_style_stripping(img)


class CapImageWidthsTests(SimpleTestCase):
    def test_full_width_photo_is_capped_at_the_column(self) -> None:
        html = (
            f'<p>Join us on election night.</p><img alt="{ALT_TEXT}" '
            f'src="/media/images/election_party.original.jpg" width="{WIDE_PHOTO_SIZE[0]}" '
            f'style="display: block; margin: 0 auto; width: 100%; '
            f'max-width: {WIDE_PHOTO_SIZE[0]}px; height: auto">'
        )
        img = _assert_only_one_img(cap_image_widths(html, COLUMN_WIDTH))
        self.assertEqual(int(img["width"]), COLUMN_WIDTH)

    def test_half_width_photo_is_capped_at_half_the_column(self) -> None:
        html = (
            f'<img alt="{ALT_TEXT}" src="/media/images/election_party.original.jpg" '
            f'width="{WIDE_PHOTO_SIZE[0]}" style="width: {HALF_WIDTH_PERCENT}%; '
            f'max-width: {WIDE_PHOTO_SIZE[0]}px; height: auto">'
        )
        img = _assert_only_one_img(cap_image_widths(html, COLUMN_WIDTH))
        self.assertEqual(int(img["width"]), COLUMN_WIDTH * HALF_WIDTH_PERCENT // 100)

    def test_capped_image_loses_its_height_attribute(self) -> None:
        # Pasted HTML can carry a height attribute that the image formats never write.
        html = (
            f'<img alt="{ALT_TEXT}" src="https://fusionparty.space/media/election_party.jpg" '
            f'width="{WIDE_PHOTO_SIZE[0]}" height="{WIDE_PHOTO_SIZE[1]}">'
        )
        img = _assert_only_one_img(cap_image_widths(html, COLUMN_WIDTH))
        self.assertEqual(int(img["width"]), COLUMN_WIDTH)
        self.assertNotIn(
            "height",
            img.attrs,
            "The old height no longer matches the capped width, and would stretch the image.",
        )

    def test_narrow_image_is_left_untouched(self) -> None:
        html = (
            f'<img alt="Party logo" src="/media/images/party_logo.original.png" '
            f'width="{SMALL_LOGO_SIZE[0]}" height="{SMALL_LOGO_SIZE[1]}">'
        )
        img = _assert_only_one_img(cap_image_widths(html, COLUMN_WIDTH))
        self.assertEqual(int(img["width"]), SMALL_LOGO_SIZE[0])
        self.assertEqual(
            int(img["height"]),
            SMALL_LOGO_SIZE[1],
            "An image that already fits keeps its height, which still matches its width.",
        )

    def test_percentage_width_attribute_is_left_alone(self) -> None:
        percentage_width = f"{HALF_WIDTH_PERCENT}%"
        html = f'<img alt="Party logo" src="/media/party_logo.png" width="{percentage_width}">'
        img = _assert_only_one_img(cap_image_widths(html, COLUMN_WIDTH))
        self.assertEqual(img["width"], percentage_width)
