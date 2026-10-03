import datetime
import email.utils
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from django.core.management.base import BaseCommand, CommandError

from underground_crm.management.commands.legacy_api_client import (
    FIRST_PAGE_NUMBER,
    fetch_all_page_html,
    fetch_page_html,
    fetch_page_json,
    get_api_headers,
    require_env,
)


# Statuses by which a site asks us to slow down, rather than telling us about the page.
THROTTLED_STATUSES = frozenset({429, 503})
THROTTLED_ATTEMPTS = 4
THROTTLED_RETRY_BACKOFF = 30.0  # seconds, doubling on each further attempt

DEFAULT_REQUEST_DELAY = 0.5


def _retry_delay(error: urllib.error.HTTPError, attempt: int) -> float:
    """
    How long to wait before asking again: the site's Retry-After header, given either as
    a number of seconds or as an HTTP date, and otherwise an exponential backoff.
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


class ThrottleAwareOpener:
    """
    Wraps a urllib opener so that a throttled response (HTTP 429, or 503) is waited out
    and asked for again, up to THROTTLED_ATTEMPTS times, before it is reported as an error.
    """

    def __init__(self, opener, log):
        self.opener = opener
        self.log = log

    def open(self, url):
        for attempt in range(1, THROTTLED_ATTEMPTS + 1):
            try:
                return self.opener.open(url)
            except urllib.error.HTTPError as error:
                if error.code not in THROTTLED_STATUSES or attempt == THROTTLED_ATTEMPTS:
                    raise
                backoff = _retry_delay(error, attempt)
                self.log(
                    f"  [throttled] {url}: HTTP {error.code}. Waiting {backoff:.0f}s "
                    f"(attempt {attempt + 1} of {THROTTLED_ATTEMPTS})."
                )
                time.sleep(backoff)
        raise AssertionError("unreachable: the loop above either returns or raises")


def _make_html_opener(cookie_file, user_agent):
    cookie_jar = http.cookiejar.MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener.addheaders = [("User-Agent", user_agent), ("Accept", "text/html")]
    return opener


class Command(BaseCommand):
    help = (
        "Fetch a legacy page's JSON and HTML from the legacy website: one page given by "
        "--slug, or every page listed in an all_pages.json given by --pages-file."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            required=True,
            help="Domain to fetch the rendered HTML from (e.g. example.com).",
        )
        which_pages = parser.add_mutually_exclusive_group(required=True)
        which_pages.add_argument(
            "--slug",
            help="Page slug to fetch.",
        )
        which_pages.add_argument(
            "--pages-file",
            type=Path,
            help=(
                "Fetch every page in this file, as written by build_page_graph "
                "(<domain>/all_pages.json). Each page's record is saved from the file "
                "without a further API request, and its HTML is fetched from the page's "
                "url_path. A page that is already saved is skipped, which is what lets an "
                "interrupted run resume."
            ),
        )
        parser.add_argument(
            "--with-pagination",
            action="store_true",
            help=(
                "Also fetch every further page of a paginated listing (a blog, say), "
                'found through the links in its <ul class="pagination">. They are '
                "saved as <slug>?page=<number>.html beside <slug>.html."
            ),
        )
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="With --pages-file, fetch pages again even if they are already saved.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="With --pages-file, the maximum number of pages to fetch in this run.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=DEFAULT_REQUEST_DELAY,
            help=(
                "With --pages-file, seconds to wait between pages, so that the site is not "
                f"overwhelmed (default: {DEFAULT_REQUEST_DELAY})."
            ),
        )

    def handle(self, *args, **options):
        domain = options["domain"]
        user_agent = require_env("LEGACY_USER_AGENT")
        cookie_file = require_env("LEGACY_ADMIN_COOKIE_FILE")
        html_opener = ThrottleAwareOpener(
            _make_html_opener(cookie_file, user_agent), self.stderr.write
        )

        output_dir = Path(domain)
        output_dir.mkdir(exist_ok=True)

        if options["pages_file"]:
            self._fetch_listed_pages(
                options["pages_file"],
                domain,
                output_dir,
                html_opener,
                with_pagination=options["with_pagination"],
                refresh=options["refresh"],
                limit=options["limit"],
                delay=options["delay"],
            )
            return

        slug = options["slug"]
        site_id = int(require_env("LEGACY_SITE_ID"))
        admin_url = require_env("LEGACY_ADMIN_URL").rstrip("/")
        api_headers = get_api_headers()

        self.stderr.write(f"Fetching JSON for slug '{slug}'...")
        record, error = fetch_page_json(slug, admin_url, api_headers=api_headers, site_id=site_id)
        if error:
            raise CommandError(error)

        self._save_page(
            record, slug, slug, domain, output_dir, html_opener, options["with_pagination"]
        )

    def _save_page(
        self,
        record: dict,
        slug: str,
        url_path: str,
        domain: str,
        output_dir: Path,
        html_opener,
        with_pagination: bool,
    ):
        """
        Save a page's record and rendered HTML. url_path is where the page is served,
        which differs from its slug for a page nested beneath another. Raises
        CommandError if the HTML cannot be fetched; the record is saved first.
        """
        json_path = output_dir / f"{slug}.json"
        json_path.write_text(json.dumps(record, indent=2))
        self.stderr.write(f"Saved {json_path}")

        self.stderr.write(f"Fetching HTML from https://{domain}/{url_path}...")
        if with_pagination:
            pages, error = fetch_all_page_html(domain, url_path, html_opener)
        else:
            html_bytes, error = fetch_page_html(domain, url_path, html_opener)
            pages = {FIRST_PAGE_NUMBER: html_bytes}
        if error:
            raise CommandError(error)

        for page_number, html_bytes in sorted(pages.items()):
            file_name = (
                f"{slug}.html"
                if page_number == FIRST_PAGE_NUMBER
                else f"{slug}?page={page_number}.html"
            )
            html_path = output_dir / file_name
            html_path.write_bytes(html_bytes)
            self.stderr.write(f"Saved {html_path}")

    def _fetch_listed_pages(
        self,
        pages_file: Path,
        domain: str,
        output_dir: Path,
        html_opener,
        with_pagination: bool,
        refresh: bool,
        limit: Optional[int],
        delay: float,
    ):
        try:
            records = json.loads(pages_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CommandError(f"Could not read '{pages_file}': {error}")
        if not isinstance(records, list):
            raise CommandError(f"'{pages_file}' does not hold a list of page records.")

        listed = []
        for record in records:
            attributes = record.get("attributes", {}) if isinstance(record, dict) else {}
            slug = attributes.get("slug")
            if not slug:
                self.stderr.write(f"[skip] a record with no slug: {str(record)[:100]}")
                continue
            # The page is served at its url_path, which is the slug only at the top level.
            url_path = (attributes.get("url_path") or f"/{slug}").strip("/")
            listed.append((record, slug, url_path))

        outstanding = [
            entry
            for entry in listed
            if refresh
            or not (
                (output_dir / f"{entry[1]}.json").exists()
                and (output_dir / f"{entry[1]}.html").exists()
            )
        ]
        batch = outstanding if limit is None else outstanding[:limit]
        self.stderr.write(
            f"{len(listed)} pages in {pages_file}; {len(listed) - len(outstanding)} already "
            f"saved; fetching {len(batch)} now."
        )

        failures = []
        for index, (record, slug, url_path) in enumerate(batch, start=1):
            if index > 1:
                time.sleep(delay)
            self.stderr.write(f"[{index}/{len(batch)}] {slug}")
            try:
                self._save_page(
                    record, slug, url_path, domain, output_dir, html_opener, with_pagination
                )
            except (CommandError, OSError) as error:
                # One page that cannot be fetched must not cost the pages that follow it.
                failures.append((slug, str(error)))
                self.stderr.write(f"  [failed] {slug}: {error}")

        remaining = len(outstanding) - len(batch)
        if remaining:
            self.stderr.write(
                f"{remaining} pages still outstanding. Re-run the same command to continue."
            )
        self.stderr.write(f"Done. {len(batch) - len(failures)} fetched, {len(failures)} failed.")
        for slug, error in failures:
            self.stderr.write(f"  {slug}: {error}")
