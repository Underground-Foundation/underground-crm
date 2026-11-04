import logging
from argparse import ArgumentParser
from typing import Optional

from django.core.management.base import BaseCommand, CommandError
from wagtail.models import Site

from underground_email.tasks import register_email_failure_webhooks

logger = logging.getLogger(__name__)


def _find_site(domain: Optional[str]) -> Site:
    """The site with the given domain name, or the default site when none is given."""
    if domain is None:
        site = Site.objects.filter(is_default_site=True).first()
        if site is None:
            raise CommandError("There is no default site, so please name one with --domain.")
        return site

    matching_sites = list(Site.objects.filter(hostname__iexact=domain))
    if not matching_sites:
        known_domains = ", ".join(
            Site.objects.order_by("hostname").values_list("hostname", flat=True)
        )
        raise CommandError(
            f"There is no site with the domain '{domain}'. Known domains: {known_domains}."
        )
    if len(matching_sites) > 1:
        # A Wagtail Site is unique by its hostname and port together, so a domain alone can be
        # ambiguous. Each port gives a different webhook URL, so we must not guess between them.
        root_urls = ", ".join(sorted(site.root_url for site in matching_sites))
        raise CommandError(f"The domain '{domain}' matches more than one site: {root_urls}.")
    return matching_sites[0]


class Command(BaseCommand):
    help = "Register smtp2go webhooks to receive delivery-failure notifications (spam, bounce, reject, unsubscribe)."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--domain",
            default=None,
            help="Domain name of the Wagtail site that will receive the webhooks (default: the default site).",
        )

    def handle(self, *args, **options) -> None:
        site = _find_site(options["domain"])
        register_email_failure_webhooks(site)
        logger.info("smtp2go webhooks registered successfully for %s.", site.root_url)
