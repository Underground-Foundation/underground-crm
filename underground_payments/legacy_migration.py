"""
Parsing for the legacy CRM data that the donation migration commands work from:
the financial transactions CSV export (for migrate_donors) and the admin listing of
recurring donation plans (for fetch_recurring_donations and
migrate_recurring_donations).

Pure Python and bs4, with no Django or Stripe import, so that it can be tested
without a database or network.
"""

import csv
import datetime
import re
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Optional, Union

from bs4 import BeautifulSoup, Tag

# ---- Financial transactions CSV ----

# Columns of the legacy CRM's financial transactions export that identify the donor.
CSV_LEGACY_PERSON_ID_COLUMN = "signup_nationbuilder_id"
CSV_EMAIL_COLUMN = "signup_email"
CSV_FIRST_NAME_COLUMN = "signup_first_name"
CSV_LAST_NAME_COLUMN = "signup_last_name"


@dataclass(frozen=True)
class LegacyDonor:
    legacy_person_id: int
    email: str
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        return " ".join(name for name in (self.first_name, self.last_name) if name)


def fix_encoding(text: str) -> str:
    """
    Repair a string whose UTF-8 bytes were misread as Latin-1.

    The legacy CRM exports UTF-8, but Microsoft tools (Excel, etc.) may have re-saved
    the file as Windows-1252/Latin-1 without converting it, so that "ö" (UTF-8 bytes
    0xC3 0xB6) reads back as the two Latin-1 characters "Ã¶".

    If the string contains no mojibake (because it is pure ASCII, or because the file
    was already read correctly), the encode/decode round trip raises an exception and
    the original string is returned unchanged.
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def read_transaction_rows(path: Union[str, Path]) -> Iterator[dict[str, str]]:
    """Yield the rows of a financial transactions CSV, with their encoding repaired."""
    # Open as Latin-1 so that no bytes are lost before they are repaired.
    with open(path, encoding="latin-1", newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            yield {
                key: fix_encoding(value) if isinstance(value, str) else value
                for key, value in row.items()
            }


def read_donors(path: Union[str, Path]) -> tuple[list[LegacyDonor], int]:
    """
    The distinct donors named by a financial transactions CSV, in order of first
    appearance, together with the number of rows skipped because they name no donor
    ID or no email address.
    """
    donors: dict[int, LegacyDonor] = {}
    skipped_row_count = 0
    for row in read_transaction_rows(path):
        legacy_person_id = row.get(CSV_LEGACY_PERSON_ID_COLUMN, "").strip()
        email = row.get(CSV_EMAIL_COLUMN, "").strip()
        if not legacy_person_id.isdigit() or not email:
            skipped_row_count += 1
            continue
        donors.setdefault(
            int(legacy_person_id),
            LegacyDonor(
                legacy_person_id=int(legacy_person_id),
                email=email,
                first_name=row.get(CSV_FIRST_NAME_COLUMN, "").strip(),
                last_name=row.get(CSV_LAST_NAME_COLUMN, "").strip(),
            ),
        )
    return list(donors.values()), skipped_row_count


# ---- Recurring donation plans listing ----

ACTIVE_STATUS = "active"

# A plan's own link, e.g. /admin/signups/37776/recurring_donations/2118
_PLAN_LINK_PATTERN = re.compile(r"/signups/(?P<person>\d+)/recurring_donations/(?P<plan>\d+)$")
# The plan's description, e.g. "$2.20 per month"
_INTERVAL_PATTERN = re.compile(r"\bper (?P<interval>day|week|month|year)$")
_NEXT_BILL_DATE_FORMAT = "%B %d, %Y"


class NotAListingError(ValueError):
    """The HTML is not a listing of recurring plans, e.g. a login page after the session expired."""


@dataclass(frozen=True)
class RecurringPlan:
    legacy_plan_id: int
    legacy_person_id: int
    donor_name: str
    status: str
    amount_cents: int
    # Stripe's name for the billing interval ("day", "week", "month" or "year"), or
    # None if the description names none of them.
    interval: Optional[str]
    # None for plans that will never bill again (canceled or never started).
    next_bill_date: Optional[datetime.date]


def _parse_amount_cents(text: str) -> int:
    return int(Decimal(text.replace("$", "").replace(",", "").strip()) * 100)


def _parse_row(cells_by_heading: dict[str, Tag]) -> RecurringPlan:
    amount_cell = cells_by_heading["Amount"]
    plan_link = amount_cell.find("a", href=True)
    if plan_link is None:
        raise ValueError(f"Recurring plan row has no link: {amount_cell}")
    match = _PLAN_LINK_PATTERN.search(urllib.parse.urlparse(str(plan_link["href"])).path)
    if match is None:
        raise ValueError(f"Unrecognized recurring plan link: {plan_link['href']}")
    interval_match = _INTERVAL_PATTERN.search(
        cells_by_heading["Description"].get_text(" ", strip=True)
    )
    next_bill_text = cells_by_heading["Next bill date"].get_text(strip=True)
    return RecurringPlan(
        legacy_plan_id=int(match["plan"]),
        legacy_person_id=int(match["person"]),
        donor_name=cells_by_heading["Name"].get_text(" ", strip=True),
        status=cells_by_heading["Status"].get_text(strip=True),
        amount_cents=_parse_amount_cents(amount_cell.get_text(strip=True)),
        interval=interval_match["interval"] if interval_match else None,
        next_bill_date=(
            datetime.datetime.strptime(next_bill_text, _NEXT_BILL_DATE_FORMAT).date()
            if next_bill_text
            else None
        ),
    )


def parse_recurring_plans(html: Union[str, bytes]) -> list[RecurringPlan]:
    """
    The plans listed on one page of the legacy CRM's recurring donations listing.

    Cells are found by their column heading rather than their position, so a
    reordered table still parses.
    """
    table = BeautifulSoup(html, "html.parser").find("table", class_="data-table")
    if table is None:
        raise NotAListingError("No table of recurring plans found.")
    rows = table.find_all("tr")
    headings = [cell.get_text(strip=True) for cell in rows[0].find_all(["th", "td"])]
    plans = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if cells:
            plans.append(_parse_row(dict(zip(headings, cells))))
    return plans


def next_page_path(html: Union[str, bytes]) -> Optional[str]:
    """The path of the listing's next page, or None on its last page."""
    link = BeautifulSoup(html, "html.parser").find("a", rel="next", href=True)
    if link is None or link.get("disabled"):
        return None
    return str(link["href"])
