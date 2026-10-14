"""
Recreate the recurring donations in a CSV exported by the legacy CRM as Stripe
subscriptions.

Usage:
    python manage.py import_recurring_donations_from_csv --csv-file <path> \\
        --payment-page-id <id> --dry-run
    python manage.py import_recurring_donations_from_csv --csv-file <path> \\
        --payment-page-id <id>
    python manage.py import_recurring_donations_from_csv --csv-file <path> \\
        --payment-page-id <id> --legacy-plan-id <id>

Reads a CSV that the legacy CRM's staff export from the Stripe account that charges its
donors, with a row for each active plan naming its Customer, its card (PaymentMethod),
its amount, its frequency and when it next bills. Stripe keeps those Customer and
PaymentMethod IDs when it copies them into this site's account, so this command needs
neither migrate_donors nor fetch_recurring_donations. For each plan, it creates a
subscription charging that card and first billing at the moment the legacy CRM would
next have charged it.

A plan is skipped, with a warning, if its Customer or card has not been copied into this
site's account, or if its next payment is not in the future. A plan already migrated,
by this command or by migrate_recurring_donations, is never migrated again.

Each subscription is recorded against the PaymentPage given by --payment-page-id, as
though the donor had signed up there, so that its renewals are recorded as Donations.

Cancel each plan in the legacy CRM once its subscription exists, and before its next
payment, or the donor will be charged by both. Requires STRIPE_SECRET_KEY.
"""

import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from underground_payments.legacy_migration import read_exported_recurring_plans
from underground_payments.models import PaymentPage
from underground_payments.stripe_migration import (
    DonationPage,
    PlanOutcome,
    create_subscription_for_exported_plan,
    make_client,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recreate the recurring donations in a legacy CRM's CSV export as Stripe subscriptions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv-file",
            required=True,
            help="The recurring donations CSV exported from the legacy CRM's Stripe account.",
        )
        parser.add_argument(
            "--payment-page-id",
            type=int,
            required=True,
            help="ID of the live PaymentPage that the subscriptions' renewals are recorded against.",
        )
        parser.add_argument(
            "--legacy-plan-id",
            type=int,
            default=None,
            help="Only migrate this plan, e.g. to try the command on one donor first.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Look up Customers and cards and report what would be created, without "
            "creating anything.",
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

        csv_path = Path(options["csv_file"])
        if not csv_path.is_file():
            raise CommandError(f"No such file: {csv_path}")
        plans, problems = read_exported_recurring_plans(csv_path)
        for problem in problems:
            logger.warning("Skipped the unreadable row at %s", problem)
        if options["legacy_plan_id"] is not None:
            plans = [plan for plan in plans if plan.legacy_plan_id == options["legacy_plan_id"]]
            if not plans:
                raise CommandError(f"No plan {options['legacy_plan_id']} in {csv_path}.")

        now = timezone.now()
        counts = {outcome: 0 for outcome in PlanOutcome}
        for plan in plans:
            label = f"plan {plan.legacy_plan_id} (Customer {plan.customer_id})"
            result = create_subscription_for_exported_plan(
                client,
                plan,
                page,
                currency=settings.STRIPE_DEFAULT_CURRENCY,
                now=now,
                dry_run=dry_run,
            )
            counts[result.outcome] += 1
            if result.outcome is PlanOutcome.SKIPPED:
                logger.warning("Skipped %s, since %s", label, result.message)
            else:
                logger.info("%s: %s", label, result.message)

        summary = ", ".join(f"{count} {outcome.value}" for outcome, count in counts.items())
        if problems:
            summary += f", {len(problems)} unreadable"
        prefix = "Dry run: " if dry_run else ""
        logger.info("%splans: %s.", prefix, summary)
