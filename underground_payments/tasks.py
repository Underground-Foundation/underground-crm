import datetime
import logging

from django.contrib.auth import get_user_model
from django.utils import timezone
from djmoney.money import Money

logger = logging.getLogger(__name__)


def _fit(value: str, max_len: int, field: str) -> str:
    if len(value) > max_len:
        logger.warning(
            "Truncating metadata field %r from %d to %d chars", field, len(value), max_len
        )
        return value[:max_len]
    return value


_CURRENCY = "AUD"


def handle_successful_payment(payment_intent_id: str, amount_cents: int, metadata: dict) -> None:
    """
    Post-payment side effects triggered by a Stripe payment_intent.succeeded webhook:
    create a Donation record, apply donor_tag, update Person aggregates, and
    optionally send a success email.
    """
    from underground_crm.models import Donation, Engagement
    from underground_crm.models.person import PersonTag

    from .models import PaymentPage

    page_id = metadata.get("page_id")
    donor_email = metadata.get("donor_email", "").strip().lower()
    frequency = metadata.get("frequency", "once")
    page_url = metadata.get("page_url", "")
    first_name = _fit(metadata.get("donor_first_name", "").strip(), 100, "donor_first_name")
    middle_name = _fit(metadata.get("donor_middle_name", "").strip(), 100, "donor_middle_name")
    last_name = _fit(metadata.get("donor_last_name", "").strip(), 100, "donor_last_name")
    phone = _fit(metadata.get("donor_phone", "").strip(), 30, "donor_phone")
    address_line1 = _fit(
        metadata.get("donor_address_line1", "").strip(), 200, "donor_address_line1"
    )
    address_line2 = _fit(
        metadata.get("donor_address_line2", "").strip(), 200, "donor_address_line2"
    )
    address_line3 = _fit(
        metadata.get("donor_address_line3", "").strip(), 200, "donor_address_line3"
    )
    city = _fit(metadata.get("donor_city", "").strip(), 100, "donor_city")
    state = _fit(metadata.get("donor_state", "").strip(), 100, "donor_state")
    postcode = _fit(metadata.get("donor_postcode", "").strip(), 20, "donor_postcode")
    country = _fit(metadata.get("donor_country", "").strip(), 2, "donor_country")

    page = (
        PaymentPage.objects.select_related("donor_tag", "success_email__sender__sender")
        .filter(pk=page_id)
        .first()
    )
    if not page:
        logger.error(
            "PaymentPage pk=%s not found for payment_intent %s", page_id, payment_intent_id
        )
        return

    if not donor_email:
        logger.warning(
            "No donor email in payment_intent %s metadata; skipping post-donation processing.",
            payment_intent_id,
        )
        return

    # Guard against duplicate webhook delivery.
    if Donation.objects.filter(stripe_payment_id=payment_intent_id).exists():
        logger.info("payment_intent %s already recorded; skipping.", payment_intent_id)
        return

    from underground_crm.models import Address

    User = get_user_model()
    person = User.objects.filter(email__iexact=donor_email).first()
    if not person:
        person = User(
            email=donor_email,
            first_name=first_name or None,
            last_name=last_name or None,
        )
        person.set_unusable_password()
        person.save()
        logger.info("Created new Person for donor %s", donor_email)
    else:
        # Fill in blanks but never overwrite existing data.
        fill = {}
        if first_name and not person.first_name:
            fill["first_name"] = first_name
        if last_name and not person.last_name:
            fill["last_name"] = last_name
        if fill:
            for k, v in fill.items():
                setattr(person, k, v)
            person.save(update_fields=list(fill.keys()))

    # Store or update billing address if the donation supplied one.
    if address_line1 or city or state or postcode:
        if person.billing_address_id:
            logger.info(
                "Updating billing address for %s via payment_intent %s",
                donor_email,
                payment_intent_id,
            )
            addr = person.billing_address
            addr.line1 = address_line1 or None
            addr.line2 = address_line2 or None
            addr.line3 = address_line3 or None
            addr.city = city or None
            addr.state = state or None
            addr.postcode = postcode or None
            if country:
                addr.country_code = country
            addr.save(
                update_fields=[
                    "line1",
                    "line2",
                    "line3",
                    "city",
                    "state",
                    "postcode",
                    "country_code",
                ]
            )
        else:
            addr = Address.objects.create(
                line1=address_line1 or None,
                line2=address_line2 or None,
                line3=address_line3 or None,
                city=city or None,
                state=state or None,
                postcode=postcode or None,
                country_code=country or "AU",
            )
            person.billing_address = addr
            person.save(update_fields=["billing_address"])

    amount = Money(amount_cents / 100, _CURRENCY)
    is_recurring = frequency in ("monthly", "annual")
    now = timezone.now()

    donation = Donation.objects.create(
        person=person,
        amount=amount,
        stripe_payment_id=payment_intent_id,
        is_recurring=is_recurring,
        donated_at=now,
        page_url=page_url,
        metadata={"frequency": frequency, "page_id": str(page_id)},
    )

    # Update Person donation aggregates.
    update_fields = ["donations_count", "donations_amount", "is_donor", "last_donated_at"]
    person.donations_count = (person.donations_count or 0) + 1
    person.donations_amount = (person.donations_amount or Money(0, _CURRENCY)) + amount
    person.is_donor = True
    person.last_donated_at = now
    if not person.first_donated_at:
        person.first_donated_at = now
        update_fields.append("first_donated_at")
    person.save(update_fields=update_fields)

    Engagement.objects.create(
        person=person,
        action_type=Engagement.DONATED,
        page_title=page.title,
        page_url=page_url,
        metadata={
            "page_id": str(page_id),
            "amount_cents": amount_cents,
            "frequency": frequency,
            "stripe_payment_id": payment_intent_id,
        },
    )

    if page.donor_tag_id:
        PersonTag.objects.get_or_create(person=person, tag_id=page.donor_tag_id)
        logger.info("Applied tag %s to %s", page.donor_tag_id, donor_email)

    if page.success_email_id:
        is_first = (
            not Donation.objects.filter(person=person, page_url=page_url)
            .exclude(pk=donation.pk)
            .exists()
        )
        if page.email_every_time or is_first:
            _send_success_email(page.success_email, person)


def handle_successful_subscription_renewal(
    payment_intent_id: str, amount_cents: int, subscription_id: str
) -> None:
    """Handle recurring subscription payments after the first."""
    import stripe
    from django.conf import settings

    client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)
    try:
        subscription = client.subscriptions.retrieve(subscription_id)
        metadata = dict(subscription.get("metadata") or {})
    except stripe.StripeError:
        logger.exception("Could not retrieve subscription %s", subscription_id)
        return

    if not metadata.get("page_id"):
        return

    # Determine frequency from the subscription interval.
    interval = "once"
    try:
        interval_unit = subscription["items"]["data"][0]["price"]["recurring"]["interval"]
        interval = "monthly" if interval_unit == "month" else "annual"
    except (KeyError, IndexError, TypeError):
        pass

    metadata.setdefault("frequency", interval)
    handle_successful_payment(payment_intent_id, amount_cents, metadata)


def _send_success_email(campaign, person) -> None:
    from smtp2go.core import Smtp2goClient

    from underground_email.tasks import _api_key, _render_email_html

    if not campaign.sender_id:
        logger.warning(
            "Campaign %s has no sender configured; skipping success email.", campaign.utm_id
        )
        return

    sender_person = campaign.sender.sender
    sender_str = f"{sender_person.full_name} <{sender_person.email}>"
    client = Smtp2goClient(api_key=_api_key())
    response = client.send(
        sender=sender_str,
        recipients=[f"{person.full_name} <{person.email}>"],
        subject=campaign.subject,
        html=_render_email_html(campaign, person),
    )
    if not response.success:
        logger.error(
            "Failed to send success email to %s for campaign %s: %s",
            person.email,
            campaign.utm_id,
            response.errors,
        )
    else:
        logger.info("Sent success email to %s (campaign %s).", person.email, campaign.utm_id)
