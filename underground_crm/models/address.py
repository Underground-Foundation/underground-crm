import uuid

from django.db import models


class Address(models.Model):
    """A physical or postal address, reusable across multiple person address roles."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line1 = models.CharField(max_length=200, blank=True)
    line2 = models.CharField(max_length=200, blank=True)
    line3 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    country_code = models.CharField(max_length=2, default="AU")

    class Meta:
        verbose_name = "address"
        verbose_name_plural = "addresses"

    def __str__(self):
        parts = [self.line1, self.city, self.state, self.postcode]
        return ", ".join(p for p in parts if p) or "(empty address)"

    @property
    def one_line(self):
        return str(self)
