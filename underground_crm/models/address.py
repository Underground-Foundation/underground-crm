import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from underground_crm import addressr


def _normalized_one_line(text: str) -> str:
    """A single-line address reduced to its comparable core: commas and
    surplus whitespace collapsed and case folded away. Commas are ignored
    because Address.one_line comma-separates every component while Addressr's
    sla runs "LOCALITY STATE POSTCODE" together, and that difference does not
    make them different addresses."""
    return " ".join(text.replace(",", " ").split()).casefold()


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
    gnaf_id = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("G-NAF ID"),
        help_text=_(
            "The G-NAF Address Detail PID of the address Addressr matched "
            "(e.g. GANSW705239062). Set whenever the address was resolved "
            "through Addressr, whether from an autocomplete selection or a "
            "background geocode."
        ),
    )

    class Meta:
        verbose_name = "address"
        verbose_name_plural = "addresses"

    def __str__(self):
        # Notice we omit the country
        parts = [self.line1, self.line2, self.line3, self.city, self.state, self.postcode]
        return ", ".join(p for p in parts if p) or "(empty address)"

    @property
    def one_line(self):
        return str(self)

    def is_equivalent(self, other: "Address") -> bool:
        """True if both addresses describe the same physical location."""
        if self.gnaf_id and other.gnaf_id:
            # G-NAF IDs identify addresses exactly, so when both records carry
            # one, the IDs alone decide — differing text is just formatting.
            return self.gnaf_id == other.gnaf_id
        fields = ("line1", "line2", "line3", "city", "state", "postcode", "country_code")
        return all((getattr(self, f) or "") == (getattr(other, f) or "") for f in fields)

    @classmethod
    def from_components(
        cls,
        *,
        line1: str = "",
        line2: str = "",
        line3: str = "",
        city: str = "",
        state: str = "",
        postcode: str = "",
        gnaf_id: str | None = None,
    ) -> "Address":
        """
        Build an unsaved Address from the component values of a structured
        address question (see StructuredAddressWidget in widgets.py).

        When the visitor picked an autocomplete suggestion, its G-NAF ID
        arrives as gnaf_id and is verified against Addressr: if the ID still
        describes the submitted line1/suburb/state/postcode, the record
        carries the match's geocode. Lines 2 and 3 are deliberately left out
        of that check — they hold supplementary delivery detail (a unit, a
        "c/-" line) that does not move the property, so the coordinates
        survive them. The ID itself is stored only while those lines are
        empty: with extra lines the record no longer names exactly the
        address the ID identifies, so keeping the ID would over-claim,
        whereas the geocode remains honest.

        When the ID cannot be honored at all — no pick, an unknown ID, a
        stale ID left over from a pick whose first line or locality the
        visitor then edited by hand (possible when the page's script never
        ran), or Addressr being unavailable — the components are stored
        exactly as typed on an unverified record, and the background
        geocoding flow (signals.on_address_post_save, or the
        geocode_addresses command) verifies the address later.
        """
        match: addressr.Geocode | None = None
        if gnaf_id:
            match = addressr.geocode_by_id(gnaf_id)
            if match is not None:
                submitted = ", ".join(p for p in (line1, city, state, postcode) if p)
                if match.sla is None or _normalized_one_line(match.sla) != _normalized_one_line(
                    submitted
                ):
                    match = None
        has_extra_lines = bool(line2 or line3)
        return cls(
            line1=line1 or None,
            line2=line2 or None,
            line3=line3 or None,
            city=city or None,
            state=state or None,
            postcode=postcode or None,
            latitude=match.latitude if match else None,
            longitude=match.longitude if match else None,
            geocode_reliability=match.reliability if match else None,
            gnaf_id=match.gnaf_id if match and not has_extra_lines else None,
        )
