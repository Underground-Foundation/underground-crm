"""
Migrating the assets referenced by a legacy site's pages.

The legacy site linked its own uploaded files, and shared theme/framework
assets, from one or more separate asset hosts (LEGACY_ASSET_URLS), e.g.:

  <img src="https://assets.legacycrm.example.com/site-slug/pages/4106/attachments/original/1720696911/photo.jpg?1720696911">
  <a href="https://assets.legacycrm.example.com/site-slug/pages/3623/attachments/original/1761520197/submission.pdf?1761520197">

Each such URL found in a page is migrated once, then (with `replace`)
every src/href/content attribute pointing at it is rewritten to where it now
lives:

- An image Wagtail can hold (png, jpg, gif, webp, …) becomes a Wagtail image,
  through the same RemoteImageResolver that the page import uses, so the image
  library, its deduplication and the media storage are all Wagtail's own. The
  page then refers to the image file's URL, which is also the address the page
  import recognizes as an image it already has (see legacy_images.py).
- A document (a PDF) becomes a Wagtail document in the same way, through a
  RemoteDocumentResolver (see legacy_documents.py), and the link is pointed at
  the stored file.
- Anything else the image library cannot hold — an SVG — is handed to an
  `upload_asset` function, which puts it somewhere it will be served from and
  says where. The default puts it in the media storage; then a deployment
  whose files are served from elsewhere will pass its own.

CSS/JS/SCSS references are handled differently: they're already served by the
rebuilt theme, so the whole tag is instead wrapped in an HTML comment to
neutralize it. Any other asset type (calendar invitations, stray .dat files, etc.)
is left untouched and logged at WARNING: migrating it isn't implemented yet.

Used by the migrate_assets management command.
"""

import logging
import mimetypes
import re
import urllib.parse
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from wagtail.images import get_image_model
from wagtail.images.fields import get_allowed_image_extensions

from underground_crm.legacy_documents import DOCUMENT_EXTENSIONS

logger = logging.getLogger(__name__)

# Images that Wagtail's image library cannot hold, so they are handed to the
# uploader instead. (".dat" is what some legacy image attachments are named.)
FILE_IMAGE_EXTENSIONS = {"svg", "svgz", "dat"}
# The migration of these assets should have been handled during the recreation of the theme
HTML_EXTENSIONS = {"css", "js", "scss"}

# The folder Wagtail's image model uploads into, within the media storage.
IMAGE_FOLDER = "original_images"

# Elements with no closing tag, per the HTML spec — commenting one out only needs its
# opening tag; anything else needs its matching closing tag included too.
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

# A network blip says nothing about the asset, so retry it; a bad HTTP status
# is the site's answer and retrying won't change it.
NETWORK_ATTEMPTS = 4
NETWORK_RETRY_BACKOFF = 2.0  # seconds, doubling on each further attempt
TIMEOUT = 30

# Puts a file that the image library cannot hold where it will be served from, and
# returns the URL it is served at. Given the file's name within the media folders
# ("original_images/4106_diagram.svg"), a function returning its bytes — called only
# when the file is not already there — and its content type. May raise; that
# fails the one asset.
AssetUploader = Callable[[str, Callable[[], bytes], str], str]


def store_in_media(name: str, fetch: Callable[[], bytes], content_type: str) -> str:
    """The default AssetUploader: the media storage. A file already there is left as it is."""
    storage = default_storage
    if not storage.exists(name):
        name = storage.save(name, ContentFile(fetch()))
    return storage.url(name)


def build_legacy_session(user_agent: str = "") -> requests.Session:
    """A session for the legacy asset hosts, retrying network failures (not HTTP errors)."""
    session = requests.Session()
    if user_agent:
        session.headers["User-Agent"] = user_agent
    retry = Retry(
        total=NETWORK_ATTEMPTS - 1,
        backoff_factor=NETWORK_RETRY_BACKOFF,
        status_forcelist=(),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def iter_asset_attrs(soup: BeautifulSoup, asset_urls: Tuple[str, ...]) -> Iterator[tuple]:
    """
    Every (tag, attribute name, url) whose src/href/content starts with one of `asset_urls`.

    content covers <meta property="og:image" content="..."> and
    <meta name="twitter:image" content="...">, which carry the URL in a third
    place besides the usual src/href — easy to miss since most tags don't use
    "content" as a URL-bearing attribute at all.
    """
    for tag in soup.find_all(True):
        for attr in ("src", "href", "content"):
            value = tag.get(attr)
            # These are never multi-valued attributes, so bs4 always gives a plain
            # str here in practice; the isinstance check just makes that explicit for
            # the type checker (Tag.get()'s declared return type also covers a list,
            # for genuinely multi-valued attributes like class).
            if isinstance(value, str) and value.startswith(asset_urls):
                yield tag, attr, value


def parse_asset_url(url: str) -> Tuple[str, str, str]:
    """
    Split a legacy asset URL into (unique_id, filename, extension).

    Legacy asset URLs come in a few shapes:
      .../pages/<page_id>/attachments/original/<attachment_id>/<filename>
      .../mailings/<mailing_id>/attachments/original/<filename>
      .../pages/<page_id>/meta_images/original/<filename>
      .../sites/<site_id>/{meta_images,favicon_images}/original/<filename>

    Only the first shape carries a numeric attachment ID, which is already
    unique across the whole legacy site. The others have no per-file ID at
    all — the legacy CRM just reuses the owning resource's ID — so unique_id
    falls back to "<resource_type><resource_id>" (e.g. "pages6426") in that
    case, since plain filenames like "20221130.jpg" recur across unrelated
    pages and mailings.

    Shared theme/framework assets (e.g. ".../assets/liquid-<hash>.js", sitting
    directly under the host with no resource segments at all) are shallower
    still — too shallow for "<resource_type><resource_id>" to even apply — so
    unique_id falls back further, to everything but the filename.
    """
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    segments = path.strip("/").split("/")
    filename = segments[-1]
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if segments[-2].isdigit():
        unique_id = segments[-2]
    elif len(segments) >= 3:
        # segments[0] is the site slug already embedded in LEGACY_ASSET_URLS; the
        # resource type and its ID are the two segments right after it.
        resource_type, resource_id = segments[1], segments[2]
        unique_id = f"{resource_type}{resource_id}"
    else:
        unique_id = "".join(segments[:-1]) or "root"

    return unique_id, filename, extension


def valid_filename(name: str) -> str:
    """
    `name` as Django's get_valid_filename gives it (which Wagtail applies to every
    upload): spaces become underscores, and anything but letters, digits, dashes,
    underscores and dots is dropped. Done here so that the name is already the one
    the CMS would use, and adopting the file does not rename it.
    """
    return re.sub(r"(?u)[^-\w.]", "", name.strip().replace(" ", "_"))


def asset_filename(unique_id: str, filename: str) -> str:
    """The name an asset is kept under. See parse_asset_url for why unique_id is needed."""
    return valid_filename(f"{unique_id}_{filename}")


def replace_attr_value(html: str, attr: str, old_url: str, new_url: str) -> str:
    """
    Replace attr="old_url" (or attr='old_url', or bare attr=old_url — HTML allows
    an unquoted attribute value with no whitespace, and some fetched pages use it)
    with attr="new_url".
    """
    escaped_attr = re.escape(attr)
    escaped_old = re.escape(old_url)
    pattern = re.compile(
        rf'{escaped_attr}=(?:"{escaped_old}"|\'{escaped_old}\'|{escaped_old}(?=[\s/>]))'
    )
    new_snippet = f'{attr}="{new_url}"'
    if not pattern.search(html):
        logger.warning("could not find %s=%s to replace (unexpected quoting?)", attr, old_url)
        return html
    return pattern.sub(lambda _match: new_snippet, html)


def tag_source_span(html: str, tag) -> Optional[str]:
    """
    A tag's exact original markup, read from html at its parsed source position.

    bs4's own serialization (str(tag)) is not a safe substitute: it can reorder
    attributes and adds a self-closing slash to void elements regardless of
    whether the source had one, so it does not reliably match the original text.
    """
    if tag.sourceline is None or tag.sourcepos is None:
        return None
    lines = html.split("\n")
    start = sum(len(line) + 1 for line in lines[: tag.sourceline - 1]) + tag.sourcepos

    # Walk forward to the end of the opening tag, skipping '>' inside quoted
    # attribute values.
    pos = start
    quote = None
    while pos < len(html):
        char = html[pos]
        if quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == ">":
            pos += 1
            break
        pos += 1
    else:
        return None

    if tag.name in VOID_ELEMENTS:
        return html[start:pos]

    closing_tag = f"</{tag.name}>"
    end = html.find(closing_tag, pos)
    if end == -1:
        return None
    return html[start : end + len(closing_tag)]


def comment_out_tag(html: str, tag_html: str) -> str:
    """Neutralise a tag by wrapping its exact source markup in an HTML comment."""
    if tag_html not in html:
        logger.warning("could not find tag to comment out (unexpected formatting?): %s", tag_html)
        return html
    return html.replace(tag_html, f"<!-- {tag_html} -->")


class LegacyAssetMigrator:
    """
    Brings the assets of fetched legacy pages across (see the module docstring).

    `image_resolver` and `document_resolver` (a RemoteImageResolver and a
    RemoteDocumentResolver) do the fetching for images and documents; `session`
    fetches the rest. `dry_run` reports what would happen and touches nothing;
    `replace` rewrites the pages to the new addresses.
    """

    def __init__(
        self,
        image_resolver,
        document_resolver,
        asset_urls: Tuple[str, ...],
        upload_asset: AssetUploader = store_in_media,
        session: Optional[requests.Session] = None,
        replace: bool = False,
        dry_run: bool = False,
    ):
        self.image_resolver = image_resolver
        self.document_resolver = document_resolver
        self.asset_urls = asset_urls
        self.upload_asset = upload_asset
        self.session = session or requests.Session()
        self.replace = replace
        self.dry_run = dry_run
        # Where each legacy URL now lives; None for one that could not be brought across.
        self.migrated: Dict[str, Optional[str]] = {}

    @property
    def failed(self) -> int:
        return sum(1 for new_url in self.migrated.values() if new_url is None)

    def _migrate_image(self, url: str, alt: str, filename: str) -> Optional[str]:
        image_id = self.image_resolver(url, alt, filename)
        if image_id is None:
            logger.error("%s: could not be made into an image (see above)", url)
            return None
        return get_image_model().objects.get(pk=image_id).file.url

    def _migrate_document(self, url: str, title: str, filename: str) -> Optional[str]:
        document_id = self.document_resolver(url, title, filename)
        new_url = self.document_resolver.url_of(document_id) if document_id is not None else None
        if new_url is None:
            logger.error("%s: could not be made into a document (see above)", url)
        return new_url

    def _migrate_file(self, url: str, name: str) -> Optional[str]:
        def fetch() -> bytes:
            response = self.session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            return response.content

        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            return self.upload_asset(name, fetch, content_type)
        except Exception as error:  # pylint: disable=broad-except
            logger.error("%s: not uploaded: %s", url, error)
            return None

    def migrate(self, url: str, title: str = "") -> Optional[str]:
        """
        Bring one asset across, once, and return where it now lives (None if it
        failed). `title` is what the page called it: an image's alt text, or the
        text of a link.
        """
        if url in self.migrated:
            return self.migrated[url]
        unique_id, filename, extension = parse_asset_url(url)
        stored_filename = asset_filename(unique_id, filename)

        if self.dry_run:
            logger.info("[dry-run] would bring across: %s -> %s", url, stored_filename)
            new_url = url
        elif extension in get_allowed_image_extensions():
            new_url = self._migrate_image(url, title, stored_filename)
        elif extension in DOCUMENT_EXTENSIONS:
            new_url = self._migrate_document(url, title, stored_filename)
        else:
            new_url = self._migrate_file(url, f"{IMAGE_FOLDER}/{stored_filename}")
        self.migrated[url] = new_url
        return new_url

    @staticmethod
    def _title_of(tag) -> str:
        """What the page called the asset: an image's alt text, or the text of the link to it."""
        if tag.name == "img":
            return tag.get("alt", "")
        return " ".join(tag.get_text().split()) if tag.name == "a" else ""

    def process_html(self, html: str, label: str) -> Tuple[str, Dict[str, int]]:
        """
        Bring across every legacy asset in `html`, and return it (rewritten, if
        `replace`) with counts of what it referenced.
        """
        original_html = html
        soup = BeautifulSoup(html, "html.parser")
        stats = {"images": 0, "documents": 0, "commented": 0, "other": 0}
        # str.replace rewrites every occurrence of a given snippet in one pass, so a
        # snippet repeated on the same page (e.g. the same tag or attribute value
        # appearing twice) must only be replaced once.
        replaced_pairs: Set[Tuple[str, str]] = set()
        commented_tags: Set[str] = set()
        for tag, attr, url in iter_asset_attrs(soup, self.asset_urls):
            _unique_id, _filename, extension = parse_asset_url(url)

            if extension in HTML_EXTENSIONS:
                # These are already served by the rebuilt theme — the old reference is
                # just neutralized so it stops pointing at the legacy site.
                stats["commented"] += 1
                if self.dry_run:
                    logger.info("[dry-run] would comment out legacy %s tag: %s", extension, url)
                elif self.replace:
                    # Read from the original HTML, not the (possibly already-mutated) one:
                    # tag.sourcepos/sourceline were computed against the text soup was
                    # built from, so they'd point at the wrong place once an earlier
                    # replacement has shifted the string.
                    tag_html = tag_source_span(original_html, tag)
                    if tag_html is None:
                        logger.warning(
                            "%s: could not locate source markup for legacy %s tag: %s",
                            label,
                            extension,
                            url,
                        )
                    elif tag_html not in commented_tags:
                        html = comment_out_tag(html, tag_html)
                        commented_tags.add(tag_html)
                continue

            is_image = extension in get_allowed_image_extensions() or (
                extension in FILE_IMAGE_EXTENSIONS
            )
            if not is_image and extension not in DOCUMENT_EXTENSIONS:
                logger.warning(
                    "%s: unmigrated asset type .%s at %s=%r",
                    label,
                    extension or "(none)",
                    attr,
                    url,
                )
                stats["other"] += 1
                continue

            stats["images" if is_image else "documents"] += 1
            new_url = self.migrate(url, title=self._title_of(tag))
            if (
                self.replace
                and not self.dry_run
                and new_url is not None
                and (attr, url) not in replaced_pairs
            ):
                html = replace_attr_value(html, attr, url, new_url)
                replaced_pairs.add((attr, url))
        return html, stats

    def process_file(self, path: Path, site_dir: Path) -> Dict[str, int]:
        """Bring across the assets of one fetched page, rewriting the file if `replace`."""
        original_html = path.read_text(encoding="utf-8")
        html, stats = self.process_html(original_html, str(path.relative_to(site_dir)))
        if html != original_html:
            path.write_text(html, encoding="utf-8")
            logger.info("rewrote: %s", path.relative_to(site_dir))
        return stats
