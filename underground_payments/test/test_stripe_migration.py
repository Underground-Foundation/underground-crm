"""
Tests for recreating legacy recurring donation plans as Stripe subscriptions.

The tests that call Stripe run only against a test-mode account (a STRIPE_SECRET_KEY
beginning with "sk_test_"), and clean up the Customers they create.
"""

import dataclasses
import datetime
import uuid
import zoneinfo
from pathlib import Path

import stripe
from django.conf import settings
from django.test import SimpleTestCase

from underground_payments.legacy_migration import (
    ACTIVE_STATUS,
    ExportedRecurringPlan,
    RecurringPlan,
    parse_recurring_plans,
)
from underground_payments.stripe_migration import (
    LEGACY_PERSON_ID_METADATA_KEY,
    LEGACY_PLAN_ID_METADATA_KEY,
    DonationPage,
    PlanOutcome,
    create_subscription_for_exported_plan,
    create_subscription_for_plan,
    first_billing_timestamp,
    make_client,
    quote_search_value,
    subscription_payment_params,
)

TIME_ZONE = "Australia/Melbourne"
DONATION_PAGE = DonationPage(
    id=48, url="https://fusionparty.org.au/donate/", title="Donate to Fusion"
)
CARD_ID = "pm_1Sf8O32EiAKqJzItc4rd0001"
OTHER_CARD_ID = "pm_1Sf8O32EiAKqJzItc4rd0002"
LEGACY_CARD_SOURCE_ID = "card_1Sf8O32EiAKqJzItc4rd0003"


class SubscriptionPaymentParamsTests(SimpleTestCase):
    def test_customer_default_payment_method_is_used_implicitly(self):
        self.assertEqual(subscription_payment_params(CARD_ID, None, [CARD_ID, OTHER_CARD_ID]), {})

    def test_customer_default_card_source_is_used_implicitly(self):
        self.assertEqual(subscription_payment_params(None, LEGACY_CARD_SOURCE_ID, []), {})

    def test_only_saved_card_is_chosen(self):
        self.assertEqual(
            subscription_payment_params(None, None, [CARD_ID]),
            {"default_payment_method": CARD_ID},
        )

    def test_no_saved_card(self):
        self.assertIsNone(subscription_payment_params(None, None, []))

    def test_several_cards_without_a_default_are_ambiguous(self):
        self.assertIsNone(subscription_payment_params(None, None, [CARD_ID, OTHER_CARD_ID]))


class FirstBillingTimestampTests(SimpleTestCase):
    def test_bills_at_midday_in_the_site_time_zone(self):
        next_bill_date = datetime.date(2026, 10, 15)
        expected = datetime.datetime(
            2026, 10, 15, 12, 0, tzinfo=zoneinfo.ZoneInfo(TIME_ZONE)
        ).timestamp()
        self.assertEqual(first_billing_timestamp(next_bill_date, TIME_ZONE), expected)


class QuoteSearchValueTests(SimpleTestCase):
    def test_escapes_an_apostrophe_in_an_email_address(self):
        self.assertEqual(
            quote_search_value("siobhan.o'brien@example.org"),
            "'siobhan.o\\'brien@example.org'",
        )


SAMPLE_PAGE = Path(__file__).parent / "recurring_plans_sample.html"
# The statuses that the saved page shows for plans that are no longer billing.
INACTIVE_STATUSES = {"canceled", "failed", "pending"}


class InactivePlanTests(SimpleTestCase):
    def test_inactive_plans_are_skipped_without_contacting_stripe(self):
        plans = [
            plan
            for plan in parse_recurring_plans(SAMPLE_PAGE.read_bytes())
            if plan.status != ACTIVE_STATUS
        ]
        self.assertEqual(
            {plan.status for plan in plans},
            INACTIVE_STATUSES,
            "The saved page should show plans in every inactive status, so that each is tested.",
        )
        for plan in plans:
            with self.subTest(legacy_plan_id=plan.legacy_plan_id, status=plan.status):
                result = create_subscription_for_plan(
                    # No Stripe client or Customer, so any call to Stripe would fail the test.
                    client=None,
                    customer=None,
                    plan=plan,
                    page=DONATION_PAGE,
                    currency="aud",
                    time_zone=TIME_ZONE,
                    today=datetime.date(2026, 10, 13),
                    dry_run=False,
                )
                self.assertIs(result.outcome, PlanOutcome.SKIPPED)
                self.assertEqual(result.message, f"it is {plan.status}")


# How far ahead of today the legacy plans in these tests next bill.
DAYS_UNTIL_NEXT_BILL = 10
# Stripe's test card that always succeeds.
TEST_CARD = "pm_card_visa"


def test_mode_key() -> str:
    key = settings.STRIPE_SECRET_KEY
    return key if key.startswith("sk_test_") else ""


class CreateSubscriptionForPlanTests(SimpleTestCase):
    """Against Stripe's test mode, since what matters is what Stripe accepts and does."""

    def setUp(self):
        key = test_mode_key()
        if not key:
            self.skipTest("STRIPE_SECRET_KEY is not a test-mode key.")
        self.client = make_client(key)
        self.today = datetime.date.today()
        # Plan IDs are unique per run, so that one run's subscriptions never look like
        # another's to the check for plans already migrated.
        self.plan = RecurringPlan(
            legacy_plan_id=uuid.uuid4().int % 10**9,
            legacy_person_id=37776,
            donor_name="Riley Hutchinson",
            status="active",
            amount_cents=220,
            interval="month",
            next_bill_date=self.today + datetime.timedelta(days=DAYS_UNTIL_NEXT_BILL),
        )

    def create_customer(self, with_card: bool) -> stripe.Customer:
        params = {
            "email": "riley.hutchinson@example.org",
            "name": "Riley Hutchinson",
            "metadata": {LEGACY_PERSON_ID_METADATA_KEY: str(self.plan.legacy_person_id)},
        }
        if with_card:
            params["payment_method"] = TEST_CARD
            params["invoice_settings"] = {"default_payment_method": TEST_CARD}
        customer = self.client.v1.customers.create(params=params)
        # Deleting a Customer also cancels their subscriptions.
        self.addCleanup(self.client.v1.customers.delete, customer.id)
        return customer

    def migrate(self, customer: stripe.Customer):
        return create_subscription_for_plan(
            self.client,
            customer,
            self.plan,
            DONATION_PAGE,
            currency="aud",
            time_zone=TIME_ZONE,
            today=self.today,
            dry_run=False,
        )

    def test_subscription_first_bills_on_the_next_bill_date(self):
        result = self.migrate(self.create_customer(with_card=True))
        self.assertIs(result.outcome, PlanOutcome.CREATED, result.message)

        subscription = self.client.v1.subscriptions.retrieve(
            result.subscription_id, params={"expand": ["latest_invoice"]}
        )
        self.assertEqual(subscription.status, "active")
        self.assertEqual(
            subscription.billing_cycle_anchor,
            first_billing_timestamp(self.plan.next_bill_date, TIME_ZONE),
        )
        self.assertIsNone(
            subscription.latest_invoice,
            "Nothing should be invoiced, let alone charged, before the legacy plan's next "
            "bill date.",
        )
        price = subscription["items"].data[0].price
        self.assertEqual(price.unit_amount, self.plan.amount_cents)
        self.assertEqual(price.recurring.interval, self.plan.interval)
        metadata = subscription.metadata.to_dict()
        self.assertEqual(metadata["page_id"], str(DONATION_PAGE.id))
        self.assertEqual(metadata["frequency"], "monthly")
        self.assertEqual(metadata["donor_email"], "riley.hutchinson@example.org")
        self.assertEqual(metadata[LEGACY_PLAN_ID_METADATA_KEY], str(self.plan.legacy_plan_id))

    def test_plan_is_not_migrated_twice(self):
        customer = self.create_customer(with_card=True)
        first = self.migrate(customer)
        second = self.migrate(customer)
        self.assertIs(second.outcome, PlanOutcome.ALREADY_MIGRATED, second.message)
        self.assertEqual(second.subscription_id, first.subscription_id)

    def test_customer_without_a_card_is_skipped(self):
        result = self.migrate(self.create_customer(with_card=False))
        self.assertIs(result.outcome, PlanOutcome.SKIPPED, result.message)


# When the exported plans in these tests are migrated, relative to their next payment.
MIGRATION_TIME = datetime.datetime(2026, 10, 14, 9, 30, tzinfo=datetime.timezone.utc)
EXPORTED_PLAN = ExportedRecurringPlan(
    legacy_plan_id=2118,
    customer_id="cus_Tbk1RileyHutch",
    payment_method_id="pm_1SeWXmRileyHutchinson00",
    legacy_subscription_id="sub_1SeWXpRileyHutchinson0",
    amount_cents=220,
    interval="month",
    interval_count=1,
    next_payment_at=MIGRATION_TIME + datetime.timedelta(days=1),
)


class ExportedPlanSkippedBeforeContactingStripeTests(SimpleTestCase):
    def migrate(self, plan: ExportedRecurringPlan):
        return create_subscription_for_exported_plan(
            # No Stripe client, so any call to Stripe would fail the test.
            client=None,
            plan=plan,
            page=DONATION_PAGE,
            currency="aud",
            now=MIGRATION_TIME,
            dry_run=False,
        )

    def test_quarterly_plan_is_skipped(self):
        # The webhook records only monthly and annual payments.
        result = self.migrate(dataclasses.replace(EXPORTED_PLAN, interval_count=3))
        self.assertIs(result.outcome, PlanOutcome.SKIPPED, result.message)

    def test_plan_whose_next_payment_has_passed_is_skipped(self):
        result = self.migrate(dataclasses.replace(EXPORTED_PLAN, next_payment_at=MIGRATION_TIME))
        self.assertIs(result.outcome, PlanOutcome.SKIPPED, result.message)


# How far ahead of now the exported plans in the test-mode tests next bill.
HOURS_UNTIL_NEXT_PAYMENT = 30


class CreateSubscriptionForExportedPlanTests(SimpleTestCase):
    """Against Stripe's test mode, since what matters is what Stripe accepts and does."""

    def setUp(self):
        key = test_mode_key()
        if not key:
            self.skipTest("STRIPE_SECRET_KEY is not a test-mode key.")
        self.client = make_client(key)
        self.now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)

    def create_customer_with_card(self, email: str) -> tuple[stripe.Customer, str]:
        """A Customer like one Stripe copied from the legacy CRM, and their card's ID."""
        customer = self.client.v1.customers.create(
            params={"email": email, "name": "Riley Hutchinson", "payment_method": TEST_CARD}
        )
        # Deleting a Customer also cancels their subscriptions.
        self.addCleanup(self.client.v1.customers.delete, customer.id)
        [card] = self.client.v1.customers.payment_methods.list(customer.id).data
        return customer, card.id

    def exported_plan(self, customer_id: str, payment_method_id: str) -> ExportedRecurringPlan:
        return dataclasses.replace(
            EXPORTED_PLAN,
            # Unique per run, so that one run's subscriptions never look like another's to
            # the check for plans already migrated.
            legacy_plan_id=uuid.uuid4().int % 10**9,
            customer_id=customer_id,
            payment_method_id=payment_method_id,
            next_payment_at=self.now + datetime.timedelta(hours=HOURS_UNTIL_NEXT_PAYMENT),
        )

    def migrate(self, plan: ExportedRecurringPlan, dry_run: bool = False):
        return create_subscription_for_exported_plan(
            self.client, plan, DONATION_PAGE, currency="aud", now=self.now, dry_run=dry_run
        )

    def test_subscription_charges_the_exported_card_from_its_next_payment(self):
        customer, card_id = self.create_customer_with_card("riley.hutchinson@example.org")
        plan = self.exported_plan(customer.id, card_id)
        result = self.migrate(plan)
        self.assertIs(result.outcome, PlanOutcome.CREATED, result.message)

        subscription = self.client.v1.subscriptions.retrieve(
            result.subscription_id, params={"expand": ["latest_invoice"]}
        )
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.default_payment_method, card_id)
        self.assertEqual(subscription.billing_cycle_anchor, int(plan.next_payment_at.timestamp()))
        self.assertIsNone(
            subscription.latest_invoice,
            "Nothing should be invoiced, let alone charged, before the legacy plan's next "
            "payment.",
        )
        price = subscription["items"].data[0].price
        self.assertEqual(price.unit_amount, plan.amount_cents)
        self.assertEqual(price.recurring.interval, plan.interval)
        metadata = subscription.metadata.to_dict()
        self.assertEqual(metadata["page_id"], str(DONATION_PAGE.id))
        self.assertEqual(metadata["frequency"], "monthly")
        self.assertEqual(metadata["donor_email"], "riley.hutchinson@example.org")
        self.assertEqual(metadata[LEGACY_PLAN_ID_METADATA_KEY], str(plan.legacy_plan_id))
        self.assertNotIn(
            LEGACY_PERSON_ID_METADATA_KEY,
            metadata,
            "The Customer was never labeled by migrate_donors, so no legacy person ID is known.",
        )

    def test_plan_is_not_migrated_twice(self):
        customer, card_id = self.create_customer_with_card("riley.hutchinson@example.org")
        plan = self.exported_plan(customer.id, card_id)
        first = self.migrate(plan)
        second = self.migrate(plan)
        self.assertIs(second.outcome, PlanOutcome.ALREADY_MIGRATED, second.message)
        self.assertEqual(second.subscription_id, first.subscription_id)

    def test_customer_without_an_email_gets_the_email_of_their_legacy_person(self):
        legacy_person_id = 48213
        legacy_person_email = "riley.hutchinson@example.org"
        customer, card_id = self.create_customer_with_card(legacy_person_email)
        self.client.v1.customers.update(
            customer.id,
            params={
                "email": "",
                "metadata": {LEGACY_PERSON_ID_METADATA_KEY: str(legacy_person_id)},
            },
        )
        plan = self.exported_plan(customer.id, card_id)
        result = create_subscription_for_exported_plan(
            self.client,
            plan,
            DONATION_PAGE,
            currency="aud",
            now=self.now,
            dry_run=False,
            email_of_legacy_person=lambda person_id: (
                legacy_person_email if person_id == legacy_person_id else None
            ),
        )
        self.assertIs(result.outcome, PlanOutcome.CREATED, result.message)
        subscription = self.client.v1.subscriptions.retrieve(result.subscription_id)
        self.assertEqual(
            subscription.metadata["donor_email"],
            legacy_person_email,
            "The Customer has no email, so the webhook should be given the legacy person's.",
        )

    def test_customer_without_an_email_or_legacy_person_is_skipped(self):
        customer, card_id = self.create_customer_with_card("riley.hutchinson@example.org")
        self.client.v1.customers.update(customer.id, params={"email": ""})
        result = self.migrate(self.exported_plan(customer.id, card_id))
        self.assertIs(result.outcome, PlanOutcome.SKIPPED, result.message)

    def test_dry_run_creates_nothing(self):
        customer, card_id = self.create_customer_with_card("riley.hutchinson@example.org")
        result = self.migrate(self.exported_plan(customer.id, card_id), dry_run=True)
        self.assertIs(result.outcome, PlanOutcome.CREATED, result.message)
        subscriptions = self.client.v1.subscriptions.list(params={"customer": customer.id})
        self.assertEqual(subscriptions.data, [])

    def test_customer_not_yet_copied_is_skipped(self):
        _, card_id = self.create_customer_with_card("riley.hutchinson@example.org")
        result = self.migrate(self.exported_plan("cus_NotCopiedYet0000", card_id))
        self.assertIs(result.outcome, PlanOutcome.SKIPPED, result.message)

    def test_card_is_the_customers_own_whatever_id_the_csv_names(self):
        customer, card_id = self.create_customer_with_card("riley.hutchinson@example.org")
        id_from_the_legacy_account = "pm_1T3aEWCMpnBh61hXYDFbmOxw"
        plan = dataclasses.replace(
            self.exported_plan(customer.id, card_id), payment_method_id=id_from_the_legacy_account
        )
        result = self.migrate(plan)
        self.assertIs(result.outcome, PlanOutcome.CREATED, result.message)
        subscription = self.client.v1.subscriptions.retrieve(result.subscription_id)
        self.assertEqual(subscription.default_payment_method, card_id)
