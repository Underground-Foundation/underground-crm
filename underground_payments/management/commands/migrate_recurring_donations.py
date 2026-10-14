"""
Recreate the legacy CRM's active recurring donation plans as Stripe subscriptions.

Usage:
    python manage.py migrate_recurring_donations --input-dir <path> --csv-file <path> \\
        --payment-page-id <id> --dry-run
    python manage.py migrate_recurring_donations --input-dir <path> --csv-file <path> \\
        --payment-page-id <id>
    python manage.py migrate_recurring_donations --input-dir <path> --csv-file <path> \\
        --payment-page-id <id> --legacy-person-id <id>

Reads the listing pages saved by fetch_recurring_donations, which name each plan's donor
but not its Stripe Customer, and the recurring donations CSV exported from the legacy
CRM's Stripe account, which names each plan's Customer and card but not its donor.
Stripe's copies of those Customers have no name or email address.

Each active plan is paired with the row of the CSV that has its plan ID (the row's
nationbuilder_recurring_donation_id), which is the ID in the plan's own link on the
listing. A plan with no row, or whose row's Customer already belongs to another donor, is
skipped, with a warning.

The row names the Customer, so that Customer is then given the donor's legacy person ID,
name and email address (the email address being the Person's), and a subscription is
created charging its card and first billing at the moment given by the row. A plan
already migrated, by this command or by import_recurring_donations_from_csv, is never
migrated again.

Each subscription is recorded against the PaymentPage given by --payment-page-id, as
though the donor had signed up there, so that its renewals are recorded as Donations.

Cancel each plan in the legacy CRM once its subscription exists, and before its next
bill date, or the donor will be charged by both. Requires STRIPE_SECRET_KEY.
"""

import datetime
import logging
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from underground_crm.models import Person
from underground_payments.legacy_migration import (
    ACTIVE_STATUS,
    ExportedRecurringPlan,
    parse_recurring_plans,
    read_exported_recurring_plans,
)
from underground_payments.models import PaymentPage
from underground_payments.stripe_migration import (
    DonationPage,
    PlanOutcome,
    PlanResult,
    create_subscription_for_exported_plan,
    label_customer,
    make_client,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recreate the legacy CRM's active recurring donation plans as Stripe subscriptions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input-dir",
            required=True,
            help="Directory of listing pages saved by fetch_recurring_donations.",
        )
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
            "--legacy-person-id",
            type=int,
            default=None,
            help="Only migrate this donor's plans, e.g. to try the command on one person first.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Pair plans with rows and report what would be created, without changing "
            "anything in Stripe.",
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
        listed_plans = [
            plan for path in page_files for plan in parse_recurring_plans(path.read_bytes())
        ]
        # Only active plans are recreated, and a canceled or failed one never takes a row.
        # In order of plan ID, so that a repeat of this command takes rows in the same order.
        active_plans = sorted(
            (plan for plan in listed_plans if plan.status == ACTIVE_STATUS),
            key=lambda plan: plan.legacy_plan_id,
        )

        csv_path = Path(options["csv_file"])
        if not csv_path.is_file():
            raise CommandError(f"No such file: {csv_path}")
        rows, problems = read_exported_recurring_plans(csv_path)
        for problem in problems:
            logger.warning("Skipped the unreadable row at %s", problem)

        rows_by_plan_id: dict[int, ExportedRecurringPlan] = {}
        for row in rows:
            if row.legacy_plan_id in rows_by_plan_id:
                logger.warning(
                    "Plan %s is in the CSV twice; using its first row, for Customer %s",
                    row.legacy_plan_id,
                    rows_by_plan_id[row.legacy_plan_id].customer_id,
                )
                continue
            rows_by_plan_id[row.legacy_plan_id] = row

        now = timezone.now()
        counts = {outcome: 0 for outcome in PlanOutcome}
        for plan in active_plans:
            if (
                options["legacy_person_id"] is not None
                and plan.legacy_person_id != options["legacy_person_id"]
            ):
                continue
            label = (
                f"plan {plan.legacy_plan_id} ({plan.donor_name}, legacy ID {plan.legacy_person_id})"
            )
            row = rows_by_plan_id.pop(plan.legacy_plan_id, None)
            if row is None:
                counts[PlanOutcome.SKIPPED] += 1
                logger.warning("Skipped %s, since the CSV has no row for this plan", label)
                continue

            person = Person.objects.filter(legacy_id=plan.legacy_person_id).first()
            result = self.migrate_matched_plan(
                client,
                person=person,
                legacy_person_id=plan.legacy_person_id,
                donor_name=plan.donor_name,
                row=row,
                page=page,
                now=now,
                dry_run=dry_run,
            )
            counts[result.outcome] += 1
            if result.outcome is PlanOutcome.SKIPPED:
                logger.warning("Skipped %s, since %s", label, result.message)
            else:
                logger.info("%s (Customer %s): %s", label, row.customer_id, result.message)

        summary = ", ".join(f"{count} {outcome.value}" for outcome, count in counts.items())
        if problems:
            summary += f", {len(problems)} unreadable"
        unused_rows = list(rows_by_plan_id.values()) if options["legacy_person_id"] is None else []
        if unused_rows:
            summary += f"; {len(unused_rows)} rows of the CSV matched no active plan"
        prefix = "Dry run: " if dry_run else ""
        logger.info("%sactive plans: %s.", prefix, summary)
        for row in unused_rows:
            logger.info(
                "Unused row: plan %s (Customer %s), %s cents, next payment %s",
                row.legacy_plan_id,
                row.customer_id,
                row.amount_cents,
                row.next_payment_at,
            )

    def migrate_matched_plan(
        self,
        client,
        person: Optional[Person],
        legacy_person_id: int,
        donor_name: str,
        row: ExportedRecurringPlan,
        page: DonationPage,
        now: datetime.datetime,
        dry_run: bool,
    ) -> PlanResult:
        """Label the row's Customer as the donor, then subscribe them as the row says."""
        if person is None or not person.email:
            return PlanResult(
                PlanOutcome.SKIPPED,
                f"no Person with legacy ID {legacy_person_id} has an email address to give "
                f"Customer {row.customer_id}",
            )
        if not dry_run:
            why_not = label_customer(
                client, row.customer_id, donor_name, person.email, legacy_person_id
            )
            if why_not is not None:
                return PlanResult(PlanOutcome.SKIPPED, why_not)
        return create_subscription_for_exported_plan(
            client,
            row,
            page,
            currency=settings.STRIPE_DEFAULT_CURRENCY,
            now=now,
            dry_run=dry_run,
            email_of_legacy_person=lambda person_id: person.email,
            legacy_person_id_override=legacy_person_id,
        )
