"""
Management command for email campaign operations.

Usage:
    python manage.py email_campaigns list
    python manage.py email_campaigns process-results --utm-id <utm_id>
    python manage.py email_campaigns process-results --utm-id <utm_id> --dry-run

The process-results subcommand polls SMTP2Go for activity events on the given
campaign and records engagements / rejects in the database. It is safe to run
more than once — all writes are deduplicated against existing records.
"""

from django.core.management.base import BaseCommand, CommandError

from underground_email.api import SMTP2GoEventType
from underground_email.models import EMAIL_STATES, EmailCampaign
from underground_email.tasks import get_email_results_and_save_engagements

_STATE_LABELS: dict[int, str] = dict(EMAIL_STATES)

_PROCESSABLE_EVENT_TYPES = [
    SMTP2GoEventType.OPENED,
    SMTP2GoEventType.CLICKED,
    SMTP2GoEventType.SOFT_BOUNCED,
    SMTP2GoEventType.HARD_BOUNCED,
    SMTP2GoEventType.REJECTED,
    SMTP2GoEventType.SPAM,
    SMTP2GoEventType.UNSUBSCRIBED,
]

# (header text, model attribute, column width)
_COLUMNS = [
    ("UTM ID", "utm_id", 10),
    ("Subject", "subject", 36),
    ("State", "state", 9),
    ("Sent on", "sending_date", 16),
    ("Sent", "sent_count", 7),
    ("Opened", "opened_count", 7),
    ("Clicked", "clicked_count", 7),
    ("Spam", "spam_count", 7),
    ("Unsubs", "unsubscription_count", 7),
]


def _cell(value, width: int) -> str:
    text = "—" if value is None else str(value)
    if len(text) > width:
        text = text[: width - 1] + "…"
    return text.ljust(width)


def _separator() -> str:
    return "+-" + "-+-".join("-" * w for _, _, w in _COLUMNS) + "-+"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


class Command(BaseCommand):
    help = "Email campaign operations: list campaigns or process engagement results from SMTP2Go."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="subcommand", required=True)

        subparsers.add_parser("list", help="Print all email campaigns as a table.")

        process_parser = subparsers.add_parser(
            "process-results",
            help="Fetch engagement events from SMTP2Go and record them against the campaign.",
        )
        process_parser.add_argument(
            "--utm-id",
            required=True,
            help="The UTM identifier of the campaign to process.",
        )
        process_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and report events without writing to the database.",
        )

    def handle(self, *args, **options):
        if options["subcommand"] == "list":
            self._handle_list()
        else:
            self._handle_process_results(
                utm_id=options["utm_id"],
                dry_run=options["dry_run"],
            )

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    def _handle_list(self) -> None:
        campaigns = list(EmailCampaign.objects.select_related("sender").order_by("-sending_date"))
        sep = _separator()

        self.stdout.write(sep)
        self.stdout.write(_row([_cell(label, w) for label, _, w in _COLUMNS]))
        self.stdout.write(sep)

        for campaign in campaigns:
            cells = []
            for _, attr, width in _COLUMNS:
                val = getattr(campaign, attr)
                if attr == "state":
                    val = _STATE_LABELS.get(val, val)
                elif attr == "sending_date" and val is not None:
                    val = val.strftime("%Y-%m-%d %H:%M")
                cells.append(_cell(val, width))
            self.stdout.write(_row(cells))

        self.stdout.write(sep)
        self.stdout.write(f"  {len(campaigns)} campaign(s).")

    # ------------------------------------------------------------------
    # process-results
    # ------------------------------------------------------------------

    def _handle_process_results(self, utm_id: str, dry_run: bool) -> None:
        try:
            campaign = EmailCampaign.objects.get(utm_id=utm_id)
        except EmailCampaign.DoesNotExist:
            raise CommandError(f"No campaign found with UTM ID '{utm_id}'.")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no database writes will occur."))

        self.stdout.write(f"Processing results for '{campaign.subject}' ({utm_id}).")

        for event_type in _PROCESSABLE_EVENT_TYPES:
            self.stdout.write(f"Fetching {event_type.value} events…")
            try:
                get_email_results_and_save_engagements(
                    campaign_utm_id=utm_id,
                    event_types=event_type,
                    persist=not dry_run,
                )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"[error] {event_type.value}: {exc}"))

        self.stdout.write(self.style.SUCCESS("Done."))
