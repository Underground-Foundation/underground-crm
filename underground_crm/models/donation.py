import uuid

from django.db import models
from djmoney.models.fields import MoneyField


class Donation(models.Model):
    """
    A single donation transaction. Aggregate totals are also denormalised onto
    Person for fast querying; those fields are updated whenever a Donation is
    saved or deleted.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(
        "underground_crm.Person",
        on_delete=models.CASCADE,
        related_name="donations",
    )
    amount = MoneyField(max_digits=14, decimal_places=2, default_currency="AUD")
    stripe_payment_id = models.CharField(
        max_length=200, blank=True, db_index=True, help_text="Stripe PaymentIntent or Charge ID."
    )
    is_recurring = models.BooleanField(
        default=False, help_text="Whether this is part of a recurring (monthly) donation series."
    )
    donated_at = models.DateTimeField(db_index=True)
    page_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="URL of the donation page that generated this transaction.",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-donated_at"]

    def __str__(self):
        return f"{self.person} — {self.amount} on {self.donated_at:%Y-%m-%d}"
