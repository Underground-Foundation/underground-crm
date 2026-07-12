import uuid

from django.db import models

from underground_crm import addressr


class Address(models.Model):
    """A physical or postal address, reusable across multiple person address roles."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line1 = models.CharField(max_length=200, blank=True, null=True, db_index=True)
    line2 = models.CharField(max_length=200, blank=True, null=True, db_index=True)
    line3 = models.CharField(max_length=200, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    state = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    postcode = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    country_code = models.CharField(max_length=2, default="AU", db_index=True)

    # Set by the background geocoding task after Addressr verifies the address.
    # A non-null value indicates the address has been verified and geocoded.
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # Spatial precision of the geocode: 1 (surveyed/exact) to 6 (postcode-region level).
    geocode_reliability = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "address"
        verbose_name_plural = "addresses"

    def __str__(self):
        # Notice we omit the country
        parts = [self.line1, self.city, self.state, self.postcode]
        return ", ".join(p for p in parts if p) or "(empty address)"

    @property
    def one_line(self):
        return str(self)

    def is_equivalent(self, other: "Address") -> bool:
        """True if both addresses describe the same physical location."""
        fields = ("line1", "line2", "line3", "city", "state", "postcode", "country_code")
        return all((getattr(self, f) or "") == (getattr(other, f) or "") for f in fields)

    @classmethod
    def from_one_line(cls, one_line: str) -> "Address":
        """
        Build an unsaved Address from a single-line address string, as typed
        into (or picked from) an Addressr-backed autocomplete input.

        The string is resolved through Addressr so the returned record carries
        the structured components and geocode of the match. When Addressr is
        unavailable or finds no match, the raw string is kept in line1 on an
        unverified record — the geocode_addresses command (with
        --correct-address-fields) repairs such records once Addressr can
        resolve them.
        """
        match = addressr.geocode(one_line)
        if match is None:
            return cls(line1=one_line)

        structured = match.address
        if structured is None:
            return cls(
                line1=one_line,
                latitude=match.latitude,
                longitude=match.longitude,
                geocode_reliability=match.reliability,
            )
        return cls(
            line1=structured.line1,
            city=structured.city,
            state=structured.state,
            postcode=structured.postcode,
            latitude=match.latitude,
            longitude=match.longitude,
            geocode_reliability=match.reliability,
        )
