"""
Geocode unverified Address records using the Addressr API.

Addressr has no batch endpoint, so requests are issued concurrently within each
batch using a thread pool. Results are written back via bulk_update, which does
not trigger Django signals.

Usage:
    python manage.py geocode_addresses
    python manage.py geocode_addresses --batch-size 20
    python manage.py geocode_addresses --limit 500
    python manage.py geocode_addresses --correct-address-fields
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand

from underground_crm import addressr as addressr_client
from underground_crm.models.address import Address

# Fields overwritten from the Addressr match when --correct-address-fields is set.
_CORRECTABLE_FIELDS = ("line1", "city", "state", "postcode")

# The leading street-number token of a line1 string, e.g. "1" or "12A" in
# "1 Cook Rd" / "12A Smith St". Used to make sure a line1 correction only ever
# fixes the street name/type spelling, never the number the user actually typed.
_STREET_NUMBER_RE = re.compile(r"^\s*(\d+[A-Za-z]?)\b")


def _street_number(line1: str | None) -> str | None:
    """Return the leading street-number token from a line1 string, or None if it has none."""
    if not line1:
        return None
    match = _STREET_NUMBER_RE.match(line1)
    return match.group(1).upper() if match else None


def _apply_corrections(address: Address, matched: addressr_client.StructuredAddress) -> list[str]:
    """
    Overwrite any of address's line1/city/state/postcode that differ from the
    matched Addressr address, leaving line2/line3 (unit/level detail Addressr
    doesn't return here) untouched. Returns the names of the fields changed.

    line1 is only corrected when its leading street number matches the
    address's existing one — Addressr's fuzzy search can match a nearby
    property with a different number, and that number must never be silently
    swapped in (e.g. "1 Cok Rd" -> "1 Cook Road" is fine; "1 Cok Rd" -> "1A
    Cook Road" is not).
    """
    changed = []
    for field in _CORRECTABLE_FIELDS:
        new_value = getattr(matched, field)
        if not new_value or new_value == getattr(address, field):
            continue
        if field == "line1" and _street_number(address.line1) != _street_number(new_value):
            continue
        setattr(address, field, new_value)
        changed.append(field)
    return changed


def _geocode_one(address: Address):
    query = " ".join(
        p
        for p in [
            address.line1,
            address.line2,
            address.line3,
            address.city,
            address.state,
            address.postcode,
        ]
        if p
    )
    if not query:
        return address, None
    return address, addressr_client.geocode(query)


class Command(BaseCommand):
    help = "Geocode unverified Address records via Addressr."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of addresses to geocode concurrently per batch (default: 50).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum total number of addresses to process (default: all).",
        )
        parser.add_argument(
            "--correct-address-fields",
            action="store_true",
            default=False,
            help=(
                "Also overwrite line1/city/state/postcode with the values from "
                "the matched Addressr address, in case the imported legacy "
                "address had a wrong street name, suburb or postcode."
            ),
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        limit = options["limit"]
        correct_address_fields = options["correct_address_fields"]

        qs = Address.objects.filter(latitude__isnull=True).order_by("id")
        if limit:
            qs = qs[:limit]

        total = qs.count()
        if total == 0:
            self.stdout.write("No unverified addresses found.")
            return

        self.stdout.write(f"Geocoding {total} address(es) in batches of {batch_size}…")

        update_fields = ["latitude", "longitude", "geocode_reliability"]
        if correct_address_fields:
            update_fields += list(_CORRECTABLE_FIELDS)

        processed = geocoded = skipped = failed = corrected = 0

        for batch_start in range(0, total, batch_size):
            batch = list(qs[batch_start : batch_start + batch_size])
            to_update: list[Address] = []

            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = {executor.submit(_geocode_one, addr): addr for addr in batch}
                for future in as_completed(futures):
                    address, result = future.result()
                    processed += 1
                    if result is None:
                        if not any([address.line1, address.city, address.state, address.postcode]):
                            skipped += 1
                        else:
                            failed += 1
                        continue
                    address.latitude = result.latitude
                    address.longitude = result.longitude
                    address.geocode_reliability = result.reliability
                    if correct_address_fields and result.address:
                        changed = _apply_corrections(address, result.address)
                        if changed:
                            corrected += 1
                            self.stdout.write(
                                f"  Corrected {', '.join(changed)} for address {address.pk}"
                            )
                    to_update.append(address)
                    geocoded += 1

            if to_update:
                Address.objects.bulk_update(to_update, update_fields)

            self.stdout.write(f"  {processed}/{total}")

        summary = f"Done. Geocoded: {geocoded}, no content: {skipped}, no result: {failed}."
        if correct_address_fields:
            summary += f" Corrected: {corrected}."
        self.stdout.write(self.style.SUCCESS(summary))
