"""
Tests for underground_crm/legacy_assets.py.

The Wagtail image model and a real storage (on a temporary directory) are used,
because bringing an image across means it ends up in the library; the legacy
host is a fake session, and the uploader for everything else a recording function.
"""

import shutil
import tempfile

import requests
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from wagtail.documents.models import Document
from wagtail.images.models import Image

from underground_crm.legacy_assets import (
    LegacyAssetMigrator,
    asset_filename,
    parse_asset_url,
    store_in_media,
)
from underground_crm.legacy_documents import RemoteDocumentResolver
from underground_crm.legacy_images import RemoteImageResolver
from underground_crm.test.test_legacy_images import FakeResponse, FakeSession, png_bytes

LEGACY = "https://assets.legacy.example/site-slug"
PHOTO = f"{LEGACY}/pages/4106/attachments/original/1720696911/photo.png?1720696911"
DIAGRAM = f"{LEGACY}/pages/4106/attachments/original/1720696912/diagram.svg"
FORM = f"{LEGACY}/pages/3623/attachments/original/1761520197/My form.pdf"
OG_IMAGE = f"{LEGACY}/pages/4106/meta_images/original/og.png?1"
STYLES = f"{LEGACY}/themes/x/default/styles.css"
CALENDAR = f"{LEGACY}/pages/1/attachments/original/2/invite.ics9"

PAGE = f"""<html><head>
<meta property="og:image" content="{OG_IMAGE}">
<link rel="stylesheet" href="{STYLES}">
</head><body>
<img src="{PHOTO}" alt="A photo">
<img src="{DIAGRAM}">
<a href="{FORM}">the form</a>
<a href="{CALENDAR}">invite</a>
</body></html>"""


class LegacyAssetTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        settings = override_settings(MEDIA_ROOT=self.media, MEDIA_URL="/media/")
        settings.enable()
        self.addCleanup(settings.disable)
        self.session = FakeSession(
            {
                PHOTO: FakeResponse(png_bytes(12, 8)),
                OG_IMAGE: FakeResponse(png_bytes(20, 10)),
                DIAGRAM: FakeResponse(b"<svg/>", content_type="image/svg+xml"),
                FORM: FakeResponse(b"%PDF", content_type="application/pdf"),
            }
        )
        self.uploaded = []

    def upload(self, name, fetch, content_type):
        self.uploaded.append((name, content_type, fetch()))
        return f"https://cdn.example/{name}"

    def migrator(self, **options):
        resolvers = []
        for resolver_class in (RemoteImageResolver, RemoteDocumentResolver):
            resolver = resolver_class(
                base_url="https://site.example",
                legacy_asset_urls=(LEGACY,),
                satisfactory_image_domains=(),
            )
            resolver.session = self.session
            resolvers.append(resolver)
        return LegacyAssetMigrator(
            *resolvers,
            (LEGACY,),
            upload_asset=self.upload,
            session=self.session,
            **options,
        )

    def test_asset_names_carry_the_legacy_id_and_are_valid_filenames(self):
        self.assertEqual(parse_asset_url(PHOTO)[:2], ("1720696911", "photo.png"))
        self.assertEqual(parse_asset_url(OG_IMAGE)[:2], ("pages4106", "og.png"))
        self.assertEqual(asset_filename("3623", "My form (1).pdf"), "3623_My_form_1.pdf")

    def test_images_and_documents_become_library_records_and_the_rest_go_to_the_uploader(self):
        html, stats = self.migrator(replace=True).process_html(PAGE, "page.html")

        names = sorted(image.file.name for image in Image.objects.all())
        self.assertEqual(
            names,
            ["original_images/1720696911_photo.png", "original_images/pages4106_og.png"],
        )
        self.assertIn('src="/media/original_images/1720696911_photo.png"', html)
        self.assertIn('content="/media/original_images/pages4106_og.png"', html)
        self.assertIn('src="https://cdn.example/original_images/1720696912_diagram.svg"', html)
        self.assertEqual(
            [document.file.name for document in Document.objects.all()],
            ["documents/1761520197_My_form.pdf"],
        )
        self.assertEqual(Document.objects.get().title, "the form")
        self.assertIn('href="/media/documents/1761520197_My_form.pdf"', html)
        self.assertEqual(
            [(name, kind) for name, kind, _ in self.uploaded],
            [("original_images/1720696912_diagram.svg", "image/svg+xml")],
        )
        self.assertEqual(self.uploaded[0][2], b"<svg/>")
        self.assertEqual(stats, {"images": 3, "documents": 1, "commented": 1, "other": 1})

    def test_stylesheets_are_commented_out_and_unknown_types_left_alone(self):
        html, _ = self.migrator(replace=True).process_html(PAGE, "page.html")
        self.assertIn(f'<!-- <link rel="stylesheet" href="{STYLES}"> -->', html)
        self.assertIn(f'href="{CALENDAR}"', html)

    def test_without_replace_the_page_is_left_alone_but_the_assets_are_brought_across(self):
        html, _ = self.migrator().process_html(PAGE, "page.html")
        self.assertEqual(html, PAGE)
        self.assertEqual(Image.objects.count(), 2)
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(len(self.uploaded), 1)

    def test_a_dry_run_changes_nothing(self):
        html, stats = self.migrator(replace=True, dry_run=True).process_html(PAGE, "page.html")
        self.assertEqual(html, PAGE)
        self.assertEqual(Image.objects.count(), 0)
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(self.uploaded, [])
        self.assertEqual(stats["images"], 3)

    def test_a_second_run_makes_no_second_copy_of_an_image(self):
        self.migrator(replace=True).process_html(PAGE, "page.html")
        self.migrator(replace=True).process_html(PAGE, "page.html")
        self.assertEqual(Image.objects.count(), 2)
        self.assertEqual(Document.objects.count(), 1)

    def test_an_asset_that_cannot_be_fetched_is_counted_and_left_in_the_page(self):
        self.session.responses[DIAGRAM] = FakeResponse(error=requests.HTTPError("HTTP 404"))
        migrator = self.migrator(replace=True)
        html, _ = migrator.process_html(PAGE, "page.html")
        self.assertEqual(migrator.failed, 1)
        self.assertIn(f'src="{DIAGRAM}"', html)
        self.assertIn('href="/media/documents/1761520197_My_form.pdf"', html)


class StoreInMediaTests(TestCase):
    def test_a_file_already_in_the_storage_is_not_fetched_again(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media, MEDIA_URL="/media/"):
            fetched = []

            def fetch():
                fetched.append(1)
                return b"%PDF"

            first = store_in_media("documents/1_form.pdf", fetch, "application/pdf")
            second = store_in_media("documents/1_form.pdf", fetch, "application/pdf")

            self.assertEqual(first, "/media/documents/1_form.pdf")
            self.assertEqual(second, first)
            self.assertEqual(len(fetched), 1)
            self.assertEqual(default_storage.listdir("documents")[1], ["1_form.pdf"])
