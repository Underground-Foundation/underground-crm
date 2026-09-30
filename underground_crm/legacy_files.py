"""
Internalizing files referenced by legacy pages into a Wagtail library.

The library is Wagtail's own: images (legacy_images.py) and documents
(legacy_documents.py) are each a model with a file in the media storage. A page
that refers to a legacy asset host has to have the file fetched once and
registered before it can point at a library file instead.

A source is only worth fetching when it's prefixed by one of LEGACY_ASSET_URLS
or has "/uploads/" in its path -- and never when its host is one of
SATISFACTORY_IMAGE_DOMAINS, which overrides both of those (a file already served
from our own domain has nothing to fetch or duplicate). A relative source is
always internalized, exempt from all three checks, since it was only ever
relative to the legacy page itself.

A file that is already in our own media storage is adopted rather than copied:
the storage is asked for it (not the network), and the library gets a record
pointing at the file that is already there. That covers a source served from the
storage's own URL, and a download that turns out to be byte-for-byte the file
already stored under the name an upload would get. (The storage alone cannot make
that call: with file_overwrite off it only checks that the name is taken, and
would file the upload again under a suffixed name.)

The resolvers are deliberately forgiving. Anything they cannot or should not
fetch returns None, and the caller keeps the original reference. A broken
picture or link is not worth failing an import over.
"""

import hashlib
import logging
import os
from pathlib import PurePosixPath
from typing import Optional, cast
from urllib.parse import unquote, urljoin, urlparse

import requests

logger = logging.getLogger(__name__)


def legacy_asset_urls_from_env() -> tuple:
    """
    The base URLs that will trigger internalisation of files, when the source is
    *absolute*. This is defined in LEGACY_ASSET_URLS. Any absolute paths using
    other prefixes are left as they are. A *relative* source was only ever
    relative to the legacy page itself, so it's always internalized.
    """
    raw = os.environ.get("LEGACY_ASSET_URLS", "")
    return tuple(url.strip().rstrip("/") for url in raw.split(",") if url.strip())


def satisfactory_image_domains_from_env() -> tuple:
    """
    Domains, defined in SATISFACTORY_IMAGE_DOMAINS, that our own site's files
    are already properly served from. An absolute source already pointing at
    one of these is left exactly as it is rather than internalized -- it's
    already ours, so there is nothing to fetch and duplicate into the library.
    This takes priority over LEGACY_ASSET_URLS and the "/uploads/" rule above.
    """
    raw = os.environ.get("SATISFACTORY_IMAGE_DOMAINS", "")
    return tuple(domain.strip().lower() for domain in raw.split(",") if domain.strip())


# Long enough for a slow legacy asset host, short enough that a dead one does
# not hold up an import of hundreds of pages.
DEFAULT_TIMEOUT = 30

# Wagtail's own limit on a title.
_TITLE_LIMIT = 255


class RemoteFileResolver:
    """
    Fetches remote files and returns the primary key of the library record
    holding each one. What kind of file, and what its record is, is for a
    subclass to say: `kind`, `_model`, `_skip_reason`, `_accepts` and `_build`.

    Downloads are deduplicated twice over: once per run by source URL, and
    once against the library by content hash, so re-importing a page — or
    importing two pages that share a file — reuses the record already stored
    rather than filling the library with copies.
    """

    kind = "file"
    # How the summary describes what it did not bring into the library.
    left_as = "left as they were"

    def __init__(
        self,
        base_url: str = "",
        legacy_asset_urls: Optional[tuple] = None,
        satisfactory_image_domains: Optional[tuple] = None,
        timeout: int = DEFAULT_TIMEOUT,
        stdout=None,
    ):
        self.base_url = base_url
        self.legacy_asset_urls = (
            legacy_asset_urls if legacy_asset_urls is not None else legacy_asset_urls_from_env()
        )
        self.satisfactory_image_domains = (
            satisfactory_image_domains
            if satisfactory_image_domains is not None
            else satisfactory_image_domains_from_env()
        )
        self.timeout = timeout
        self.stdout = stdout
        self.session = requests.Session()
        self._resolved: dict = {}
        self.fetched = 0
        self.reused = 0
        self.adopted = 0
        self.skipped = 0

    # --- What a subclass says -------------------------------------------------

    def _model(self):
        """The model of the library records."""
        raise NotImplementedError

    def _skip_reason(self, filename: str) -> Optional[str]:
        """Why a file with this name is not worth fetching, or None."""
        return None

    def _accepts(self, content_type: str) -> bool:
        """Whether a response of this content type (which may be empty) is the kind of file."""
        return True

    def _build(self, model, content: bytes, filename: str, title_hint: str):
        """
        The unsaved record for the downloaded bytes, with `file` set to an upload
        of them. Raises if the bytes are not a file of this kind.
        """
        raise NotImplementedError

    # --- Resolving ---------------------------------------------------------------

    def __call__(
        self, source: str, title_hint: str = "", filename: Optional[str] = None
    ) -> Optional[object]:
        """
        The primary key of the record for `source`, or None. `title_hint` is
        what the source's author called it (an image's alt text, a link's text);
        `filename` names the stored file when it should not be taken from the URL.
        """
        if source in self._resolved:
            return self._resolved[source]
        pk = self._resolve(source, title_hint, filename)
        self._resolved[source] = pk
        return pk

    def _report(self, message: str) -> None:
        message = f"    [{self.kind}] {message}"
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

    def _resolve(
        self, source: str, title_hint: str, filename: Optional[str] = None
    ) -> Optional[object]:
        url = self._absolute(source)
        if url is None:
            self.skipped += 1
            self._report(f"skipped '{source[:80]}': not an absolute http(s) URL.")
            return None

        stored_name = self._stored_name(url)
        if stored_name is not None:
            return self._resolve_stored(stored_name, title_hint)

        # A source with no scheme of its own ("image.png") was only ever
        # relative to the legacy body it came from, needs to be internalized,
        # just like a source with the explicit legacy asset URL.
        was_relative = not urlparse(source).scheme
        if not was_relative:
            host = urlparse(url).netloc.split(":")[0].lower()
            if self._matches_domain(host, self.satisfactory_image_domains):
                self.skipped += 1
                self._report(f"skipped '{url}': already served from our own domain.")
                return None
            if not (url.startswith(self.legacy_asset_urls) or "/uploads/" in urlparse(url).path):
                self.skipped += 1
                self._report(f"skipped '{url}': not a legacy asset URL.")
                return None

        filename = filename or self._filename(url)
        reason = cast(Optional[str], self._skip_reason(filename))
        if reason:
            self.skipped += 1
            self._report(f"skipped '{filename}': {reason}.")
            return None

        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as error:
            self.skipped += 1
            self._report(f"could not fetch '{url}': {error}")
            return None

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if not self._accepts(content_type):
            self.skipped += 1
            self._report(f"skipped '{url}': served as '{content_type}'.")
            return None

        content = response.content
        digest = hashlib.sha1(content).hexdigest()

        model = self._model()
        existing = model.objects.filter(file_hash=digest).first()
        if existing is not None:
            self.reused += 1
            return existing.pk

        try:
            record = self._create(model, content, digest, filename, title_hint)
        except Exception as error:  # pylint: disable=broad-except
            # Pillow raises a variety of errors for a truncated or mislabelled
            # file, and none of them should end the import.
            self.skipped += 1
            self._report(f"could not store '{url}': {error}")
            return None

        self.fetched += 1
        self._report(f"stored '{record.title}' as {self.kind} {record.pk}.")
        return record.pk

    @staticmethod
    def _matches_domain(host: str, domains: tuple) -> bool:
        """True when `host` is one of `domains`, or a subdomain of one."""
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)

    @staticmethod
    def _filename(url: str) -> str:
        """
        The file name to use for storing the file, taken from the URL's path and
        stripped of the cache-busting query that legacy asset URLs might carry
        ("...~/banner.jpg?1760233316").
        """
        name = unquote(PurePosixPath(urlparse(url).path).name) or "file"
        return name[:100]

    @staticmethod
    def _title(filename: str, title_hint: str) -> str:
        """
        A human-readable title for the library: what the source's author called
        the file when they said, otherwise the file name turned back into words.
        """
        if title_hint:
            return title_hint[:_TITLE_LIMIT]
        stem = PurePosixPath(filename).stem.replace("_", " ").replace("-", " ").strip()
        return (stem or filename)[:_TITLE_LIMIT]

    # --- Files already in the media storage ----------------------------------------

    @staticmethod
    def _file_storage(model):
        """The storage the model keeps its files in, or None for a stand-in model."""
        meta = getattr(model, "_meta", None)
        return meta.get_field("file").storage if meta is not None else None

    def _stored_name(self, url: str) -> Optional[str]:
        """
        The name of the file in the storage that `url` serves, or None when
        `url` is not one of the storage's own.
        """
        storage = self._file_storage(self._model())
        if storage is None:
            return None
        prefix = storage.url("x")[:-1]
        if self.base_url:
            # A storage on the local disk serves relative URLs.
            prefix = urljoin(self.base_url, prefix)
        if not urlparse(prefix).scheme or not url.startswith(prefix):
            return None
        return unquote(urlparse(url[len(prefix) :]).path) or None

    def _resolve_stored(self, name: str, title_hint: str) -> Optional[object]:
        """The record for a file already in the storage, made without fetching or storing anything."""
        model = self._model()
        storage = self._file_storage(model)
        existing = model.objects.filter(file=name).first()
        if existing is not None:
            self.reused += 1
            return existing.pk
        if not storage.exists(name):
            self.skipped += 1
            self._report(f"skipped '{name}': not in the media storage.")
            return None
        with storage.open(name) as stored:
            content = stored.read()
        digest = hashlib.sha1(content).hexdigest()
        existing = model.objects.filter(file_hash=digest).first()
        if existing is not None:
            self.reused += 1
            return existing.pk
        try:
            record = self._create(
                model, content, digest, PurePosixPath(name).name, title_hint, stored_name=name
            )
        except Exception as error:  # pylint: disable=broad-except
            self.skipped += 1
            self._report(f"could not read '{name}': {error}")
            return None
        self.adopted += 1
        self._report(f"adopted '{name}' as {self.kind} {record.pk}.")
        return record.pk

    @staticmethod
    def _holds(storage, name: str, digest: str) -> bool:
        """Whether the storage already has a file called `name` with exactly this content."""
        if not storage.exists(name):
            return False
        with storage.open(name) as stored:
            return hashlib.sha1(stored.read()).hexdigest() == digest

    def _create(
        self,
        model,
        content: bytes,
        digest: str,
        filename: str,
        title_hint: str,
        stored_name: Optional[str] = None,
    ):
        """
        Store the downloaded bytes as a library record, unless the storage already
        holds them (as `stored_name`, or under the name an upload would be given),
        in which case the record just points at that file.
        """
        record = self._build(model, content, filename, title_hint)
        # Set by Wagtail lazily on first access otherwise, which would mean
        # re-reading every file from storage to deduplicate the next import.
        record.file_hash = digest
        record.file_size = len(content)
        storage = self._file_storage(model)
        if storage is not None:
            if stored_name is None:
                upload_name = record.file.field.generate_filename(record, filename)
                if self._holds(storage, upload_name, digest):
                    stored_name = upload_name
            if stored_name is not None:
                # A name, not an upload: Django takes it as a file that is already stored.
                record.file = stored_name
        record.save()
        return record

    def get_summary(self) -> str:
        return (
            f"{self.fetched} {self.kind}(s) fetched, {self.reused} reused from the library, "
            f"{self.adopted} adopted from the media storage, {self.skipped} {self.left_as}"
        )
