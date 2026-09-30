"""
Adoption of files already in the media storage, by underground_crm/legacy_images.py.

Unlike test_legacy_images.py this uses the real Wagtail image model and a real
storage (on a temporary directory), because what is being checked is what ends
up in that storage: no second copy, and no suffixed name.
"""

import hashlib
import os
import shutil
import tempfile

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from wagtail.images.models import Image

from underground_crm.legacy_images import RemoteImageResolver
from underground_crm.test.test_legacy_images import FakeResponse, FakeSession, png_bytes

SITE = "https://media.example.org"
LEGACY = "https://legacy.example.org/site"


class ImageAdoptionTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        settings = override_settings(MEDIA_ROOT=self.media, MEDIA_URL="/media/")
        settings.enable()
        self.addCleanup(settings.disable)
        self.png = png_bytes()
        self.session = FakeSession()

    def resolver(self):
        resolver = RemoteImageResolver(
            base_url=SITE, legacy_asset_urls=(LEGACY,), satisfactory_image_domains=()
        )
        resolver.session = self.session
        return resolver

    def stored_files(self):
        folder = os.path.join(self.media, "original_images")
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def put(self, name, content):
        from django.core.files.storage import default_storage

        default_storage.save(name, ContentFile(content))

    def test_a_file_served_from_the_storage_becomes_an_image_without_a_fetch_or_a_copy(self):
        self.put("original_images/12_photo.png", self.png)
        resolver = self.resolver()

        image_id = resolver(f"{SITE}/media/original_images/12_photo.png", "A photo")

        image = Image.objects.get(pk=image_id)
        self.assertEqual(image.file.name, "original_images/12_photo.png")
        self.assertEqual(image.file_hash, hashlib.sha1(self.png).hexdigest())
        self.assertEqual((image.width, image.height), (12, 8))
        self.assertEqual(self.session.requested, [])
        self.assertEqual(self.stored_files(), ["12_photo.png"])
        self.assertEqual(resolver.adopted, 1)

    def test_it_is_the_same_image_the_next_time(self):
        self.put("original_images/12_photo.png", self.png)
        resolver = self.resolver()
        first = resolver(f"{SITE}/media/original_images/12_photo.png", "")
        second = self.resolver()(f"{SITE}/media/original_images/12_photo.png", "")
        self.assertEqual(first, second)
        self.assertEqual(Image.objects.count(), 1)

    def test_a_file_the_storage_does_not_have_is_left_alone(self):
        resolver = self.resolver()
        self.assertIsNone(resolver(f"{SITE}/media/original_images/missing.png", ""))
        self.assertEqual(resolver.skipped, 1)
        self.assertEqual(Image.objects.count(), 0)

    def test_a_download_identical_to_the_stored_file_points_at_it(self):
        self.put("original_images/photo.png", self.png)
        self.session.responses[f"{LEGACY}/photo.png"] = FakeResponse(self.png)

        image_id = self.resolver()(f"{LEGACY}/photo.png", "")

        self.assertEqual(Image.objects.get(pk=image_id).file.name, "original_images/photo.png")
        self.assertEqual(self.stored_files(), ["photo.png"])

    def test_a_different_file_of_the_same_name_is_kept_under_another_name(self):
        self.put("original_images/photo.png", png_bytes(30, 20))
        self.session.responses[f"{LEGACY}/photo.png"] = FakeResponse(self.png)

        image_id = self.resolver()(f"{LEGACY}/photo.png", "")

        name = Image.objects.get(pk=image_id).file.name
        self.assertNotEqual(name, "original_images/photo.png")
        self.assertEqual(len(self.stored_files()), 2)
