"""
Tests for underground_crm/legacy_images.py.

No database and no network: the Wagtail image model is stood in for by a
recording stub (the resolver reaches it through one classmethod precisely so
that this is possible), and the HTTP session by a fake that returns canned
responses. What is being checked is the decision-making around the download
— which sources are worth fetching, when an existing image is reused, what a
failure does — not Wagtail's own storage.
"""

import io
import os
import unittest
from unittest import mock

import requests
from PIL import Image as PillowImage

from underground_crm.legacy_images import RemoteImageResolver

# A stand-in LEGACY_ASSET_URLS entry, used as the default for build_resolver()
# below so these tests don't depend on (or leak into) the real environment.
LEGACY_ASSET_URL = "https://assets.legacycrm.example.com/site-slug"


def png_bytes(width: int = 12, height: int = 8) -> bytes:
    buffer = io.BytesIO()
    PillowImage.new("RGB", (width, height), "purple").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeResponse:

    def __init__(self, content=b"", content_type="image/png", error=None):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error


class FakeSession:
    """Returns a canned response per URL, and records what was asked for."""

    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.requested = []

    def get(self, url, timeout=None):
        self.requested.append(url)
        if self.error:
            raise self.error
        return self.responses.get(url, FakeResponse(png_bytes()))


class FakeImage:
    """Enough of a Wagtail image to be saved and read back."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.pk = None
        self.file_hash = ""
        self.file_size = 0


class FakeManager:

    def __init__(self):
        self.stored = {}

    def filter(self, file_hash=None):
        manager = self

        class Query:
            def first(self):
                return manager.stored.get(file_hash)

        return Query()


class FakeImageModel:

    def __init__(self):
        self.objects = FakeManager()

    def __call__(self, **kwargs):
        image = FakeImage(**kwargs)

        def save():
            image.pk = len(self.objects.stored) + 1
            self.objects.stored[image.file_hash] = image

        image.save = save
        return image


def build_resolver(session=None, image_model=None, **kwargs) -> RemoteImageResolver:
    kwargs.setdefault("legacy_asset_urls", (LEGACY_ASSET_URL,))
    kwargs.setdefault("satisfactory_image_domains", ())
    resolver = RemoteImageResolver(**kwargs)
    resolver.session = session or FakeSession()
    model = image_model or FakeImageModel()
    resolver._image_model = lambda: model  # pylint: disable=protected-access
    return resolver


class TestSourceEligibility(unittest.TestCase):

    def test_relative_source_is_skipped_without_a_base_url(self):
        session = FakeSession()
        resolver = build_resolver(session)
        self.assertIsNone(resolver("image.png", ""))
        self.assertEqual(session.requested, [])
        self.assertEqual(resolver.skipped, 1)

    def test_relative_source_is_resolved_against_a_base_url(self):
        session = FakeSession()
        resolver = build_resolver(session, base_url=f"{LEGACY_ASSET_URL}/")
        self.assertIsNotNone(resolver("images/banner.png", ""))
        self.assertEqual(session.requested, [f"{LEGACY_ASSET_URL}/images/banner.png"])

    def test_data_uri_is_skipped(self):
        session = FakeSession()
        resolver = build_resolver(session, base_url=f"{LEGACY_ASSET_URL}/")
        self.assertIsNone(resolver("data:image/png;base64,iVBORw0KGgo=", ""))
        self.assertEqual(session.requested, [])

    def test_svg_is_skipped_before_it_is_fetched(self):
        session = FakeSession()
        resolver = build_resolver(session)
        self.assertIsNone(resolver(f"{LEGACY_ASSET_URL}/logo.svg", ""))
        self.assertEqual(session.requested, [])

    def test_a_page_served_where_an_image_was_expected_is_skipped(self):
        url = f"{LEGACY_ASSET_URL}/missing.png"
        session = FakeSession({url: FakeResponse(b"<html>404</html>", "text/html")})
        resolver = build_resolver(session)
        self.assertIsNone(resolver(url, ""))
        self.assertEqual(resolver.skipped, 1)

    def test_a_failed_request_is_skipped_rather_than_raised(self):
        resolver = build_resolver(FakeSession(error=requests.ConnectionError("no route")))
        self.assertIsNone(resolver(f"{LEGACY_ASSET_URL}/b.png", ""))
        self.assertEqual(resolver.skipped, 1)

    def test_unreadable_bytes_are_skipped_rather_than_raised(self):
        url = f"{LEGACY_ASSET_URL}/truncated.png"
        session = FakeSession({url: FakeResponse(b"not really a png")})
        resolver = build_resolver(session)
        self.assertIsNone(resolver(url, ""))
        self.assertEqual(resolver.skipped, 1)

    def test_a_url_not_prefixed_by_a_legacy_asset_url_is_skipped_even_if_absolute(self):
        session = FakeSession()
        resolver = build_resolver(session)
        self.assertIsNone(resolver("https://www.fusionparty.org.au/photos/banner.png", ""))
        self.assertEqual(session.requested, [])
        self.assertEqual(resolver.skipped, 1)

    def test_a_url_with_uploads_in_the_path_is_internalized_even_off_a_legacy_asset_url(self):
        url = "https://cdn.example.test/uploads/banner.png"
        session = FakeSession({url: FakeResponse(png_bytes())})
        resolver = build_resolver(session)
        self.assertIsNotNone(resolver(url, ""))
        self.assertEqual(session.requested, [url])

    def test_a_url_on_a_satisfactory_domain_is_skipped_even_with_uploads_in_the_path(self):
        session = FakeSession()
        resolver = build_resolver(session, satisfactory_image_domains=("fusionparty.org.au",))
        self.assertIsNone(resolver("https://fusionparty.org.au/uploads/banner.png", ""))
        self.assertEqual(session.requested, [])
        self.assertEqual(resolver.skipped, 1)

    def test_a_url_on_a_satisfactory_domain_is_skipped_even_if_it_matches_a_legacy_asset_url(self):
        # A satisfactory domain wins even over an explicit LEGACY_ASSET_URLS
        # prefix match: it's already ours, so there's nothing to fetch.
        session = FakeSession()
        resolver = build_resolver(
            session, satisfactory_image_domains=("assets.legacycrm.example.com",)
        )
        self.assertIsNone(resolver(f"{LEGACY_ASSET_URL}/banner.png", ""))
        self.assertEqual(session.requested, [])
        self.assertEqual(resolver.skipped, 1)

    def test_a_satisfactory_domain_matches_a_subdomain_too(self):
        session = FakeSession()
        resolver = build_resolver(session, satisfactory_image_domains=("fusionparty.org.au",))
        self.assertIsNone(resolver("https://cdn.fusionparty.org.au/uploads/banner.png", ""))
        self.assertEqual(session.requested, [])
        self.assertEqual(resolver.skipped, 1)

    def test_satisfactory_image_domains_defaults_to_the_environment_variable(self):
        url = "https://fusionparty.org.au/uploads/banner.png"
        session = FakeSession({url: FakeResponse(png_bytes())})
        with mock.patch.dict(os.environ, {"SATISFACTORY_IMAGE_DOMAINS": "fusionparty.org.au"}):
            resolver = RemoteImageResolver(legacy_asset_urls=())
        resolver.session = session
        resolver._image_model = lambda: FakeImageModel()  # pylint: disable=protected-access
        self.assertIsNone(resolver(url, ""))
        self.assertEqual(session.requested, [])

    def test_a_url_on_the_same_host_but_a_different_path_is_skipped(self):
        # LEGACY_ASSET_URLS is a prefix match, not just a host match: a
        # second site's uploads sharing the asset host but not the path
        # aren't ours to internalize.
        session = FakeSession()
        resolver = build_resolver(session)
        other_site_url = "https://assets.legacycrm.example.com/a-different-site/banner.png"
        self.assertIsNone(resolver(other_site_url, ""))
        self.assertEqual(session.requested, [])
        self.assertEqual(resolver.skipped, 1)

    def test_a_relative_source_is_internalized_even_off_a_legacy_asset_url(self):
        # A bare "image.png" in a legacy body was never anything but relative
        # to that body — resolving it against our own domain (the usual
        # --image-base-url default) doesn't make it any less a legacy asset,
        # so it isn't held to the LEGACY_ASSET_URLS check absolute sources are.
        url = "https://www.fusionparty.org.au/images/banner.png"
        session = FakeSession({url: FakeResponse(png_bytes())})
        resolver = build_resolver(session, base_url="https://www.fusionparty.org.au/")
        self.assertIsNotNone(resolver("images/banner.png", ""))
        self.assertEqual(session.requested, [url])

    def test_legacy_asset_urls_can_be_overridden(self):
        url = "https://cdn.example.com/banner.png"
        session = FakeSession({url: FakeResponse(png_bytes())})
        resolver = build_resolver(session, legacy_asset_urls=("https://cdn.example.com",))
        self.assertIsNotNone(resolver(url, ""))
        self.assertEqual(session.requested, [url])

    def test_legacy_asset_urls_defaults_to_the_environment_variable(self):
        url = "https://assets.example.test/uploads/banner.png"
        session = FakeSession({url: FakeResponse(png_bytes())})
        with mock.patch.dict(os.environ, {"LEGACY_ASSET_URLS": "https://assets.example.test"}):
            resolver = RemoteImageResolver()
        resolver.session = session
        resolver._image_model = lambda: FakeImageModel()  # pylint: disable=protected-access
        self.assertIsNotNone(resolver(url, ""))
        self.assertEqual(session.requested, [url])


class TestStoringImages(unittest.TestCase):

    def test_an_image_is_stored_with_its_dimensions(self):
        url = f"{LEGACY_ASSET_URL}/banner.png"
        session = FakeSession({url: FakeResponse(png_bytes(120, 40))})
        model = FakeImageModel()
        resolver = build_resolver(session, model)
        image_id = resolver(url, "A banner")
        stored = model.objects.stored[next(iter(model.objects.stored))]
        self.assertEqual(stored.pk, image_id)
        self.assertEqual((stored.width, stored.height), (120, 40))
        self.assertEqual(resolver.fetched, 1)

    def test_alt_text_becomes_the_title(self):
        resolver = build_resolver(image_model=(model := FakeImageModel()))
        resolver(
            f"{LEGACY_ASSET_URL}/Vic_state_party_rego.png?1760233316",
            "Registration announcement",
        )
        stored = next(iter(model.objects.stored.values()))
        self.assertEqual(stored.title, "Registration announcement")

    def test_the_filename_becomes_the_title_when_there_is_no_alt_text(self):
        resolver = build_resolver(image_model=(model := FakeImageModel()))
        resolver(f"{LEGACY_ASSET_URL}/Vic_state_party_rego.png?1760233316", "")
        stored = next(iter(model.objects.stored.values()))
        self.assertEqual(stored.title, "Vic state party rego")
        self.assertEqual(stored.file.name, "Vic_state_party_rego.png")

    def test_the_same_source_is_only_fetched_once(self):
        session = FakeSession()
        resolver = build_resolver(session)
        first = resolver(f"{LEGACY_ASSET_URL}/b.png", "")
        second = resolver(f"{LEGACY_ASSET_URL}/b.png", "")
        self.assertEqual(first, second)
        self.assertEqual(len(session.requested), 1)

    def test_identical_content_at_two_urls_reuses_the_stored_image(self):
        shared = png_bytes(20, 20)
        session = FakeSession(
            {
                f"{LEGACY_ASSET_URL}/one.png": FakeResponse(shared),
                f"{LEGACY_ASSET_URL}/two.png": FakeResponse(shared),
            }
        )
        resolver = build_resolver(session)
        first = resolver(f"{LEGACY_ASSET_URL}/one.png", "")
        second = resolver(f"{LEGACY_ASSET_URL}/two.png", "")
        self.assertEqual(first, second)
        self.assertEqual(resolver.fetched, 1)
        self.assertEqual(resolver.reused, 1)

    def test_summary_counts_every_outcome(self):
        session = FakeSession()
        resolver = build_resolver(session)
        resolver(f"{LEGACY_ASSET_URL}/b.png", "")
        resolver("relative.png", "")
        self.assertEqual(
            resolver.get_summary(),
            "1 image(s) fetched, 0 reused from the library, 1 left as raw HTML",
        )


if __name__ == "__main__":
    unittest.main()
