"""
Import the legacy CRM's donations as Donation records, so that each person's history of
giving is kept.

Usage:
    python manage.py import_donations --csv-file <path> --dry-run
    python manage.py import_donations --csv-file <path>
    python manage.py import_donations --csv-file <path> --legacy-person-id <id>

Reads the legacy CRM's financial transactions CSV export, the same file as
migrate_donors. Each transaction that succeeded becomes a Donation, dated when it
succeeded, together with a "donated" Engagement. Failed, pending and refunded
transactions are counted but not imported, since no money was kept from them.

Donors are matched to the Person with their legacy person ID, or failing that, their
email address, so run import_people_csv first. A transaction whose donor matches nobody
is skipped with a warning. Re-running the import never duplicates a Donation, since each
is keyed by its legacy transaction ID.

The people's donation totals (donations_count, donations_amount, etc.) are left as
import_people_csv set them from the legacy CRM's own totals, which count transactions
this command cannot attribute to anyone.

The export's naive timestamps are read in LEGACY_SOURCE_TIMEZONE, or TIME_ZONE if that
is unset.
"""

import logging
import os
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from djmoney.money import Money

from underground_crm.models import Donation, Engagement, Person
from underground_payments.legacy_migration import (
    SUCCEEDED_STATUS,
    LegacyDonation,
    read_donations,
)

logger = logging.getLogger(__name__)

LEGACY_SOURCE_TIMEZONE_ENV_VAR = "LEGACY_SOURCE_TIMEZONE"


def legacy_source_time_zone() -> ZoneInfo:
    name = os.environ.get(LEGACY_SOURCE_TIMEZONE_ENV_VAR, "").strip() or settings.TIME_ZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise CommandError(f"{name!r} is not a known IANA time zone.") from exc


class DonorNotFoundError(Exception):
    """No Person can safely be credited with a legacy donation."""


def find_donor(donation: LegacyDonation) -> Person:
    """
    The Person who made a legacy donation: the one with its legacy person ID, or failing
    that, the one with its email address, unless they have another legacy ID.
    """
    if donation.legacy_person_id is not None:
        person = Person.objects.filter(legacy_id=donation.legacy_person_id).first()
        if person is not None:
            return person
    if not donation.email:
        raise DonorNotFoundError(
            "This donation lacks an email address and the donor could not be found by their legacy ID"
        )
    person = Person.objects.filter(email__iexact=donation.email).first()
    if person is None:
        raise DonorNotFoundError(
            "The email address for this donation does not correspond to a person in our database"
        )
    if person.legacy_id is not None and person.legacy_id != donation.legacy_person_id:
        raise DonorNotFoundError(
            f"The Person tied to this donation's email address ({donation.email}) has a differing legacy ID {person.legacy_id}, not {donation.legacy_person_id}"
        )
    return person


def donation_metadata(donation: LegacyDonation) -> dict[str, str]:
    """What the legacy CRM recorded about the donation that Donation has no field for."""
    metadata = {
        "legacy_page_slug": donation.page_slug,
        "legacy_tracking_code": donation.tracking_code,
        "legacy_payment_type": donation.payment_type,
        "legacy_merchant_account": donation.merchant_account,
        # Not a stripe_payment_id, since any Stripe charge it names belongs to the legacy
        # CRM's Stripe account rather than ours.
        "legacy_transaction_key": donation.transaction_key,
    }
    return {key: value for key, value in metadata.items() if value}


def import_donation(donation: LegacyDonation, person: Person, currency: str) -> Donation:
    amount = Money(Decimal(donation.amount_cents) / 100, currency)
    metadata = donation_metadata(donation)
    with transaction.atomic():
        record = Donation.objects.create(
            person=person,
            amount=amount,
            is_recurring=donation.is_recurring,
            donated_at=donation.succeeded_at,
            metadata=metadata,
            legacy_id=donation.legacy_donation_id,
        )
        engagement = Engagement.objects.create(
            person=person,
            action_type=Engagement.DONATED,
            metadata={
                **metadata,
                "amount_cents": donation.amount_cents,
                "legacy_donation_id": donation.legacy_donation_id,
            },
        )
        # created_at is set on creation, so the donation's own date is applied afterwards.
        Engagement.objects.filter(pk=engagement.pk).update(created_at=donation.succeeded_at)
    return record


class Command(BaseCommand):
    help = "Import the legacy CRM's successful donations as Donation records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv-file",
            required=True,
            help="Path to the legacy CRM's financial transactions CSV export.",
        )
        parser.add_argument(
            "--legacy-person-id",
            type=int,
            default=None,
            help="Only import this donor's donations, e.g. to try the command on one person.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Match donors and report what would be imported, without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        currency = settings.STRIPE_DEFAULT_CURRENCY.upper()

        try:
            donations, skipped_row_count = read_donations(
                options["csv_file"], legacy_source_time_zone()
            )
        except FileNotFoundError as exc:
            raise CommandError(f"File not found: {options['csv_file']}") from exc
        if skipped_row_count:
            logger.warning("Skipped %d row(s) with no transaction ID or amount.", skipped_row_count)
        legacy_person_id: Optional[int] = options["legacy_person_id"]
        if legacy_person_id is not None:
            donations = [
                donation for donation in donations if donation.legacy_person_id == legacy_person_id
            ]
            if not donations:
                raise CommandError(f"No donation by legacy ID {legacy_person_id} in the CSV.")

        already_imported = set(
            Donation.objects.filter(
                legacy_id__in=[donation.legacy_donation_id for donation in donations]
            ).values_list("legacy_id", flat=True)
        )
        imported_count = already_imported_count = skipped_count = 0
        # Transactions that kept no money, counted by their status.
        not_succeeded_counts: dict[str, int] = {}
        for donation in donations:
            if donation.status != SUCCEEDED_STATUS:
                not_succeeded_counts[donation.status] = (
                    not_succeeded_counts.get(donation.status, 0) + 1
                )
                continue
            if donation.legacy_donation_id in already_imported:
                already_imported_count += 1
                continue
            if donation.succeeded_at is None:
                skipped_count += 1
                logger.warning(
                    "Skipped donation %s, since it has no success time.",
                    donation.legacy_donation_id,
                )
                continue
            try:
                person = find_donor(donation)
            except DonorNotFoundError as exc:
                skipped_count += 1
                logger.warning(
                    "Skipped donation %s (legacy person ID %s): %s",
                    donation.legacy_donation_id,
                    donation.legacy_person_id,
                    exc,
                )
                continue
            if not dry_run:
                import_donation(donation, person, currency)
            imported_count += 1

        prefix = "Dry run: " if dry_run else ""
        not_succeeded = ", ".join(
            f"{count} {status or 'without a status'}"
            for status, count in sorted(not_succeeded_counts.items())
        )
        logger.info(
            "%s%d transaction(s): %d %s, %d already imported, %d skipped with a warning, "
            "and not imported for keeping no money: %s.",
            prefix,
            len(donations),
            imported_count,
            "to import" if dry_run else "imported",
            already_imported_count,
            skipped_count,
            not_succeeded or "none",
        )
