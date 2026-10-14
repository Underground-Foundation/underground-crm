"""
Copying an identity provider's profile picture into our own media storage,
and showing it on the Person admin change form.

The provider is played by a local HTTP server, so the import task makes a real
request rather than a mocked one. It serves a small JPEG portrait, an expired
URL (LinkedIn answers a request for an expired signed URL with 403 Forbidden)
and a login page served in place of the picture.
"""

import shutil
import tempfile
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import PurePosixPath

import django.test
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from PIL import Image

from underground_crm.tasks import PROFILE_PICTURE_TIMESTAMP_FORMAT, import_profile_picture

Person = get_user_model()

PORTRAIT_PATH = "/dms/image/v2/D5603AQH/profile-displayphoto-shrink_200_200/0/portrait.jpg"
EXPIRED_PATH = "/dms/image/v2/C4E03AQF/profile-displayphoto-shrink_200_200/0/expired.jpg"
LOGIN_PAGE_PATH = "/dms/image/v2/B5603AQG/profile-displayphoto-shrink_200_200/0/login.jpg"

PORTRAIT_SIZE = (200, 200)
PORTRAIT_COLOR = (182, 140, 110)


def _portrait_jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", PORTRAIT_SIZE, PORTRAIT_COLOR).save(buffer, format="JPEG")
    return buffer.getvalue()


PORTRAIT_JPEG = _portrait_jpeg()
IMPORTED_AT = datetime(2026, 10, 13, 0, 15, 23, tzinfo=timezone.utc)
LOGIN_PAGE_HTML = b"<!doctype html><title>Sign in</title><form>Sign in to continue</form>"


class _ProviderHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (the name BaseHTTPRequestHandler dispatches to)
        if self.path == PORTRAIT_PATH:
            self._respond(HTTPStatus.OK, "image/jpeg", PORTRAIT_JPEG)
        elif self.path == LOGIN_PAGE_PATH:
            self._respond(HTTPStatus.OK, "text/html; charset=utf-8", LOGIN_PAGE_HTML)
        else:
            self._respond(HTTPStatus.FORBIDDEN, "text/plain", b"Forbidden")

    def _respond(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # pylint: disable=redefined-builtin
        pass


class ImportProfilePictureTest(django.test.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.media_override = django.test.override_settings(MEDIA_ROOT=cls.media_root)
        cls.media_override.enable()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        cls.provider_url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.media_override.disable()
        shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self) -> None:
        self.person = Person.objects.create_user(
            email="priya.raman@example.com",
            password="correct-horse-battery",
            first_name="Priya",
            last_name="Raman",
        )

    def test_stores_the_picture_under_profiles(self) -> None:
        import_profile_picture(
            str(self.person.pk), self.provider_url + PORTRAIT_PATH, now=IMPORTED_AT
        )

        self.person.refresh_from_db()
        timestamp = IMPORTED_AT.strftime(PROFILE_PICTURE_TIMESTAMP_FORMAT)
        self.assertEqual(
            PurePosixPath(self.person.profile_picture_file.name),
            PurePosixPath("profiles") / f"{self.person.pk}_{timestamp}.jpg",
            "The picture should be stored under profiles/, named after the person and "
            "the time of the import, with the extension of the format Pillow found in it",
        )
        with self.person.profile_picture_file.open("rb") as stored:
            self.assertEqual(
                stored.read(),
                PORTRAIT_JPEG,
                "The stored file should hold exactly the bytes the provider served",
            )

    def test_an_expired_url_stores_nothing(self) -> None:
        import_profile_picture(str(self.person.pk), self.provider_url + EXPIRED_PATH)

        self.person.refresh_from_db()
        self.assertFalse(
            self.person.profile_picture_file,
            "A URL the provider refuses to serve (as LinkedIn does once a signed URL "
            "expires) should leave the person without a stored picture",
        )

    def test_a_page_served_in_place_of_the_picture_stores_nothing(self) -> None:
        import_profile_picture(str(self.person.pk), self.provider_url + LOGIN_PAGE_PATH)

        self.person.refresh_from_db()
        self.assertFalse(
            self.person.profile_picture_file,
            "An HTML login page served from the picture's URL must not be stored as a "
            "profile picture",
        )


class PersonAdminProfilePictureTest(django.test.TestCase):
    """The change form shows our own copy of the picture, once it has one."""

    def setUp(self) -> None:
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        media_override = django.test.override_settings(MEDIA_ROOT=self.media_root)
        media_override.enable()
        self.addCleanup(media_override.disable)

        staff_user = Person.objects.create_user(
            email="membership.officer@example.com",
            password="correct-horse-battery",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(staff_user)
        self.person = Person.objects.create_user(
            email="priya.raman@example.com",
            password="correct-horse-battery",
            first_name="Priya",
            last_name="Raman",
        )
        self.change_url = reverse("admin:underground_crm_person_change", args=[self.person.pk])

    def test_shows_no_image_before_the_picture_is_imported(self) -> None:
        response = self.client.get(self.change_url)

        self.assertNotContains(
            response,
            'alt="Profile picture"',
            msg_prefix="With no stored picture, the change form should show no image "
            "rather than a broken one",
        )

    def test_shows_the_stored_picture(self) -> None:
        timestamp = IMPORTED_AT.strftime(PROFILE_PICTURE_TIMESTAMP_FORMAT)
        self.person.profile_picture_file.save(
            f"{self.person.pk}_{timestamp}.jpg", ContentFile(PORTRAIT_JPEG), save=True
        )

        response = self.client.get(self.change_url)

        self.assertContains(
            response,
            f'<img src="{self.person.profile_picture_file.url}"',
            msg_prefix="The change form should show the picture from our media storage",
        )
