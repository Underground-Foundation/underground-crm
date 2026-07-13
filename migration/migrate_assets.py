#!/usr/bin/env python3
"""
Upload legacy assets (images and documents) referenced in the fetched HTML
pages to Cloudflare R2, and optionally rewrite the pages to point at the new
location.

The legacy site linked its own uploaded files, and shared theme/framework
assets, from one or more separate asset hosts (LEGACY_ASSET_URLS), e.g.:

  <img src="https://assets.yourlegacycrm.example.com/your-site-slug/pages/4106/attachments/original/1720696911/photo.jpg?1720696911">
  <a href="https://assets.yourlegacycrm.example.com/your-site-slug/pages/3623/attachments/original/1761520197/submission.pdf?1761520197">

Each such URL is downloaded and uploaded to R2 once — keyed by the legacy
attachment ID, since bare filenames are not unique across pages — then, with
--replace, every src/href/content attribute pointing at it is rewritten to
the object's public R2 URL.

CSS/JS/SCSS references are handled differently: they're already served by the
rebuilt theme, not migrated to R2, so with --replace the whole tag is instead
wrapped in an HTML comment to neutralise it. Any other asset type (calendar
invites, stray .dat files, etc.) is left untouched and logged at WARNING:
migrating it isn't implemented yet.

Usage:
  python -m migration.migrate_assets                # scan and upload only
  python -m migration.migrate_assets --replace       # also rewrite src/href
  python -m migration.migrate_assets --dry-run        # report only, touch nothing

Reads LEGACY_ASSET_URLS, LEGACY_USER_AGENT, CLOUDFLARE_ASSETS_ENDPOINT,
CLOUDFLARE_ASSETS_BUCKET and CLOUDFLARE_PUBLIC_ASSET_URL from the environment
(see ../.env). R2 credentials come from the AWS_PROFILE environment variable,
via the usual ~/.aws/credentials file.
"""

import argparse
import logging
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import boto3
import botocore.exceptions
from bs4 import BeautifulSoup

from .config import (
    CLOUDFLARE_ASSETS_BUCKET,
    CLOUDFLARE_ASSETS_ENDPOINT,
    CLOUDFLARE_PUBLIC_ASSET_URL,
    LEGACY_ASSET_URLS,
    LEGACY_USER_AGENT,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("migrate_assets")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SITE_DIR = REPO_ROOT / "fusionparty.org.au"

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "svg", "dat"}
DOCUMENT_EXTENSIONS = {"pdf", "ics"}
# The migration of these assets should have been handled during the recreation of the theme
HTML_EXTENSIONS = {"css", "js", "scss"}
KNOWN_EXTENSIONS = IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS | HTML_EXTENSIONS

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


def iter_asset_attrs(soup: BeautifulSoup):
    """
    Every (tag, attribute name, url) whose src/href/content starts with one of
    LEGACY_ASSET_URLS.

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
            if isinstance(value, str) and value.startswith(LEGACY_ASSET_URLS):
                yield tag, attr, value


def parse_asset_url(url: str) -> tuple[str, str, str]:
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


def r2_key(unique_id: str, filename: str, extension: str) -> str:
    """Where an asset lands in the bucket. See parse_asset_url for why unique_id is needed."""
    kind = "images" if extension in IMAGE_EXTENSIONS else "documents"
    return f"{kind}/{unique_id}_{filename}"


def public_url(key: str) -> str:
    return f"{CLOUDFLARE_PUBLIC_ASSET_URL}/{key}"


def download_asset(url: str, attempts: int = NETWORK_ATTEMPTS) -> tuple[int, bytes]:
    """Fetch one asset's bytes, retrying on network-level failures only."""
    request = urllib.request.Request(url, headers={"User-Agent": LEGACY_USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == attempts:
                raise
            backoff = NETWORK_RETRY_BACKOFF * 2 ** (attempt - 1)
            logger.warning(
                "%s: %s. Retrying in %.0fs (attempt %d of %d).",
                url,
                error,
                backoff,
                attempt + 1,
                attempts,
            )
            time.sleep(backoff)
    raise AssertionError("unreachable: the loop either returns or raises")


def object_exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=CLOUDFLARE_ASSETS_BUCKET, Key=key)
        return True
    except botocore.exceptions.ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise


def upload_asset(s3, key: str, url: str, dry_run: bool) -> bool:
    """Download url and upload it to R2 under key, unless it's already there. Returns success."""
    if object_exists(s3, key):
        logger.info("already in R2, skipping download: %s", key)
        return True

    if dry_run:
        logger.info("[dry-run] would download and upload: %s -> %s", url, key)
        return True

    try:
        status, content = download_asset(url)
    except OSError as error:
        logger.error("%s: unreachable, not uploaded: %s", url, error)
        return False
    if status != 200:
        logger.error("%s: HTTP %s, not uploaded", url, status)
        return False

    content_type, _ = mimetypes.guess_type(key)
    s3.put_object(
        Bucket=CLOUDFLARE_ASSETS_BUCKET,
        Key=key,
        Body=content,
        ContentType=content_type or "application/octet-stream",
    )
    logger.info("uploaded: %s -> %s (%d bytes)", url, key, len(content))
    return True


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


def tag_source_span(html: str, tag) -> str | None:
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


def process_file(
    path: Path, site_dir: Path, s3, uploaded: dict[str, bool], replace: bool, dry_run: bool
) -> dict:
    """Scan one HTML file for legacy assets, upload any new ones, and optionally rewrite it."""
    display_path = path.relative_to(site_dir)
    original_html = path.read_text(encoding="utf-8")
    html = original_html
    soup = BeautifulSoup(html, "html.parser")

    stats = {"images": 0, "documents": 0, "commented": 0, "other": 0}
    # str.replace rewrites every occurrence of a given snippet in one pass, so a
    # snippet repeated on the same page (e.g. the same tag or attribute value
    # appearing twice) must only be replaced once.
    replaced_pairs: set[tuple[str, str]] = set()
    commented_tags: set[str] = set()
    for tag, attr, url in iter_asset_attrs(soup):
        unique_id, filename, extension = parse_asset_url(url)

        if extension in HTML_EXTENSIONS:
            # These are already served by the rebuilt theme, not migrated to R2 —
            # the old reference is just neutralised so it stops pointing at the
            # legacy site.
            stats["commented"] += 1
            if dry_run:
                logger.info("[dry-run] would comment out legacy %s tag: %s", extension, url)
            elif replace:
                # Read from original_html, not the (possibly already-mutated) html:
                # tag.sourcepos/sourceline were computed against the text soup was
                # built from, so they'd point at the wrong place once an earlier
                # replacement has shifted the string.
                tag_html = tag_source_span(original_html, tag)
                if tag_html is None:
                    logger.warning(
                        "%s: could not locate source markup for legacy %s tag: %s",
                        display_path,
                        extension,
                        url,
                    )
                elif tag_html not in commented_tags:
                    html = comment_out_tag(html, tag_html)
                    commented_tags.add(tag_html)
            continue

        if extension not in KNOWN_EXTENSIONS:
            logger.warning(
                "%s: unmigrated asset type .%s at %s=%r",
                display_path,
                extension or "(none)",
                attr,
                url,
            )
            stats["other"] += 1
            continue

        stats["images" if extension in IMAGE_EXTENSIONS else "documents"] += 1
        key = r2_key(unique_id, filename, extension)

        if key not in uploaded:
            uploaded[key] = upload_asset(s3, key, url, dry_run)

        if replace and not dry_run and uploaded[key] and (attr, url) not in replaced_pairs:
            html = replace_attr_value(html, attr, url, public_url(key))
            replaced_pairs.add((attr, url))

    if html != original_html:
        path.write_text(html, encoding="utf-8")
        logger.info("rewrote: %s", display_path)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload legacy imagery and PDFs referenced in fetched HTML pages to R2."
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        default=DEFAULT_SITE_DIR,
        help=f"Directory of fetched HTML files to scan (default: {DEFAULT_SITE_DIR}).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Rewrite each migrated asset's src/href to its new R2 URL in the local HTML files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be uploaded/replaced without uploading or writing anything.",
    )
    args = parser.parse_args()

    if not args.site_dir.is_dir():
        parser.error(f"{args.site_dir} is not a directory")

    s3 = boto3.client("s3", endpoint_url=CLOUDFLARE_ASSETS_ENDPOINT)

    html_files = sorted(args.site_dir.rglob("*.html"))
    logger.info("Scanning %d HTML files under %s", len(html_files), args.site_dir)

    uploaded: dict[str, bool] = {}
    totals = {"images": 0, "documents": 0, "commented": 0, "other": 0}
    for index, path in enumerate(html_files, start=1):
        stats = process_file(path, args.site_dir, s3, uploaded, args.replace, args.dry_run)
        for kind, count in stats.items():
            totals[kind] += count
        if any(stats.values()):
            logger.info(
                "[%d/%d] %s: %d image(s), %d document(s), %d commented out, %d other",
                index,
                len(html_files),
                path.relative_to(args.site_dir),
                stats["images"],
                stats["documents"],
                stats["commented"],
                stats["other"],
            )

    failed = sum(1 for ok in uploaded.values() if not ok)
    logger.info(
        "\nDone. %d image reference(s), %d document reference(s), %d commented-out reference(s), "
        "%d other (unmigrated) reference(s). %d unique asset(s) uploaded or confirmed present, "
        "%d failed.",
        totals["images"],
        totals["documents"],
        totals["commented"],
        totals["other"],
        len(uploaded) - failed,
        failed,
    )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
