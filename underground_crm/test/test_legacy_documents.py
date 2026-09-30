"""
Tests for underground_crm/legacy_documents.py, and for the page import's use of it.

The real Wagtail document model and a real storage (on a temporary directory) are
used, since what matters is what ends up in the library and the storage; the legacy
host is a fake session.
"""

import shutil
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from wagtail.documents.models import Document

from underground_crm.legacy_documents import (
    RemoteDocumentResolver,
    is_document_url,
    rewrite_document_links,
)
from underground_crm.management.commands.import_pages import extract_importable_html
from underground_crm.test.test_legacy_images import FakeResponse, FakeSession

LEGACY = "https://assets.legacy.example/site-slug"
FORM = f"{LEGACY}/pages/3623/attachments/original/1761520197/Sign-up form.pdf?1761520197"
PDF = b"%PDF-1.4 a form"


class DocumentTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        settings = override_settings(MEDIA_ROOT=self.media, MEDIA_URL="/media/")
        settings.enable()
        self.addCleanup(settings.disable)
        self.session = FakeSession({FORM: FakeResponse(PDF, content_type="application/pdf")})

    def resolver(self):
        resolver = RemoteDocumentResolver(
            base_url="https://site.example",
            legacy_asset_urls=(LEGACY,),
            satisfactory_image_domains=(),
        )
        resolver.session = self.session
        return resolver

    def test_a_legacy_document_becomes_a_wagtail_document(self):
        resolver = self.resolver()
        pk = resolver(FORM, "Sign up here")
        document = Document.objects.get(pk=pk)
        self.assertEqual(document.title, "Sign up here")
        self.assertEqual(document.file.name, "documents/Sign-up_form.pdf")
        self.assertEqual(document.file.read(), PDF)
        self.assertEqual(resolver.url_of(pk), "/media/documents/Sign-up_form.pdf")

    def test_the_same_document_is_not_stored_twice(self):
        first = self.resolver()(FORM, "")
        second = self.resolver()(FORM, "")
        self.assertEqual(first, second)
        self.assertEqual(Document.objects.count(), 1)

    def test_a_page_of_html_is_not_taken_for_a_document(self):
        self.session.responses[FORM] = FakeResponse(b"<html>Gone</html>", content_type="text/html")
        self.assertIsNone(self.resolver()(FORM, ""))
        self.assertEqual(Document.objects.count(), 0)

    def test_a_file_already_in_the_storage_is_adopted_not_copied(self):
        default_storage.save("documents/1_form.pdf", ContentFile(PDF))
        resolver = self.resolver()
        pk = resolver("https://site.example/media/documents/1_form.pdf", "")
        self.assertEqual(Document.objects.get(pk=pk).file.name, "documents/1_form.pdf")
        self.assertEqual(self.session.requested, [])
        self.assertEqual(default_storage.listdir("documents")[1], ["1_form.pdf"])

    def test_an_identical_download_points_at_the_stored_file(self):
        default_storage.save("documents/Sign-up_form.pdf", ContentFile(PDF))
        pk = self.resolver()(FORM, "")
        self.assertEqual(Document.objects.get(pk=pk).file.name, "documents/Sign-up_form.pdf")
        self.assertEqual(default_storage.listdir("documents")[1], ["Sign-up_form.pdf"])

    def test_which_links_are_to_documents(self):
        self.assertTrue(is_document_url(FORM))
        self.assertTrue(is_document_url("/uploads/Minutes.PDF"))
        self.assertFalse(is_document_url("/policy"))
        self.assertFalse(is_document_url("https://example.org/page.html"))

    def test_only_links_to_documents_are_rewritten(self):
        soup = BeautifulSoup(
            f'<p><a href="{FORM}">the form</a> <a href="/policy">policy</a> '
            f'<a href="https://elsewhere.example/other.pdf">theirs</a></p>',
            "html.parser",
        )
        changed = rewrite_document_links(soup, self.resolver())
        hrefs = [link["href"] for link in soup.find_all("a")]
        self.assertEqual(changed, 1)
        self.assertEqual(hrefs[0], "/media/documents/Sign-up_form.pdf")
        self.assertEqual(hrefs[1:], ["/policy", "https://elsewhere.example/other.pdf"])

    def test_the_import_extract_and_soup_both_refer_to_the_stored_document(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        page = folder / "join.html"
        page.write_text(
            f'<html><body><div id="content"><p><a href="{FORM}">the form</a></p></div></body></html>',
            encoding="utf-8",
        )
        importable = folder / "importable"
        importable.mkdir()

        soup, html = extract_importable_html(page, importable, self.resolver())

        self.assertIn("/media/documents/Sign-up_form.pdf", html)
        self.assertIn("/media/documents/Sign-up_form.pdf", (importable / "join.html").read_text())
        self.assertEqual(soup.find("a")["href"], "/media/documents/Sign-up_form.pdf")
        self.assertIn(FORM, page.read_text())  # the fetched page itself is untouched
