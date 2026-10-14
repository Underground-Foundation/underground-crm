"""
Stripe calls shared by migrate_donors, migrate_recurring_donations and
import_recurring_donations_from_csv, which move the legacy CRM's donors and recurring
donation plans onto Customers and Subscriptions in this site's Stripe account.

The Customers themselves (with their saved cards) arrive through Stripe's copy of
customer and payment data from the legacy CRM's Stripe account; see
docs/migrating_finances.md. These commands only label them with the legacy CRM's
person ID and recreate the subscriptions, which Stripe never copies.
"""

import datetime
import enum
import zoneinfo
from dataclasses import dataclass
from typing import Callable, Optional

import stripe

from .legacy_migration import ACTIVE_STATUS, ExportedRecurringPlan, LegacyDonor, RecurringPlan

# Customer metadata naming the legacy CRM's person ID, matching Person.legacy_id.
LEGACY_PERSON_ID_METADATA_KEY = "legacy_person_id"
# Subscription metadata naming the legacy CRM's recurring plan, so that a plan is
# never migrated twice.
LEGACY_PLAN_ID_METADATA_KEY = "legacy_recurring_donation_id"

# The renewal webhook (underground_payments.tasks) records a payment only under these
# frequencies, so a plan billed at any other interval cannot be migrated.
FREQUENCY_BY_INTERVAL = {"month": "monthly", "year": "annual"}

# The time of day, in the site's time zone, at which a migrated subscription first
# bills on the legacy plan's next bill date.
FIRST_BILLING_TIME = datetime.time(hour=12)

# The most results Stripe returns from one list or search request.
STRIPE_PAGE_LIMIT = 100


def make_client(secret_key: str) -> stripe.StripeClient:
    return stripe.StripeClient(secret_key, max_network_retries=2)


def quote_search_value(value: str) -> str:
    """Quote a value for Stripe's search query language."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def find_customers_by_email(client: stripe.StripeClient, email: str) -> list[stripe.Customer]:
    result = client.v1.customers.search(
        params={"query": f"email:{quote_search_value(email)}", "limit": STRIPE_PAGE_LIMIT}
    )
    return list(result.data)


def legacy_person_id_of(customer: stripe.Customer) -> Optional[str]:
    metadata = customer.metadata.to_dict() if customer.metadata else {}
    return metadata.get(LEGACY_PERSON_ID_METADATA_KEY)


# ---- Donors ----


class DonorOutcome(enum.Enum):
    LABELED = "labeled"
    ALREADY_LABELED = "already labeled"
    CREATED = "created"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class DonorResult:
    outcome: DonorOutcome
    customer_ids: tuple[str, ...]
    message: str


def label_or_create_customer(
    client: stripe.StripeClient, donor: LegacyDonor, dry_run: bool
) -> DonorResult:
    """
    Label the donor's copied Customers with their legacy person ID, or create a Customer
    for them if Stripe has none with their email address.

    A created Customer has no saved card, so migrate_recurring_donations will skip any
    plan of theirs until they add one.
    """
    legacy_id = str(donor.legacy_person_id)
    customers = find_customers_by_email(client, donor.email)
    if not customers:
        if dry_run:
            return DonorResult(DonorOutcome.CREATED, (), "would create a Customer")
        customer = client.v1.customers.create(
            params={
                "email": donor.email,
                "name": donor.full_name or None,
                "metadata": {LEGACY_PERSON_ID_METADATA_KEY: legacy_id},
            },
            options={"idempotency_key": f"legacy-donor-{legacy_id}"},
        )
        return DonorResult(
            DonorOutcome.CREATED, (customer.id,), "created a Customer, which has no saved card"
        )

    conflicting = [
        customer for customer in customers if legacy_person_id_of(customer) not in (None, legacy_id)
    ]
    if conflicting:
        details = ", ".join(
            f"{customer.id} (legacy ID {legacy_person_id_of(customer)})" for customer in conflicting
        )
        return DonorResult(
            DonorOutcome.CONFLICT,
            tuple(customer.id for customer in conflicting),
            f"Customers with this email are already labeled as other people: {details}",
        )

    unlabeled = [customer for customer in customers if legacy_person_id_of(customer) is None]
    if not unlabeled:
        return DonorResult(
            DonorOutcome.ALREADY_LABELED,
            tuple(customer.id for customer in customers),
            "already labeled",
        )
    if not dry_run:
        for customer in unlabeled:
            # Stripe merges metadata on update, so the Customer's other keys are kept.
            client.v1.customers.update(
                customer.id, params={"metadata": {LEGACY_PERSON_ID_METADATA_KEY: legacy_id}}
            )
    verb = "would label" if dry_run else "labeled"
    return DonorResult(
        DonorOutcome.LABELED,
        tuple(customer.id for customer in unlabeled),
        f"{verb} {len(unlabeled)} Customer(s)",
    )


# ---- Recurring plans ----


def subscription_payment_params(
    default_payment_method: Optional[str],
    default_source: Optional[str],
    payment_method_ids: list[str],
) -> Optional[dict[str, str]]:
    """
    The payment parameters a new subscription for a Customer needs, or None if the
    Customer has no card that can be charged without asking them which one.

    An empty dict means Stripe will charge the Customer's own default, which a copied
    Customer may hold either as a PaymentMethod or as a legacy card source.
    """
    if default_payment_method or default_source:
        return {}
    if len(payment_method_ids) == 1:
        return {"default_payment_method": payment_method_ids[0]}
    return None


def first_billing_timestamp(next_bill_date: datetime.date, time_zone: str) -> int:
    """The Unix time at which a migrated subscription should first bill."""
    first_billing = datetime.datetime.combine(
        next_bill_date, FIRST_BILLING_TIME, tzinfo=zoneinfo.ZoneInfo(time_zone)
    )
    return int(first_billing.timestamp())


@dataclass(frozen=True)
class DonationPage:
    """The PaymentPage whose renewals a migrated subscription will be recorded against."""

    id: int
    url: str
    title: str


class PlanOutcome(enum.Enum):
    CREATED = "created"
    ALREADY_MIGRATED = "already migrated"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PlanResult:
    outcome: PlanOutcome
    message: str
    subscription_id: Optional[str] = None


def _skip(message: str) -> PlanResult:
    return PlanResult(PlanOutcome.SKIPPED, message)


def find_migrated_subscription(
    client: stripe.StripeClient, customer_id: str, legacy_plan_id: int
) -> Optional[stripe.Subscription]:
    subscriptions = client.v1.subscriptions.list(
        params={"customer": customer_id, "status": "all", "limit": STRIPE_PAGE_LIMIT}
    )
    for subscription in subscriptions.auto_paging_iter():
        metadata = subscription.metadata.to_dict() if subscription.metadata else {}
        if metadata.get(LEGACY_PLAN_ID_METADATA_KEY) == str(legacy_plan_id):
            return subscription
    return None


def payment_params_for_customer(
    client: stripe.StripeClient, customer: stripe.Customer
) -> Optional[dict[str, str]]:
    invoice_settings = customer.invoice_settings
    default_payment_method = invoice_settings.default_payment_method if invoice_settings else None
    payment_methods = client.v1.customers.payment_methods.list(
        customer.id, params={"limit": STRIPE_PAGE_LIMIT}
    )
    return subscription_payment_params(
        default_payment_method,
        customer.default_source,
        [payment_method.id for payment_method in payment_methods.data],
    )


def create_subscription_for_plan(
    client: stripe.StripeClient,
    customer: stripe.Customer,
    plan: RecurringPlan,
    page: DonationPage,
    currency: str,
    time_zone: str,
    today: datetime.date,
    dry_run: bool,
) -> PlanResult:
    """
    Recreate a legacy recurring plan as a subscription for the Customer, first billing
    on the plan's next bill date and nothing before it.
    """
    if plan.status != ACTIVE_STATUS:
        return _skip(f"it is {plan.status}")
    frequency = FREQUENCY_BY_INTERVAL.get(plan.interval or "")
    if frequency is None:
        return _skip(f"its billing interval ({plan.interval}) cannot be recorded by the webhook")
    if plan.next_bill_date is None:
        return _skip("it has no next bill date")
    if plan.next_bill_date <= today:
        return _skip(
            f"its next bill date ({plan.next_bill_date}) is not after today, so the legacy "
            "CRM may already have charged it; fetch the listing again"
        )
    if not customer.email:
        return _skip(f"Customer {customer.id} has no email, which the webhook needs")

    migrated = find_migrated_subscription(client, customer.id, plan.legacy_plan_id)
    if migrated is not None:
        return PlanResult(
            PlanOutcome.ALREADY_MIGRATED,
            f"already migrated as {migrated.id}",
            subscription_id=migrated.id,
        )

    payment_params = payment_params_for_customer(client, customer)
    if payment_params is None:
        return _skip(
            f"Customer {customer.id} has no default payment method and not exactly one "
            "saved card"
        )

    first_billing = first_billing_timestamp(plan.next_bill_date, time_zone)
    if dry_run:
        return PlanResult(
            PlanOutcome.CREATED,
            f"would create a {frequency} subscription of {plan.amount_cents} cents for "
            f"{customer.id}, first billing on {plan.next_bill_date}",
        )

    subscription = _create_migrated_subscription(
        client,
        customer,
        legacy_plan_id=plan.legacy_plan_id,
        legacy_person_id=str(plan.legacy_person_id),
        donor_email=customer.email,
        amount_cents=plan.amount_cents,
        interval=plan.interval,
        interval_count=1,
        frequency=frequency,
        first_billing=first_billing,
        payment_params=payment_params,
        page=page,
        currency=currency,
    )
    return PlanResult(
        PlanOutcome.CREATED,
        f"created {subscription.id}, first billing on {plan.next_bill_date}",
        subscription_id=subscription.id,
    )


def _create_migrated_subscription(
    client: stripe.StripeClient,
    customer: stripe.Customer,
    legacy_plan_id: int,
    legacy_person_id: Optional[str],
    donor_email: str,
    amount_cents: int,
    interval: str,
    interval_count: int,
    frequency: str,
    first_billing: int,
    payment_params: dict[str, str],
    page: DonationPage,
    currency: str,
) -> stripe.Subscription:
    """
    Create a subscription that invoices nothing until first_billing (a Unix time), where
    its first full payment falls.

    The metadata matches what create_payment_intent puts on subscriptions made through
    a PaymentPage, so that the renewal webhook records each payment as a Donation.
    """
    metadata = {
        "page_id": str(page.id),
        "page_url": page.url,
        "frequency": frequency,
        "donor_email": donor_email,
        LEGACY_PLAN_ID_METADATA_KEY: str(legacy_plan_id),
    }
    if legacy_person_id:
        metadata[LEGACY_PERSON_ID_METADATA_KEY] = legacy_person_id
    price = client.v1.prices.create(
        params={
            "unit_amount": amount_cents,
            "currency": currency,
            "recurring": {"interval": interval, "interval_count": interval_count},
            "product_data": {"name": f"Recurring donation — {page.title}"},
        },
        options={"idempotency_key": f"legacy-recurring-donation-price-{legacy_plan_id}"},
    )
    return client.v1.subscriptions.create(
        params={
            "customer": customer.id,
            "items": [{"price": price.id}],
            "billing_cycle_anchor": first_billing,
            "proration_behavior": "none",
            "off_session": True,
            "metadata": metadata,
            **payment_params,
        },
        options={"idempotency_key": f"legacy-recurring-donation-{legacy_plan_id}"},
    )


# ---- Recurring plans exported as CSV ----


def _retrieve_or_none(retrieve, object_id: str):
    """The Stripe object with this ID, or None if this account has no such object."""
    try:
        found = retrieve(object_id)
    except stripe.InvalidRequestError as exc:
        if exc.code == "resource_missing":
            return None
        raise
    # A deleted Customer is still returned, flagged as deleted.
    return None if getattr(found, "deleted", False) else found


def label_customer(
    client: stripe.StripeClient,
    customer_id: str,
    name: str,
    email: str,
    legacy_person_id: int,
) -> Optional[str]:
    """
    Record who a Customer is: their legacy person ID, and their name and email address
    wherever the Customer has none. Customers that Stripe copied from the legacy CRM's
    account carry neither name nor email.

    Returns why the Customer was left alone, or None once it is labeled. A Customer that
    is already labeled as a different person is never relabeled.
    """
    customer = _retrieve_or_none(client.v1.customers.retrieve, customer_id)
    if customer is None:
        return f"Customer {customer_id} is not in this Stripe account"
    existing = legacy_person_id_of(customer)
    if existing is not None and existing != str(legacy_person_id):
        return f"Customer {customer_id} is already labeled as legacy person {existing}"
    params: dict = {"metadata": {LEGACY_PERSON_ID_METADATA_KEY: str(legacy_person_id)}}
    if not customer.name:
        params["name"] = name
    if not customer.email:
        params["email"] = email
    client.v1.customers.update(customer_id, params=params)
    return None


def create_subscription_for_exported_plan(
    client: stripe.StripeClient,
    plan: ExportedRecurringPlan,
    page: DonationPage,
    currency: str,
    now: datetime.datetime,
    dry_run: bool,
    email_of_legacy_person: Callable[[int], Optional[str]] = lambda legacy_person_id: None,
    legacy_person_id_override: Optional[int] = None,
) -> PlanResult:
    """
    Recreate a plan from the recurring donations CSV as a subscription for its copied
    Customer, charging its copied card and first billing at the moment the legacy CRM
    would next have charged it, and nothing before then. The card is the Customer's own,
    since the CSV's PaymentMethod ID is not kept when Stripe copies a card.

    Unlike create_subscription_for_plan, this needs no Customer labeled by
    migrate_donors, since the CSV names the Customer and card. Any legacy person ID
    that migrate_donors put on the Customer is copied onto the subscription.

    The webhook needs the donor's email address. It is the Customer's or, if the Customer
    has none, that of the person whose legacy ID is on the Customer, as found by
    email_of_legacy_person (which returns None for a person it does not know).
    legacy_person_id_override stands in for the legacy person ID on the Customer, for a
    Customer that is about to be labeled (as label_customer does) in a dry run.
    """
    frequency = FREQUENCY_BY_INTERVAL.get(plan.interval or "")
    if frequency is None or plan.interval_count != 1:
        return _skip(
            f"its billing interval (every {plan.interval_count} {plan.interval}) cannot be "
            "recorded by the webhook"
        )
    if plan.next_payment_at <= now:
        return _skip(
            f"its next payment ({plan.next_payment_at:%Y-%m-%d %H:%M} UTC) is not in the "
            "future, so the legacy CRM may already have charged it; export the CSV again"
        )

    customer = _retrieve_or_none(client.v1.customers.retrieve, plan.customer_id)
    if customer is None:
        return _skip(
            f"Customer {plan.customer_id} is not in this Stripe account; has Stripe "
            "finished copying the legacy CRM's Customers?"
        )
    legacy_person_id = (
        str(legacy_person_id_override)
        if legacy_person_id_override is not None
        else legacy_person_id_of(customer)
    )
    donor_email = customer.email
    if not donor_email:
        if legacy_person_id is None:
            return _skip(
                f"Customer {customer.id} has no email, nor a legacy person ID to find one by"
            )
        if not legacy_person_id.isdecimal():
            return _skip(
                f"Customer {customer.id} has a legacy person ID ({legacy_person_id!r}) that "
                "is not a number"
            )
        donor_email = email_of_legacy_person(int(legacy_person_id))
        if not donor_email:
            return _skip(
                f"Customer {customer.id} has no email, and no person with the legacy ID "
                f"{legacy_person_id} has one"
            )

    migrated = find_migrated_subscription(client, customer.id, plan.legacy_plan_id)
    if migrated is not None:
        return PlanResult(
            PlanOutcome.ALREADY_MIGRATED,
            f"already migrated as {migrated.id}",
            subscription_id=migrated.id,
        )

    # The CSV's PaymentMethod ID is the one in the legacy CRM's account, which Stripe does
    # not keep when it copies a card, so the Customer's own cards are what to charge.
    payment_params = payment_params_for_customer(client, customer)
    if payment_params is None:
        return _skip(
            f"Customer {customer.id} has no default card, nor exactly one saved card, to charge"
        )

    next_payment = f"{plan.next_payment_at:%Y-%m-%d %H:%M} UTC"
    if dry_run:
        return PlanResult(
            PlanOutcome.CREATED,
            f"would create a {frequency} subscription of {plan.amount_cents} cents for "
            f"{customer.id}, first billing at {next_payment}",
        )

    subscription = _create_migrated_subscription(
        client,
        customer,
        legacy_plan_id=plan.legacy_plan_id,
        legacy_person_id=legacy_person_id,
        donor_email=donor_email,
        amount_cents=plan.amount_cents,
        interval=plan.interval,
        interval_count=plan.interval_count,
        frequency=frequency,
        first_billing=int(plan.next_payment_at.timestamp()),
        payment_params=payment_params,
        page=page,
        currency=currency,
    )
    return PlanResult(
        PlanOutcome.CREATED,
        f"created {subscription.id}, first billing at {next_payment}",
        subscription_id=subscription.id,
    )
