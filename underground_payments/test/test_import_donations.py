"""
Tests for import_donations, which records the legacy CRM's successful donations as
Donations, run against a small financial transactions CSV in the export's own format.
"""

import datetime
import io
import os
import tempfile
import zoneinfo
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from djmoney.money import Money

from underground_crm.models import Donation, Engagement, Person
from underground_payments.legacy_migration import (
    CSV_AMOUNT_CENTS_COLUMN,
    CSV_EMAIL_COLUMN,
    CSV_LEGACY_DONATION_ID_COLUMN,
    CSV_LEGACY_PERSON_ID_COLUMN,
    CSV_MERCHANT_ACCOUNT_COLUMN,
    CSV_PAGE_SLUG_COLUMN,
    CSV_PAYMENT_TYPE_COLUMN,
    CSV_RECURRING_STATUS_COLUMN,
    CSV_STATUS_COLUMN,
    CSV_SUCCEEDED_AT_COLUMN,
    CSV_TRACKING_CODE_COLUMN,
    CSV_TRANSACTION_KEY_COLUMN,
)
from underground_payments.management.commands.import_donations import (
    LEGACY_SOURCE_TIMEZONE_ENV_VAR,
)

TIME_ZONE_NAME = "Australia/Melbourne"
CSV_HEADER = [
    CSV_LEGACY_DONATION_ID_COLUMN,
    CSV_AMOUNT_CENTS_COLUMN,
    CSV_STATUS_COLUMN,
    CSV_SUCCEEDED_AT_COLUMN,
    CSV_RECURRING_STATUS_COLUMN,
    CSV_LEGACY_PERSON_ID_COLUMN,
    CSV_EMAIL_COLUMN,
    CSV_PAGE_SLUG_COLUMN,
    CSV_TRACKING_CODE_COLUMN,
    CSV_PAYMENT_TYPE_COLUMN,
    CSV_MERCHANT_ACCOUNT_COLUMN,
    CSV_TRANSACTION_KEY_COLUMN,
]

DONOR_LEGACY_ID = 40112
DONOR_EMAIL = "bjorn.andersson@example.org"

MONTHLY_PAYMENT_ID = 41873
MONTHLY_PAYMENT_CENTS = 220
MONTHLY_PAYMENT_TRANSACTION_KEY = "py_1SAb3K2EiAKqJzItR7w2Lm9c"
MONTHLY_PAYMENT_SUCCEEDED_AT = datetime.datetime(
    2026, 9, 15, 9, 5, tzinfo=zoneinfo.ZoneInfo(TIME_ZONE_NAME)
)
MONTHLY_PAYMENT = ",".join(
    [
        str(MONTHLY_PAYMENT_ID),
        str(MONTHLY_PAYMENT_CENTS),
        "succeeded",
        "09/15/2026  9:05 AM",
        "active",
        str(DONOR_LEGACY_ID),
        DONOR_EMAIL,
        "donate",
        "fusion_general",
        "Credit/Debit Card",
        "Fusion General",
        MONTHLY_PAYMENT_TRANSACTION_KEY,
    ]
)
# The same donor's attempts that kept no money.
DECLINED_PAYMENT = (
    f"41874,5000,failed,,,{DONOR_LEGACY_ID},{DONOR_EMAIL},donate,,Credit/Debit Card,"
    "Fusion General,"
)
PENDING_PAYMENT = (
    f"41875,5000,pending,,,{DONOR_LEGACY_ID},{DONOR_EMAIL},donate,,Credit/Debit Card,"
    "Fusion General,"
)
REFUNDED_PAYMENT = (
    f"41876,2600,refunded,,canceled,{DONOR_LEGACY_ID},{DONOR_EMAIL},donate,,"
    "Credit/Debit Card,Fusion General,pyr_1Qx4Tz2EiAKqJzItK3s8Vb0n"
)


class ImportDonationsTests(TestCase):
    def setUp(self):
        time_zone = mock.patch.dict(os.environ, {LEGACY_SOURCE_TIMEZONE_ENV_VAR: TIME_ZONE_NAME})
        time_zone.start()
        self.addCleanup(time_zone.stop)

    def write_csv(self, *rows: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "financial_transactions.csv"
        path.write_bytes("\n".join([",".join(CSV_HEADER), *rows, ""]).encode("utf-8"))
        return path

    def run_command(self, path: Path, *args: str) -> tuple[str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        call_command(
            "import_donations", "--csv-file", str(path), *args, stdout=stdout, stderr=stderr
        )
        return stdout.getvalue(), stderr.getvalue()

    def test_imports_a_monthly_payment_for_the_person_with_its_legacy_id(self):
        person = Person.objects.create(email=DONOR_EMAIL, legacy_id=DONOR_LEGACY_ID)
        self.run_command(self.write_csv(MONTHLY_PAYMENT))

        donation = Donation.objects.get(legacy_id=MONTHLY_PAYMENT_ID)
        self.assertEqual(donation.person, person)
        self.assertEqual(donation.amount, Money(Decimal(MONTHLY_PAYMENT_CENTS) / 100, "AUD"))
        self.assertEqual(donation.donated_at, MONTHLY_PAYMENT_SUCCEEDED_AT)
        self.assertTrue(donation.is_recurring, "The payment belongs to an active recurring plan.")
        self.assertEqual(
            donation.stripe_payment_id,
            "",
            "The charge belongs to the legacy CRM's Stripe account, so it is not ours to look up.",
        )
        self.assertEqual(
            donation.metadata["legacy_transaction_key"], MONTHLY_PAYMENT_TRANSACTION_KEY
        )
        self.assertEqual(donation.metadata["legacy_page_slug"], "donate")
        engagement = Engagement.objects.get(person=person, action_type=Engagement.DONATED)
        self.assertEqual(engagement.created_at, MONTHLY_PAYMENT_SUCCEEDED_AT)

    def test_reimporting_creates_no_duplicates(self):
        Person.objects.create(email=DONOR_EMAIL, legacy_id=DONOR_LEGACY_ID)
        path = self.write_csv(MONTHLY_PAYMENT)
        self.run_command(path)
        self.run_command(path)
        self.assertEqual(
            Donation.objects.count(), 1, "The CSV holds one successful payment, imported twice."
        )
        self.assertEqual(Engagement.objects.count(), 1)

    def test_only_successful_payments_are_imported(self):
        Person.objects.create(email=DONOR_EMAIL, legacy_id=DONOR_LEGACY_ID)
        stdout, _ = self.run_command(
            self.write_csv(MONTHLY_PAYMENT, DECLINED_PAYMENT, PENDING_PAYMENT, REFUNDED_PAYMENT)
        )
        self.assertEqual(
            list(Donation.objects.values_list("legacy_id", flat=True)), [MONTHLY_PAYMENT_ID]
        )
        self.assertIn(
            "1 failed, 1 pending, 1 refunded",
            stdout,
            "The summary should count, by status, each of the three payments that kept no money.",
        )

    def test_matches_a_person_without_a_legacy_id_by_email(self):
        person = Person.objects.create(email=DONOR_EMAIL.upper())
        self.run_command(self.write_csv(MONTHLY_PAYMENT))
        self.assertEqual(Donation.objects.get().person, person)

    def test_skips_a_payment_whose_email_belongs_to_another_legacy_person(self):
        Person.objects.create(email=DONOR_EMAIL, legacy_id=DONOR_LEGACY_ID + 1)
        _, stderr = self.run_command(self.write_csv(MONTHLY_PAYMENT))
        self.assertFalse(Donation.objects.exists())
        self.assertIn(f"donation {MONTHLY_PAYMENT_ID}", stderr)

    def test_skips_a_payment_by_someone_unknown(self):
        _, stderr = self.run_command(self.write_csv(MONTHLY_PAYMENT))
        self.assertFalse(Donation.objects.exists())
        self.assertIn(f"donation {MONTHLY_PAYMENT_ID}", stderr)

    def test_dry_run_writes_nothing(self):
        Person.objects.create(email=DONOR_EMAIL, legacy_id=DONOR_LEGACY_ID)
        stdout, _ = self.run_command(self.write_csv(MONTHLY_PAYMENT), "--dry-run")
        self.assertFalse(Donation.objects.exists())
        self.assertFalse(Engagement.objects.exists())
        self.assertIn("1 to import", stdout)

    def test_leaves_the_donation_totals_from_the_people_import_alone(self):
        # import_people_csv copies these from the legacy CRM's own totals.
        legacy_count = 128
        legacy_total = Money(Decimal("7377.92"), "AUD")
        Person.objects.create(
            email=DONOR_EMAIL,
            legacy_id=DONOR_LEGACY_ID,
            donations_count=legacy_count,
            donations_amount=legacy_total,
        )
        self.run_command(self.write_csv(MONTHLY_PAYMENT))
        person = Person.objects.get(legacy_id=DONOR_LEGACY_ID)
        self.assertEqual(person.donations_count, legacy_count)
        self.assertEqual(person.donations_amount, legacy_total)
