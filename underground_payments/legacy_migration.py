"""
Parsing for the legacy CRM data that the donation migration commands work from:
the financial transactions CSV export (for migrate_donors and import_donations), the
admin listing of recurring donation plans (for fetch_recurring_donations and
migrate_recurring_donations), and the CSV of recurring donations that the legacy CRM's
staff export from its Stripe account (for import_recurring_donations_from_csv and
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


# Further columns, describing the transaction itself, for import_donations.
CSV_LEGACY_DONATION_ID_COLUMN = "nationbuilder_id"
CSV_AMOUNT_CENTS_COLUMN = "amount_in_cents"
CSV_STATUS_COLUMN = "status_name"
CSV_SUCCEEDED_AT_COLUMN = "succeeded_at"
CSV_RECURRING_STATUS_COLUMN = "recurring_donation_status"
CSV_PAGE_SLUG_COLUMN = "page_slug"
CSV_TRACKING_CODE_COLUMN = "tracking_code_slug"
CSV_PAYMENT_TYPE_COLUMN = "payment_type_name"
CSV_MERCHANT_ACCOUNT_COLUMN = "merchant_account_name"
CSV_TRANSACTION_KEY_COLUMN = "transaction_key"

# The status of a transaction whose money was received (and not refunded).
SUCCEEDED_STATUS = "succeeded"
# The export's timestamps are naive local times, e.g. "06/30/2025  3:05 PM", whose runs
# of spaces are collapsed before parsing.
_CSV_DATETIME_FORMAT = "%m/%d/%Y %I:%M %p"


@dataclass(frozen=True)
class LegacyDonation:
    legacy_donation_id: int
    # None for a transaction recorded against nobody, as some failed ones are.
    legacy_person_id: Optional[int]
    email: str
    status: str
    amount_cents: int
    succeeded_at: Optional[datetime.datetime]
    # Whether the transaction was a payment of a recurring plan.
    is_recurring: bool
    page_slug: str
    tracking_code: str
    payment_type: str
    # The legacy CRM's name for the account that received the money.
    merchant_account: str
    # The payment processor's ID for the payment (a Stripe charge in the legacy CRM's own
    # Stripe account, or a PayPal transaction, for example).
    transaction_key: str


def parse_csv_datetime(text: str, time_zone: datetime.tzinfo) -> Optional[datetime.datetime]:
    """An aware datetime from one of the export's naive local times, or None if blank."""
    text = " ".join(text.split())
    if not text:
        return None
    return datetime.datetime.strptime(text, _CSV_DATETIME_FORMAT).replace(tzinfo=time_zone)


def read_donations(
    path: Union[str, Path], time_zone: datetime.tzinfo
) -> tuple[list[LegacyDonation], int]:
    """
    Every transaction in a financial transactions CSV, whatever its status, together with
    the number of rows skipped because they have no transaction ID or amount.

    The export's naive timestamps are read as local times in time_zone.
    """
    donations = []
    skipped_row_count = 0
    for row in read_transaction_rows(path):
        legacy_donation_id = row.get(CSV_LEGACY_DONATION_ID_COLUMN, "").strip()
        amount_cents = row.get(CSV_AMOUNT_CENTS_COLUMN, "").strip()
        if not legacy_donation_id.isdigit() or not amount_cents.isdigit():
            skipped_row_count += 1
            continue
        legacy_person_id = row.get(CSV_LEGACY_PERSON_ID_COLUMN, "").strip()
        donations.append(
            LegacyDonation(
                legacy_donation_id=int(legacy_donation_id),
                legacy_person_id=int(legacy_person_id) if legacy_person_id.isdigit() else None,
                email=row.get(CSV_EMAIL_COLUMN, "").strip(),
                status=row.get(CSV_STATUS_COLUMN, "").strip(),
                amount_cents=int(amount_cents),
                succeeded_at=parse_csv_datetime(row.get(CSV_SUCCEEDED_AT_COLUMN, ""), time_zone),
                is_recurring=bool(row.get(CSV_RECURRING_STATUS_COLUMN, "").strip()),
                page_slug=row.get(CSV_PAGE_SLUG_COLUMN, "").strip(),
                tracking_code=row.get(CSV_TRACKING_CODE_COLUMN, "").strip(),
                payment_type=row.get(CSV_PAYMENT_TYPE_COLUMN, "").strip(),
                merchant_account=row.get(CSV_MERCHANT_ACCOUNT_COLUMN, "").strip(),
                transaction_key=row.get(CSV_TRANSACTION_KEY_COLUMN, "").strip(),
            )
        )
    return donations, skipped_row_count


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


# ---- Recurring donations CSV ----

# Columns of the recurring donations CSV, which the legacy CRM's staff export from the
# Stripe account that charges its donors. Its Customer and PaymentMethod IDs are kept
# when Stripe copies them into this site's account.
RECURRING_CSV_CUSTOMER_ID_COLUMN = "customer_id"
RECURRING_CSV_PAYMENT_METHOD_ID_COLUMN = "payment_method_id"
RECURRING_CSV_SUBSCRIPTION_ID_COLUMN = "subscription_id"
RECURRING_CSV_LEGACY_PLAN_ID_COLUMN = "nationbuilder_recurring_donation_id"
RECURRING_CSV_AMOUNT_COLUMN = "amount"
RECURRING_CSV_FREQUENCY_COLUMN = "frequency"
RECURRING_CSV_NEXT_PAYMENT_COLUMN = "next_payment_date"

# A plan's frequency, e.g. "every 1 month"
_FREQUENCY_PATTERN = re.compile(
    r"^every (?P<count>\d+) (?P<interval>day|week|month|year)s?$", re.IGNORECASE
)
# The CSV's timestamps are naive UTC times, with or without microseconds, e.g.
# "2026-10-15 07:51:21" or "2025-12-15 07:50:11.222649".
_RECURRING_CSV_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class ExportedRecurringPlan:
    # The same ID as RecurringPlan.legacy_plan_id, so that a plan migrated from either
    # source is recognized by the other.
    legacy_plan_id: int
    customer_id: str
    payment_method_id: str
    # The subscription in the legacy CRM's Stripe account, which is not copied.
    legacy_subscription_id: str
    amount_cents: int
    # Stripe's name for the billing interval, or None if the frequency names none.
    interval: Optional[str]
    interval_count: int
    # When the legacy CRM will next charge the donor, in UTC.
    next_payment_at: datetime.datetime


def parse_frequency(text: str) -> tuple[Optional[str], int]:
    """
    Stripe's billing interval and interval count for a frequency such as "every 1 month",
    or (None, 0) if it is not of that form.
    """
    match = _FREQUENCY_PATTERN.match(" ".join(text.split()))
    if match is None:
        return None, 0
    return match["interval"].lower(), int(match["count"])


def parse_recurring_csv_datetime(text: str) -> datetime.datetime:
    text = text.strip()
    for datetime_format in _RECURRING_CSV_DATETIME_FORMATS:
        try:
            parsed = datetime.datetime.strptime(text, datetime_format)
        except ValueError:
            continue
        return parsed.replace(tzinfo=datetime.timezone.utc)
    raise ValueError(f"Unrecognized timestamp: {text!r}")


def read_exported_recurring_plans(
    path: Union[str, Path],
) -> tuple[list[ExportedRecurringPlan], list[str]]:
    """
    Every plan in a recurring donations CSV, together with a description of each row
    that could not be read.
    """
    plans = []
    problems = []
    with open(path, encoding="utf-8-sig", newline="") as csv_file:
        # Row 1 is the header, so the first plan is on line 2 of the file.
        for line_number, row in enumerate(csv.DictReader(csv_file), start=2):
            try:
                interval, interval_count = parse_frequency(row[RECURRING_CSV_FREQUENCY_COLUMN])
                amount_cents = Decimal(row[RECURRING_CSV_AMOUNT_COLUMN].strip()) * 100
                if amount_cents != amount_cents.to_integral_value():
                    raise ValueError(f"{amount_cents} cents is not a whole number of cents")
                plans.append(
                    ExportedRecurringPlan(
                        legacy_plan_id=int(row[RECURRING_CSV_LEGACY_PLAN_ID_COLUMN]),
                        customer_id=row[RECURRING_CSV_CUSTOMER_ID_COLUMN].strip(),
                        payment_method_id=row[RECURRING_CSV_PAYMENT_METHOD_ID_COLUMN].strip(),
                        legacy_subscription_id=row[RECURRING_CSV_SUBSCRIPTION_ID_COLUMN].strip(),
                        amount_cents=int(amount_cents),
                        interval=interval,
                        interval_count=interval_count,
                        next_payment_at=parse_recurring_csv_datetime(
                            row[RECURRING_CSV_NEXT_PAYMENT_COLUMN]
                        ),
                    )
                )
            except (KeyError, ValueError, ArithmeticError) as exc:
                problems.append(f"line {line_number}: {exc!r}")
    return plans, problems
