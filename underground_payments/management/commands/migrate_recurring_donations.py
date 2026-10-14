"""
Recreate the legacy CRM's active recurring donation plans as Stripe subscriptions.

Usage:
    python manage.py migrate_recurring_donations --input-dir <path> --payment-page-id <id> --dry-run
    python manage.py migrate_recurring_donations --input-dir <path> --payment-page-id <id>
    python manage.py migrate_recurring_donations --input-dir <path> --payment-page-id <id> \\
        --legacy-person-id <id>

Reads the listing pages saved by fetch_recurring_donations. For each active plan, finds
the Customer that migrate_donors labeled with the donor's legacy person ID and creates
a subscription that first bills on the plan's next bill date. A plan is skipped, with a
warning, if its Customer has no usable saved card, and a plan already migrated is never
migrated again.

Each subscription is recorded against the PaymentPage given by --payment-page-id, as
though the donor had signed up there, so that its renewals are recorded as Donations.

Cancel each plan in the legacy CRM once its subscription exists, and before its next
bill date, or the donor will be charged by both. Requires STRIPE_SECRET_KEY.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from underground_payments.legacy_migration import ACTIVE_STATUS, parse_recurring_plans
from underground_payments.models import PaymentPage
from underground_payments.stripe_migration import (
    DonationPage,
    PlanOutcome,
    create_subscription_for_plan,
    find_customers_by_legacy_person_id,
    make_client,
)


class Command(BaseCommand):
    help = "Recreate the legacy CRM's active recurring donation plans as Stripe subscriptions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input-dir",
            required=True,
            help="Directory of listing pages saved by fetch_recurring_donations.",
        )
        parser.add_argument(
            "--payment-page-id",
            type=int,
            required=True,
            help="ID of the live PaymentPage that the subscriptions' renewals are recorded against.",
        )
        parser.add_argument(
            "--legacy-person-id",
            type=int,
            default=None,
            help="Only migrate this donor's plans, e.g. to try the command on one person first.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Look up Customers and report what would be created, without creating anything.",
        )

    def handle(self, *args, **options):
        if not settings.STRIPE_SECRET_KEY:
            raise CommandError("STRIPE_SECRET_KEY is not set.")
        client = make_client(settings.STRIPE_SECRET_KEY)
        dry_run: bool = options["dry_run"]

        payment_page = PaymentPage.objects.live().filter(pk=options["payment_page_id"]).first()
        if payment_page is None:
            raise CommandError(f"No live PaymentPage with ID {options['payment_page_id']}.")
        page = DonationPage(
            id=payment_page.pk, url=payment_page.full_url or "", title=payment_page.title
        )

        page_files = sorted(Path(options["input_dir"]).glob("*.html"))
        if not page_files:
            raise CommandError(f"No saved listing pages in {options['input_dir']}.")
        plans = [plan for path in page_files for plan in parse_recurring_plans(path.read_bytes())]
        if options["legacy_person_id"] is not None:
            plans = [plan for plan in plans if plan.legacy_person_id == options["legacy_person_id"]]

        today = timezone.localdate()
        counts = {outcome: 0 for outcome in PlanOutcome}
        for plan in plans:
            label = (
                f"plan {plan.legacy_plan_id} ({plan.donor_name}, legacy ID {plan.legacy_person_id})"
            )
            if plan.status != ACTIVE_STATUS:
                # Only active plans are recreated; a canceled or failed one never is.
                self.stdout.write(f"  {label}: {plan.status}, skipped")
                continue

            customers = find_customers_by_legacy_person_id(client, plan.legacy_person_id)
            if len(customers) != 1:
                counts[PlanOutcome.SKIPPED] += 1
                found = ", ".join(customer.id for customer in customers) or "none"
                self.stderr.write(
                    f"  [warning] {label}: skipped, since exactly one Customer should be "
                    f"labeled with this legacy ID, but found {found}"
                )
                continue

            result = create_subscription_for_plan(
                client,
                customers[0],
                plan,
                page,
                currency=settings.STRIPE_DEFAULT_CURRENCY,
                time_zone=settings.TIME_ZONE,
                today=today,
                dry_run=dry_run,
            )
            counts[result.outcome] += 1
            if result.outcome is PlanOutcome.SKIPPED:
                self.stderr.write(f"  [warning] {label}: skipped, since {result.message}")
            else:
                self.stdout.write(f"  {label}: {result.message}")

        summary = ", ".join(f"{count} {outcome.value}" for outcome, count in counts.items())
        prefix = "Dry run: " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}active plans: {summary}."))
