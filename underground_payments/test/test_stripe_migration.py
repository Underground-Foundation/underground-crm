"""
Tests for recreating legacy recurring donation plans as Stripe subscriptions.

The tests that call Stripe run only against a test-mode account (a STRIPE_SECRET_KEY
beginning with "sk_test_"), and clean up the Customers they create.
"""

import datetime
import uuid
import zoneinfo
from pathlib import Path

import stripe
from django.conf import settings
from django.test import SimpleTestCase

from underground_payments.legacy_migration import (
    ACTIVE_STATUS,
    RecurringPlan,
    parse_recurring_plans,
)
from underground_payments.stripe_migration import (
    LEGACY_PERSON_ID_METADATA_KEY,
    LEGACY_PLAN_ID_METADATA_KEY,
    DonationPage,
    PlanOutcome,
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
