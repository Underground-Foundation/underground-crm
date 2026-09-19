"""
Internalizing images referenced by legacy pages into the Wagtail image library.

underground_crm/legacy_html.py rebuilds a legacy page out of Wagtail blocks,
and the Image block it wants to use stores an image chosen from the library —
not a remote URL. So each ``<img src="...">`` pointing at a legacy asset host,
encountered during a decomposition, has to be fetched once and registered as
a Wagtail image before the block can point at it.

The resolver is deliberately forgiving. Anything it cannot fetch — a source
that isn't a legacy asset, a dead host, an SVG Wagtail is not configured to
accept — returns None, and the decomposition keeps that one ``<img>`` in a
Raw HTML block pointing at the original URL. A broken picture is not worth
failing an import over.
"""

import hashlib
import logging
import os
from io import BytesIO
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

import requests
from django.core.files.images import ImageFile

logger = logging.getLogger(__name__)


def legacy_asset_urls_from_env() -> tuple:
    """
    The base URLs that will trigger internalisation of images, when the image is
    using an *absolute* <img src>. This is defined in LEGACY_ASSET_URLS.
    Any absolute paths using other prefixes are left as-is in a Raw HTML block
    (an Image block would involve internalizing the image). A *relative* src was
    only ever relative to the legacy page itself, so it's always internalized.
    """
    raw = os.environ.get("LEGACY_ASSET_URLS", "")
    return tuple(url.strip().rstrip("/") for url in raw.split(",") if url.strip())


# Wagtail rejects an upload whose extension is not in WAGTAILIMAGES_EXTENSIONS,
# which does not include SVG unless a project opts in. Rather than guess, these
# are left as raw HTML.
UNSUPPORTED_SUFFIXES = frozenset({".svg", ".svgz"})

# Long enough for a slow legacy asset host, short enough that a dead one does
# not hold up an import of hundreds of pages.
DEFAULT_TIMEOUT = 30

# Wagtail's own limit on Image.title.
_TITLE_LIMIT = 255


class RemoteImageResolver:
    """
    Fetches remote images and returns the primary key of the Wagtail image
    holding each one, for use as legacy_html's `image_resolver`.

    Downloads are deduplicated twice over: once per run by source URL, and
    once against the library by content hash, so re-importing a page — or
    importing two pages that share a banner — reuses the image already
    stored rather than filling the library with copies.
    """

    def __init__(
        self,
        base_url: str = "",
        legacy_asset_urls: Optional[tuple] = None,
        timeout: int = DEFAULT_TIMEOUT,
        stdout=None,
    ):
        self.base_url = base_url
        self.legacy_asset_urls = (
            legacy_asset_urls if legacy_asset_urls is not None else legacy_asset_urls_from_env()
        )
        self.timeout = timeout
        self.stdout = stdout
        self.session = requests.Session()
        self._resolved: dict = {}
        self.fetched = 0
        self.reused = 0
        self.skipped = 0

    def __call__(self, source: str, alt: str) -> Optional[object]:
        if source in self._resolved:
            return self._resolved[source]
        image_id = self._resolve(source, alt)
        self._resolved[source] = image_id
        return image_id

    def _report(self, message: str) -> None:
        if self.stdout:
            self.stdout.write(message)
        logger.info(message)

    def _absolute(self, source: str) -> Optional[str]:
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            return source
        if parsed.scheme:
            # data: URIs and anything else exotic.
            return None
        if not self.base_url:
            return None
        return urljoin(self.base_url, source)

    def _resolve(self, source: str, alt: str) -> Optional[object]:
        url = self._absolute(source)
        if url is None:
            self.skipped += 1
            self._report(f"    [image] skipped '{source[:80]}': not an absolute http(s) URL.")
            return None

        # A source with no scheme of its own ("image.png") was only ever
        # relative to the legacy body it came from, needs to be internalized,
        # just like an image with the explicit legacy asset URL.
        was_relative = not urlparse(source).scheme
        if not was_relative and not url.startswith(self.legacy_asset_urls):
            self.skipped += 1
            self._report(f"    [image] skipped '{url}': not a legacy asset URL.")
            return None

        filename = self._filename(url)
        if PurePosixPath(filename).suffix.lower() in UNSUPPORTED_SUFFIXES:
            self.skipped += 1
            self._report(f"    [image] skipped '{filename}': unsupported image type.")
            return None

        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as error:
            self.skipped += 1
            self._report(f"    [image] could not fetch '{url}': {error}")
            return None

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("image/"):
            self.skipped += 1
            self._report(f"    [image] skipped '{url}': served as '{content_type}'.")
            return None

        content = response.content
        digest = hashlib.sha1(content).hexdigest()

        image_model = self._image_model()
        existing = image_model.objects.filter(file_hash=digest).first()
        if existing is not None:
            self.reused += 1
            return existing.pk

        try:
            image = self._create_image(image_model, content, digest, filename, alt)
        except Exception as error:  # pylint: disable=broad-except
            # Pillow raises a variety of errors for a truncated or mislabelled
            # file, and none of them should end the import.
            self.skipped += 1
            self._report(f"    [image] could not store '{url}': {error}")
            return None

        self.fetched += 1
        self._report(f"    [image] stored '{image.title}' as image {image.pk}.")
        return image.pk

    @staticmethod
    def _image_model():
        from wagtail.images import get_image_model

        return get_image_model()

    @staticmethod
    def _filename(url: str) -> str:
        """
        The file name to use for storing the image, taken from the URL's path and
        stripped of the cache-busting query that legacy asset URLs might carry
        ("...~/banner.jpg?1760233316").
        """
        name = unquote(PurePosixPath(urlparse(url).path).name) or "image"
        return name[:100]

    @staticmethod
    def _title(filename: str, alt: str) -> str:
        """
        A human-readable title for the image library: the alt text when the
        original author wrote one, otherwise the file name turned back into
        words.
        """
        if alt:
            return alt[:_TITLE_LIMIT]
        stem = PurePosixPath(filename).stem.replace("_", " ").replace("-", " ").strip()
        return (stem or filename)[:_TITLE_LIMIT]

    def _create_image(self, image_model, content: bytes, digest: str, filename: str, alt: str):
        """
        Store the downloaded bytes as a Wagtail image.

        width and height are not editable and never filled in by save(), so
        they are read off the file here — Wagtail normally gets them from the
        upload form, which an import has no equivalent of.
        """
        uploaded = ImageFile(BytesIO(content), name=filename)
        if uploaded.width is None or uploaded.height is None:
            # Django reports unreadable image data by returning no dimensions
            # rather than raising, and Wagtail's width/height columns are not
            # nullable — so this has to be caught here or it becomes an
            # IntegrityError halfway through the import.
            raise ValueError("no image dimensions could be read")
        image = image_model(
            title=self._title(filename, alt),
            file=uploaded,
            width=uploaded.width,
            height=uploaded.height,
        )
        # Set by Wagtail lazily on first access otherwise, which would mean
        # re-reading every file from storage to deduplicate the next import.
        image.file_hash = digest
        image.file_size = len(content)
        image.save()
        return image

    def get_summary(self) -> str:
        return (
            f"{self.fetched} image(s) fetched, {self.reused} reused from the library, "
            f"{self.skipped} left as raw HTML"
        )
