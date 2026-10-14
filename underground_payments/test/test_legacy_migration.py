"""
Tests for parsing the legacy CRM's recurring donation plans listing and its financial
transactions CSV export, and the CSV of recurring donations exported from its Stripe
account.
"""

import datetime
import tempfile
import zoneinfo
from pathlib import Path

from django.test import SimpleTestCase

from underground_payments.legacy_migration import (
    CSV_AMOUNT_CENTS_COLUMN,
    CSV_EMAIL_COLUMN,
    CSV_FIRST_NAME_COLUMN,
    CSV_LAST_NAME_COLUMN,
    CSV_LEGACY_DONATION_ID_COLUMN,
    CSV_LEGACY_PERSON_ID_COLUMN,
    CSV_RECURRING_STATUS_COLUMN,
    CSV_STATUS_COLUMN,
    CSV_SUCCEEDED_AT_COLUMN,
    CSV_TRANSACTION_KEY_COLUMN,
    RECURRING_CSV_AMOUNT_COLUMN,
    RECURRING_CSV_CUSTOMER_ID_COLUMN,
    RECURRING_CSV_FREQUENCY_COLUMN,
    RECURRING_CSV_LEGACY_PLAN_ID_COLUMN,
    RECURRING_CSV_NEXT_PAYMENT_COLUMN,
    RECURRING_CSV_PAYMENT_METHOD_ID_COLUMN,
    RECURRING_CSV_SUBSCRIPTION_ID_COLUMN,
    ExportedRecurringPlan,
    LegacyDonation,
    LegacyDonor,
    NotAListingError,
    RecurringPlan,
    fix_encoding,
    next_page_path,
    parse_frequency,
    parse_recurring_plans,
    read_donations,
    read_donors,
    read_exported_recurring_plans,
)

# The first page of the listing, saved from the legacy CRM's admin on 13 October 2026.
SAMPLE_PAGE = Path(__file__).parent / "recurring_plans_sample.html"
# The saved page's pagination reads "1 - 30 of 72".
SAMPLE_PAGE_PLAN_COUNT = 30
SAMPLE_PAGE_NEXT_PATH = "/admin/donations/recurring?page=2"

# A donor giving $2.20 a month, as shown in the saved page's row for them.
MONTHLY_PLAN = RecurringPlan(
    legacy_plan_id=2118,
    legacy_person_id=37776,
    donor_name="Riley Hutchinson",
    status="active",
    amount_cents=220,
    interval="month",
    next_bill_date=datetime.date(2026, 10, 15),
)
# A canceled plan, which the saved page shows with an empty next bill date.
CANCELED_PLAN_ID = 2547


class RecurringPlansListingTests(SimpleTestCase):
    def setUp(self):
        self.html = SAMPLE_PAGE.read_bytes()

    def test_reads_every_row_of_the_page(self):
        plans = parse_recurring_plans(self.html)
        self.assertEqual(
            len(plans),
            SAMPLE_PAGE_PLAN_COUNT,
            "The saved page's own pagination says it lists this many plans.",
        )

    def test_reads_a_monthly_plan(self):
        plans = {plan.legacy_plan_id: plan for plan in parse_recurring_plans(self.html)}
        self.assertEqual(plans[MONTHLY_PLAN.legacy_plan_id], MONTHLY_PLAN)

    def test_canceled_plan_has_no_next_bill_date(self):
        plans = {plan.legacy_plan_id: plan for plan in parse_recurring_plans(self.html)}
        self.assertEqual(plans[CANCELED_PLAN_ID].status, "canceled")
        self.assertIsNone(plans[CANCELED_PLAN_ID].next_bill_date)

    def test_finds_the_next_page(self):
        self.assertEqual(next_page_path(self.html), SAMPLE_PAGE_NEXT_PATH)

    def test_last_page_has_no_next_page(self):
        # On the last page, the listing disables its "next" link rather than removing it.
        last_page = """
            <div class="pagination">
              <div class="pagination-results"><strong>61</strong> - <strong>72</strong> of <strong>72</strong></div>
              <a class="btn btn-nav btn-nav--rounded" rel="prev" href="/admin/donations/recurring?page=2"></a>
              <a class="btn btn-nav btn-nav--rounded disabled" disabled="true" rel="next" href="false"></a>
            </div>
        """
        self.assertIsNone(next_page_path(last_page))

    def test_login_page_is_not_a_listing(self):
        # What the admin shows instead of the listing once the session has expired.
        login_page = """
            <html><head><title>Sign in</title></head>
            <body><form action="/forms/user_sessions" method="post">
              <input type="email" name="user_session[email]">
              <input type="password" name="user_session[password]">
            </form></body></html>
        """
        with self.assertRaises(NotAListingError):
            parse_recurring_plans(login_page)


CSV_HEADER = [
    "nationbuilder_id",
    "amount",
    CSV_LEGACY_PERSON_ID_COLUMN,
    CSV_FIRST_NAME_COLUMN,
    CSV_LAST_NAME_COLUMN,
    CSV_EMAIL_COLUMN,
]
REPEAT_DONOR = LegacyDonor(
    legacy_person_id=40112,
    email="bjorn.andersson@example.org",
    first_name="Björn",
    last_name="Andersson",
)
ONE_OFF_DONOR = LegacyDonor(
    legacy_person_id=40587,
    email="priya.raman@example.org",
    first_name="Priya",
    last_name="Raman",
)


def csv_line(*values: str) -> str:
    return ",".join(values) + "\n"


class ReadDonorsTests(SimpleTestCase):
    def write_csv(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "financial_transactions.csv"
        # The export is UTF-8, which read_donors reads as Latin-1 and then repairs.
        path.write_bytes(text.encode("utf-8"))
        return path

    def donor_line(self, transaction_id: str, amount: str, donor: LegacyDonor) -> str:
        return csv_line(
            transaction_id,
            amount,
            str(donor.legacy_person_id),
            donor.first_name,
            donor.last_name,
            donor.email,
        )

    def test_each_donor_appears_once_with_their_name_repaired(self):
        path = self.write_csv(
            csv_line(*CSV_HEADER)
            + self.donor_line("18157", "$20.00", REPEAT_DONOR)
            + self.donor_line("18160", "$50.00", ONE_OFF_DONOR)
            + self.donor_line("18923", "$20.00", REPEAT_DONOR)
        )
        donors, skipped_row_count = read_donors(path)
        self.assertEqual(donors, [REPEAT_DONOR, ONE_OFF_DONOR])
        self.assertEqual(skipped_row_count, 0, "Every row names a donor ID and an email.")

    def test_skips_rows_without_an_email(self):
        anonymous_donor = LegacyDonor(
            legacy_person_id=40630, email="", first_name="Tom", last_name="Nguyen"
        )
        path = self.write_csv(
            csv_line(*CSV_HEADER)
            + self.donor_line("18161", "$10.00", ONE_OFF_DONOR)
            + self.donor_line("18162", "$10.00", anonymous_donor)
        )
        donors, skipped_row_count = read_donors(path)
        self.assertEqual(donors, [ONE_OFF_DONOR])
        self.assertEqual(skipped_row_count, 1, "Only Tom Nguyen's row has no email.")


MELBOURNE = zoneinfo.ZoneInfo("Australia/Melbourne")
DONATION_CSV_HEADER = [
    CSV_LEGACY_DONATION_ID_COLUMN,
    CSV_AMOUNT_CENTS_COLUMN,
    CSV_STATUS_COLUMN,
    CSV_SUCCEEDED_AT_COLUMN,
    CSV_RECURRING_STATUS_COLUMN,
    CSV_LEGACY_PERSON_ID_COLUMN,
    CSV_EMAIL_COLUMN,
    CSV_TRANSACTION_KEY_COLUMN,
]


class ReadDonationsTests(SimpleTestCase):
    def write_csv(self, *lines: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "financial_transactions.csv"
        path.write_bytes((csv_line(*DONATION_CSV_HEADER) + "".join(lines)).encode("utf-8"))
        return path

    def test_reads_a_monthly_payment(self):
        expected = LegacyDonation(
            legacy_donation_id=41873,
            legacy_person_id=REPEAT_DONOR.legacy_person_id,
            email=REPEAT_DONOR.email,
            status="succeeded",
            amount_cents=220,
            succeeded_at=datetime.datetime(2026, 9, 15, 9, 5, tzinfo=MELBOURNE),
            is_recurring=True,
            # Columns that this CSV leaves out.
            page_slug="",
            tracking_code="",
            payment_type="",
            merchant_account="",
            transaction_key="py_1SAb3K2EiAKqJzItR7w2Lm9c",
        )
        path = self.write_csv(
            csv_line(
                str(expected.legacy_donation_id),
                str(expected.amount_cents),
                expected.status,
                # The export pads a single-digit hour with a second space.
                "09/15/2026  9:05 AM",
                # The status of the recurring plan that the payment belongs to.
                "active",
                str(expected.legacy_person_id),
                expected.email,
                expected.transaction_key,
            )
        )
        donations, skipped_row_count = read_donations(path, MELBOURNE)
        self.assertEqual(donations, [expected])
        self.assertEqual(skipped_row_count, 0, "The only row has an ID and an amount.")

    def test_reads_a_failed_one_off_payment_by_nobody(self):
        # Some declined card payments are recorded against no person at all.
        path = self.write_csv(csv_line("41874", "5000", "failed", "", "", "", "", ""))
        [donation] = read_donations(path, MELBOURNE)[0]
        self.assertIsNone(donation.legacy_person_id)
        self.assertIsNone(donation.succeeded_at)
        self.assertFalse(donation.is_recurring, "The row names no recurring plan status.")
        self.assertEqual(donation.status, "failed")


class FixEncodingTests(SimpleTestCase):
    def test_repairs_utf8_read_as_latin1(self):
        self.assertEqual(fix_encoding("BjÃ¶rn"), "Björn")

    def test_leaves_correct_text_alone(self):
        self.assertEqual(fix_encoding("Björn"), "Björn")


# The export's header, in its own order, including the columns that the import ignores.
RECURRING_CSV_HEADER = [
    RECURRING_CSV_CUSTOMER_ID_COLUMN,
    "customer_name",
    RECURRING_CSV_PAYMENT_METHOD_ID_COLUMN,
    "payment_method_name",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
    RECURRING_CSV_SUBSCRIPTION_ID_COLUMN,
    RECURRING_CSV_LEGACY_PLAN_ID_COLUMN,
    RECURRING_CSV_AMOUNT_COLUMN,
    RECURRING_CSV_FREQUENCY_COLUMN,
    "start_date",
    "last_payment_date",
    RECURRING_CSV_NEXT_PAYMENT_COLUMN,
]
# Riley Hutchinson's $2.20 a month, the same plan as MONTHLY_PLAN, as the export shows it.
# Its next payment, at 07:51 UTC, falls on the date that the admin listing shows.
EXPORTED_MONTHLY_PLAN = ExportedRecurringPlan(
    legacy_plan_id=MONTHLY_PLAN.legacy_plan_id,
    customer_id="cus_Tbk1RileyHutch",
    payment_method_id="pm_1SeWXmRileyHutchinson00",
    legacy_subscription_id="sub_1SeWXpRileyHutchinson0",
    amount_cents=MONTHLY_PLAN.amount_cents,
    interval="month",
    interval_count=1,
    next_payment_at=datetime.datetime(2026, 10, 15, 7, 51, 21, tzinfo=datetime.timezone.utc),
)


def exported_plan_line(plan_id: str, amount: str, frequency: str, next_payment: str) -> str:
    plan = EXPORTED_MONTHLY_PLAN
    return csv_line(
        plan.customer_id,
        "",
        plan.payment_method_id,
        "",
        '"56 Grainger Circuit, Melba"',
        "",
        "Canberra",
        "",
        "2615",
        plan.legacy_subscription_id,
        plan_id,
        amount,
        frequency,
        "2025-12-15 07:50:11.222649",
        "2026-09-15 08:51:21",
        next_payment,
    )


class ReadExportedRecurringPlansTests(SimpleTestCase):
    def write_csv(self, *lines: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "recurring_donations_migration.csv"
        path.write_text(csv_line(*RECURRING_CSV_HEADER) + "".join(lines), encoding="utf-8")
        return path

    def test_reads_a_monthly_plan(self):
        path = self.write_csv(
            exported_plan_line(
                str(MONTHLY_PLAN.legacy_plan_id), "2.2", "every 1 month", "2026-10-15 07:51:21"
            )
        )
        self.assertEqual(read_exported_recurring_plans(path), ([EXPORTED_MONTHLY_PLAN], []))

    def test_reports_an_unreadable_row_and_reads_the_rest(self):
        path = self.write_csv(
            exported_plan_line("2119", "", "every 1 month", "2026-10-16 01:37:10"),
            exported_plan_line(
                str(MONTHLY_PLAN.legacy_plan_id), "2.2", "every 1 month", "2026-10-15 07:51:21"
            ),
        )
        plans, problems = read_exported_recurring_plans(path)
        self.assertEqual(plans, [EXPORTED_MONTHLY_PLAN])
        self.assertEqual(len(problems), 1, "Only plan 2119's row, with no amount, is unreadable.")
        self.assertTrue(problems[0].startswith("line 2:"), "The header is line 1.")

    def test_rejects_a_fraction_of_a_cent(self):
        path = self.write_csv(
            exported_plan_line("2120", "2.205", "every 1 month", "2026-10-15 07:51:21")
        )
        plans, problems = read_exported_recurring_plans(path)
        self.assertEqual(plans, [])
        self.assertEqual(len(problems), 1, "$2.205 cannot be charged to a card.")


class ParseFrequencyTests(SimpleTestCase):
    def test_monthly(self):
        self.assertEqual(parse_frequency("every 1 month"), ("month", 1))

    def test_quarterly(self):
        self.assertEqual(parse_frequency("every 3 months"), ("month", 3))

    def test_unrecognized(self):
        self.assertEqual(parse_frequency("fortnightly"), (None, 0))
