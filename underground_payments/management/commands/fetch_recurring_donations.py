"""
Save every page of the legacy CRM's admin listing of recurring donation plans, for
migrate_recurring_donations to read.

Usage:
    python manage.py fetch_recurring_donations --output-dir <path>

Requires an admin session cookie file (Netscape format) for the legacy CRM, exported
from the browser with a cookie export extension.

Reads LEGACY_ADMIN_URL, LEGACY_RECURRING_DONATIONS_PATH, LEGACY_USER_AGENT and
LEGACY_ADMIN_COOKIE_FILE from the environment (see .env.example). The cookie file path
can be overridden with --cookie-file.
"""

import urllib.parse
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from underground_crm.management.commands.fetch_pages import (
    ThrottleAwareOpener,
    _make_html_opener,
)
from underground_crm.management.commands.importing import require_env
from underground_payments.legacy_migration import (
    NotAListingError,
    next_page_path,
    parse_recurring_plans,
)


def page_file_name(page_number: int) -> str:
    return f"recurring_plans_page_{page_number}.html"


class Command(BaseCommand):
    help = "Save every page of the legacy CRM's listing of recurring donation plans."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            required=True,
            help="Directory to save the listing's pages in, one HTML file per page.",
        )
        parser.add_argument(
            "--cookie-file",
            default=None,
            help="Path to a Netscape-format cookie file for the legacy CRM. "
            "Defaults to LEGACY_ADMIN_COOKIE_FILE env var.",
        )

    def handle(self, *args, **options):
        admin_url = require_env("LEGACY_ADMIN_URL").rstrip("/")
        listing_path = require_env("LEGACY_RECURRING_DONATIONS_PATH")
        user_agent = require_env("LEGACY_USER_AGENT")
        cookie_file = options["cookie_file"] or require_env("LEGACY_ADMIN_COOKIE_FILE")
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        opener = ThrottleAwareOpener(_make_html_opener(cookie_file, user_agent), self.stderr.write)
        url = urllib.parse.urljoin(admin_url + "/", listing_path.lstrip("/"))
        page_number = 0
        plan_count = 0
        while url:
            page_number += 1
            self.stdout.write(f"  Fetching {url}")
            with opener.open(url) as response:
                html = response.read()
            try:
                plans = parse_recurring_plans(html)
            except NotAListingError:
                raise CommandError(
                    f"{url} is not a listing of recurring plans. "
                    "Has the admin session in the cookie file expired?"
                )
            plan_count += len(plans)
            (output_dir / page_file_name(page_number)).write_bytes(html)
            path = next_page_path(html)
            url = urllib.parse.urljoin(url, path) if path else None

        self.stdout.write(
            self.style.SUCCESS(
                f"Saved {page_number} page(s) listing {plan_count} plan(s) to {output_dir}."
            )
        )
