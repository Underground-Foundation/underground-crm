"""
Geocode unverified Address records using the Addressr API.

Addressr has no batch endpoint, so requests are issued concurrently within each
batch using a thread pool. Results are written back via bulk_update, which does
not trigger Django signals.

Usage:
    python manage.py geocode_addresses
    python manage.py geocode_addresses --batch-size 20
    python manage.py geocode_addresses --limit 500
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand

from underground_crm import addressr as addressr_client
from underground_crm.models.address import Address


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

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        limit = options["limit"]

        qs = Address.objects.filter(latitude__isnull=True).order_by("id")
        if limit:
            qs = qs[:limit]

        total = qs.count()
        if total == 0:
            self.stdout.write("No unverified addresses found.")
            return

        self.stdout.write(f"Geocoding {total} address(es) in batches of {batch_size}…")

        processed = geocoded = skipped = failed = 0

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
                    to_update.append(address)
                    geocoded += 1

            if to_update:
                Address.objects.bulk_update(
                    to_update,
                    ["latitude", "longitude", "geocode_reliability"],
                )

            self.stdout.write(f"  {processed}/{total}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Geocoded: {geocoded}, no content: {skipped}, no result: {failed}."
            )
        )
