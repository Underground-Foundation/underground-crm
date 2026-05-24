"""
Tests for underground_crm.blocks.SanitizedRawHTMLBlock.

Wagtail requires a fully configured Django environment to import its block
classes.  Rather than bootstrapping a complete project in the test runner,
we test the sanitization logic by calling ``nh3.clean()`` with the exact
allow-lists exported from the blocks module.  This verifies that the
allow-lists are correct without requiring a running database or application
server.
"""

import unittest

import nh3

# Import the allow-lists directly — these are plain Python data structures
# that do not trigger any Django/Wagtail initialisation.
from underground_crm.sanitization import (
    ALLOWED_ATTRIBUTES,
    ALLOWED_TAGS,
    ALLOWED_URL_SCHEMES,
)


def _clean(html: str) -> str:
    """Mirror what SanitizedRawHTMLBlock.clean() does."""
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel=None,
    )


class SanitizedRawHTMLBlockTest(unittest.TestCase):
    """Verify that the allow-lists strip dangerous HTML while preserving benign markup."""

    # ── XSS vectors that must be stripped ────────────────────────────────

    def test_script_tag_is_stripped(self):
        dirty = '<p>Hello</p><script>alert("xss")</script>'
        clean = _clean(dirty)
        self.assertNotIn("<script", clean)
        self.assertNotIn("alert", clean)
        self.assertIn("<p>Hello</p>", clean)

    def test_event_handler_attribute_is_stripped(self):
        dirty = '<img src="x.png" onerror="alert(1)">'
        clean = _clean(dirty)
        self.assertNotIn("onerror", clean)
        # The <img> tag itself should survive (it is allowed).
        self.assertIn("<img", clean)

    def test_javascript_uri_is_stripped(self):
        dirty = '<a href="javascript:alert(1)">Click me</a>'
        clean = _clean(dirty)
        self.assertNotIn("javascript:", clean)
        self.assertIn("Click me", clean)

    def test_iframe_is_stripped(self):
        dirty = '<iframe src="https://evil.com"></iframe>'
        clean = _clean(dirty)
        self.assertNotIn("<iframe", clean)

    def test_object_embed_form_are_stripped(self):
        dirty = (
            '<object data="x"></object>'
            '<embed src="x">'
            '<form action="/steal"><input type="text"></form>'
        )
        clean = _clean(dirty)
        for tag in ("object", "embed", "form", "input"):
            self.assertNotIn(f"<{tag}", clean)

    def test_style_tag_is_stripped(self):
        """The <style> element (not the attribute) must be removed."""
        dirty = "<style>body { display: none; }</style><p>Visible</p>"
        clean = _clean(dirty)
        self.assertNotIn("<style", clean)
        self.assertIn("<p>Visible</p>", clean)

    def test_onclick_attribute_is_stripped(self):
        dirty = '<div onclick="stealCookies()">Click</div>'
        clean = _clean(dirty)
        self.assertNotIn("onclick", clean)
        self.assertIn("<div>Click</div>", clean)

    # ── Benign HTML that must be preserved ───────────────────────────────

    def test_paragraphs_and_headings_pass_through(self):
        html = "<h2>Title</h2><p>Body text with <strong>bold</strong> and <em>italic</em>.</p>"
        clean = _clean(html)
        self.assertEqual(clean, html)

    def test_links_pass_through(self):
        html = '<a href="https://example.com" target="_blank" rel="noopener">Link</a>'
        clean = _clean(html)
        self.assertIn('href="https://example.com"', clean)
        self.assertIn('target="_blank"', clean)
        self.assertIn('rel="noopener"', clean)

    def test_images_pass_through(self):
        html = '<img src="https://example.com/photo.jpg" alt="A photo" width="600">'
        clean = _clean(html)
        self.assertIn('src="https://example.com/photo.jpg"', clean)
        self.assertIn('alt="A photo"', clean)

    def test_table_markup_passes_through(self):
        html = (
            "<table><thead><tr><th>Name</th></tr></thead>"
            "<tbody><tr><td>Alice</td></tr></tbody></table>"
        )
        clean = _clean(html)
        self.assertEqual(clean, html)

    def test_semantic_elements_pass_through(self):
        html = "<section><article><header><h3>Post</h3></header><p>Content</p></article></section>"
        clean = _clean(html)
        self.assertEqual(clean, html)

    def test_media_elements_pass_through(self):
        html = '<video controls><source src="video.mp4" type="video/mp4"></video>'
        clean = _clean(html)
        self.assertIn("<video", clean)
        self.assertIn("<source", clean)

    def test_class_and_id_attributes_pass_through(self):
        html = '<div class="container" id="main"><p>Text</p></div>'
        clean = _clean(html)
        self.assertIn('class="container"', clean)
        self.assertIn('id="main"', clean)

    def test_empty_string_passes_through(self):
        clean = _clean("")
        self.assertEqual(clean, "")

    def test_mailto_uri_passes_through(self):
        html = '<a href="mailto:admin@example.com">Email us</a>'
        clean = _clean(html)
        self.assertIn('href="mailto:admin@example.com"', clean)
