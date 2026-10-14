"""
Label each donor's Stripe Customer with their legacy CRM person ID, so that
migrate_recurring_donations can find it.

Usage:
    python manage.py migrate_donors --csv-file <path> --dry-run
    python manage.py migrate_donors --csv-file <path>
    python manage.py migrate_donors --csv-file <path> --legacy-person-id <id>

Reads the legacy CRM's financial transactions CSV export. For each distinct donor in
it, finds the Customers with their email address (those copied from the legacy CRM's
Stripe account, together with their saved cards) and adds a legacy_person_id to their
metadata. A donor with no Customer gets a new one, which has no saved card.

Run this only after Stripe has finished copying the Customers, or every donor will get
a new Customer without a card. Requires STRIPE_SECRET_KEY.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from underground_payments.legacy_migration import read_donors
from underground_payments.stripe_migration import (
    DonorOutcome,
    label_or_create_customer,
    make_client,
)


class Command(BaseCommand):
    help = "Label donors' Stripe Customers with their legacy CRM person ID."

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
            help="Only migrate this donor, e.g. to try the command on one person first.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Look up Customers and report what would change, without changing anything.",
        )

    def handle(self, *args, **options):
        if not settings.STRIPE_SECRET_KEY:
            raise CommandError("STRIPE_SECRET_KEY is not set.")
        client = make_client(settings.STRIPE_SECRET_KEY)
        dry_run: bool = options["dry_run"]

        donors, skipped_row_count = read_donors(options["csv_file"])
        if skipped_row_count:
            self.stderr.write(
                f"  [warning] skipped {skipped_row_count} row(s) with no donor ID or email."
            )
        if options["legacy_person_id"] is not None:
            donors = [
                donor for donor in donors if donor.legacy_person_id == options["legacy_person_id"]
            ]
            if not donors:
                raise CommandError(
                    f"No donor with legacy ID {options['legacy_person_id']} in the CSV."
                )

        counts = {outcome: 0 for outcome in DonorOutcome}
        for donor in donors:
            result = label_or_create_customer(client, donor, dry_run)
            counts[result.outcome] += 1
            line = f"  {donor.legacy_person_id} <{donor.email}>: {result.message}"
            if result.outcome in (DonorOutcome.CONFLICT, DonorOutcome.CREATED):
                self.stderr.write(f"  [warning] {line.strip()}")
            else:
                self.stdout.write(line)

        summary = ", ".join(f"{count} {outcome.value}" for outcome, count in counts.items())
        prefix = "Dry run: " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}{len(donors)} donor(s): {summary}."))
