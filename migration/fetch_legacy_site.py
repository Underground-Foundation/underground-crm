#!/usr/bin/env python3
"""
Fetch the legacy party website page by page, saving each page's HTML locally.

The pages to fetch are taken from the site's own sitemap, which robots.txt advertises,
and each one is then fetched over plain HTTP. The legacy site is server-rendered, so
this yields the same content a browser would show: rendering the pages in a real browser
(via a service such as Cloudflare's Browser Rendering API) adds nothing but the
advertising and analytics iframes that JavaScript injects, and such services cap how many
pages a single job may retrieve.

Runs are resumable. Each page is written as soon as it arrives, and a page already saved
locally is skipped, so an interrupted run can simply be repeated until the site is
complete. Pages are written to a directory named after the site (e.g. ./fusionparty.org.au/),
and are also emitted to stdout as newline-delimited JSON records.

The sitemap is what the site advertises to search engines, not an inventory of its CMS:
pages that are hidden, archived, or merely unlinked do not appear in it. Where a fuller
list is needed, --url-list takes one.

Usage:
  python -m migration.fetch_legacy_site                  # fetch the whole site, resuming
  python -m migration.fetch_legacy_site --limit 25       # fetch only the next 25 pages
  python -m migration.fetch_legacy_site --refresh        # re-fetch pages already saved
  python -m migration.fetch_legacy_site --url-list pages.json    # fetch a given list

Reads LEGACY_ADMIN_URL and LEGACY_USER_AGENT from the environment (see ../.env).
"""

import argparse
import datetime
import email.utils
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from .config import LEGACY_ADMIN_URL, LEGACY_USER_AGENT

# One request every half-second: brisk, but gentle enough that the site does not throttle
# us. Several hundred pages fetched back to back will earn an HTTP 429 whatever the delay,
# which is what THROTTLED_STATUSES below is for.
DEFAULT_REQUEST_DELAY = 0.5

# A network blip (DNS, a dropped connection) says nothing about the page, so retry it.
NETWORK_ATTEMPTS = 4
NETWORK_RETRY_BACKOFF = 2.0  # seconds, doubling on each further attempt

# Statuses by which a site asks us to slow down, rather than telling us about the page.
THROTTLED_STATUSES = frozenset({429, 503})
THROTTLED_RETRY_BACKOFF = 30.0  # seconds, doubling on each further attempt

# Saved pages land inside the repository, in a directory named after the site.
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---- Fetching ----


def retry_delay(error: urllib.error.HTTPError, attempt: int) -> float:
    """
    How long to wait before asking again, when the site has told us to slow down.

    A server that rate-limits us usually says when to come back, in a Retry-After header
    given either as a number of seconds or as an HTTP date. We honor whichever it sends,
    and otherwise back off exponentially.
    """
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        if retry_after.strip().isdigit():
            return float(retry_after.strip())
        resume_at = email.utils.parsedate_to_datetime(retry_after)
        if resume_at is not None:
            seconds = (resume_at - datetime.datetime.now(resume_at.tzinfo)).total_seconds()
            return max(seconds, 0.0)
    return THROTTLED_RETRY_BACKOFF * 2 ** (attempt - 1)


def fetch_page(url: str, attempts: int = NETWORK_ATTEMPTS) -> tuple[int, str]:
    """
    Fetch one URL over plain HTTP, identifying ourselves as LEGACY_USER_AGENT.

    Most HTTP error statuses are returned as they stand: they are the site's answer, and
    a page that is gone or forbidden will stay that way however often we ask. Two kinds
    of failure are worth retrying, because neither says anything about the page itself:

      * being throttled (429, or a 503 while the site catches its breath), which asks us
        to come back later and is a site's normal defence against a bulk fetch;
      * a network-level failure (DNS, a dropped connection).
    """
    request = urllib.request.Request(url, headers={"User-Agent": LEGACY_USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read().decode("utf-8", errors="replace")

        except urllib.error.HTTPError as error:
            if error.code not in THROTTLED_STATUSES or attempt == attempts:
                return error.code, error.read().decode("utf-8", errors="replace")
            backoff = retry_delay(error, attempt)
            print(
                f"  [throttled] {url}: HTTP {error.code}. Waiting {backoff:.0f}s "
                f"(attempt {attempt + 1} of {attempts}).",
                file=sys.stderr,
            )
            time.sleep(backoff)

        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == attempts:
                raise
            backoff = NETWORK_RETRY_BACKOFF * 2 ** (attempt - 1)
            print(
                f"  [retry] {url}: {error}. Trying again in {backoff:.0f}s "
                f"(attempt {attempt + 1} of {attempts}).",
                file=sys.stderr,
            )
            time.sleep(backoff)

    raise AssertionError("unreachable: the loop above either returns or raises")


def canonical_base(url: str) -> str:
    """
    The site's real base URL, following any redirect away from the URL we were given.

    LEGACY_ADMIN_URL names the legacy CRM's own domain, which redirects to the public
    website; the public website is what we want to fetch, and to name files after.
    """
    request = urllib.request.Request(url, headers={"User-Agent": LEGACY_USER_AGENT})
    with urllib.request.urlopen(request) as response:
        final = urllib.parse.urlparse(response.url)
    return f"{final.scheme}://{final.netloc}"


# ---- Deciding which pages to fetch ----


def sitemap_urls_from_robots(base: str) -> list[str]:
    """The sitemaps that the site's robots.txt advertises, if any."""
    status, body = fetch_page(urllib.parse.urljoin(base, "/robots.txt"))
    if status != 200:
        return []
    return re.findall(r"(?im)^\s*Sitemap:\s*(\S+)\s*$", body)


def page_urls_from_sitemaps(base: str) -> list[str]:
    """
    Every page URL the site lists, following sitemap indexes to the sitemaps they name.

    A <loc> inside a <sitemap> element points at another sitemap; a <loc> inside a <url>
    element points at a page. Rather than distinguish the two structurally, we follow
    anything that is itself a sitemap and treat everything else as a page.
    """
    pending = sitemap_urls_from_robots(base) or [urllib.parse.urljoin(base, "/sitemap.xml")]
    seen_sitemaps: set[str] = set()
    pages: dict[str, None] = {}  # an ordered set: sitemap order is meaningful

    while pending:
        sitemap_url = pending.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)

        status, body = fetch_page(sitemap_url)
        if status != 200:
            print(f"[warn] {sitemap_url} returned HTTP {status}; ignoring it.", file=sys.stderr)
            continue

        locations = re.findall(r"(?s)<loc>\s*([^<\s]+)\s*</loc>", body)
        print(f"  [sitemap] {sitemap_url}: {len(locations)} entries", file=sys.stderr)
        for location in locations:
            if re.search(r"(?i)sitemap[^/]*\.xml(\.gz)?$", location):
                pending.append(location)
            else:
                pages[location] = None

    return list(pages)


def page_urls_from_list(list_path: Path, base: str) -> list[str]:
    """
    Every page URL named by a file, for when the sitemap is not the list we want.

    Two shapes are accepted, distinguished by the file's first character:
      * JSON — an array of records, each carrying a url_path (either directly or under
        "attributes", as JSON:API nests it), such as an export of the legacy CMS's pages.
      * text — one URL or path per line, with '#' introducing a comment.

    A path is resolved against base; an absolute URL is taken as it stands.
    """
    raw = list_path.read_text(encoding="utf-8").strip()
    entries: list[str] = []

    if raw.startswith(("[", "{")):
        document = json.loads(raw)
        records = document if isinstance(document, list) else document.get("data", [])
        for record in records:
            attributes = record.get("attributes", record) if isinstance(record, dict) else {}
            url_path = attributes.get("url_path") or attributes.get("url")
            if url_path:
                entries.append(url_path)
    else:
        for line in raw.splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                entries.append(line)

    urls: dict[str, None] = {}  # an ordered set: the file's order is meaningful
    for entry in entries:
        urls[urllib.parse.urljoin(base + "/", entry)] = None
    return list(urls)


# ---- Saving pages locally ----


def local_path_for(url: str, output_dir: Path, site_host: str) -> Path:
    """
    Map a page's URL onto a file inside output_dir, mirroring the site's structure.

    Pages belonging to site_host sit directly in output_dir, because output_dir is
    already named after the site. Pages from any other host are grouped under a directory
    of their own, so that they cannot collide with the site's own pages.

    With site_host="www.example.org":
      https://www.example.org/       -> <output_dir>/index.html
      https://www.example.org/about  -> <output_dir>/about.html
      https://www.example.org/a/b/   -> <output_dir>/a/b/index.html
      https://other.example.net/x    -> <output_dir>/other.example.net/x.html
    """
    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(parsed.path)
    if path.endswith("/") or not path:
        path += "index"
    # A query string distinguishes otherwise identical paths, so keep it in the name.
    if parsed.query:
        path += "_" + urllib.parse.quote(parsed.query, safe="")

    segments = [segment for segment in path.split("/") if segment not in ("", ".", "..")]
    if parsed.netloc != site_host:
        segments.insert(0, parsed.netloc)

    target = output_dir.joinpath(*segments)
    if target.suffix.lower() not in (".html", ".htm"):
        target = target.with_name(target.name + ".html")
    return target


def fetch_site(
    base: str,
    output_dir: Path,
    limit: int | None,
    delay: float,
    refresh: bool,
    url_list: Path | None,
) -> list[dict]:
    """
    Fetch the site's pages, skipping any already saved, and save each one as it arrives.

    Saving page by page — rather than all at the end — is what makes a run resumable:
    whatever was retrieved before an interruption stays on disk, and re-running picks up
    from there. It also means a page we could not fetch is left for the next run, since
    nothing was written for it.
    """
    site_host = urllib.parse.urlparse(base).netloc
    if url_list:
        all_pages = page_urls_from_list(url_list, base)
        source_name = str(url_list)
    else:
        all_pages = page_urls_from_sitemaps(base)
        source_name = "the sitemap"

    outstanding = [
        url
        for url in all_pages
        if refresh or not local_path_for(url, output_dir, site_host).exists()
    ]
    already_saved = len(all_pages) - len(outstanding)
    batch = outstanding if limit is None else outstanding[:limit]

    print(
        f"{len(all_pages)} pages in {source_name}; {already_saved} already saved; "
        f"fetching {len(batch)} now.",
        file=sys.stderr,
    )

    records: list[dict] = []
    for index, url in enumerate(batch, start=1):
        if index > 1:
            time.sleep(delay)

        try:
            status, html = fetch_page(url)
        except OSError as error:
            # The network gave up on this page even after retrying. One unreachable page
            # must not cost us the several hundred that follow it.
            records.append({"url": url, "status": "errored", "error": str(error)})
            print(f"  [{index}/{len(batch)}] {url} -> unreachable: {error}", file=sys.stderr)
            continue

        record = {"url": url, "status": "completed" if status == 200 else "errored"}
        if status == 200:
            record["html"] = html
            target = local_path_for(url, output_dir, site_host)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            print(f"  [{index}/{len(batch)}] {url} -> {target}", file=sys.stderr)
        else:
            record["httpStatus"] = status
            print(f"  [{index}/{len(batch)}] {url} -> HTTP {status}", file=sys.stderr)
        records.append(record)

    remaining = len(outstanding) - len(batch)
    if remaining:
        print(
            f"\n{remaining} pages still outstanding. Re-run the same command to continue.",
            file=sys.stderr,
        )
    return records


def summarize(records: list[dict]) -> str:
    counts = Counter(record.get("status", "unknown") for record in records)
    return ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch the legacy website's pages and save each one's HTML locally."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Maximum number of pages to fetch in this run (default: all of them). A run "
            "picks up where the last one left off, so this batches a large site."
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY,
        help=(
            "Seconds to wait between requests to the site, so that we do not hammer it "
            f"(default: {DEFAULT_REQUEST_DELAY})."
        ),
    )
    parser.add_argument(
        "--url-list",
        type=Path,
        default=None,
        help=(
            "Fetch the pages named by this file rather than the ones the sitemap advertises. "
            "The file may be an export of the legacy CMS's page records (JSON, each with a "
            "url_path), or a plain list of one URL or path per line. The sitemap omits pages "
            "that are hidden, archived, or merely unlinked, so a CMS export lists more."
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Re-fetch pages that are already saved locally. Without this they are skipped, "
            "which is what lets an interrupted run resume."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write each page's HTML into, mirroring the site's URL structure. "
            "Defaults to a directory named after the site, inside this repository "
            "(e.g. ./fusionparty.org.au/). Records are written to stdout either way."
        ),
    )
    args = parser.parse_args()

    base = canonical_base(LEGACY_ADMIN_URL)
    site_host = urllib.parse.urlparse(base).netloc
    output_dir = args.output_dir or REPO_ROOT / site_host.removeprefix("www.")
    print(f"Fetching {base} into {output_dir}", file=sys.stderr)

    records = fetch_site(
        base,
        output_dir,
        limit=args.limit,
        delay=args.delay,
        refresh=args.refresh,
        url_list=args.url_list,
    )
    for record in records:
        print(json.dumps(record))

    print(f"\nDone. {len(records)} records: {summarize(records)}", file=sys.stderr)


if __name__ == "__main__":
    main()
