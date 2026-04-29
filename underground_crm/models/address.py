import uuid

from django.db import models


class Address(models.Model):
    """A physical or postal address, reusable across multiple person address roles."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line1 = models.CharField(max_length=200, blank=True, db_index=True)
    line2 = models.CharField(max_length=200, blank=True, db_index=True)
    line3 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True, db_index=True)
    state = models.CharField(max_length=100, blank=True, db_index=True)
    postcode = models.CharField(max_length=20, blank=True, db_index=True)
    country_code = models.CharField(max_length=2, default="AU", db_index=True)

    class Meta:
        verbose_name = "address"
        verbose_name_plural = "addresses"

    def __str__(self):
        parts = [self.line1, self.city, self.state, self.postcode]
        return ", ".join(p for p in parts if p) or "(empty address)"

    @property
    def one_line(self):
        return str(self)
