from django.core.management.base import BaseCommand

from underground_email.tasks import register_email_failure_webhooks


class Command(BaseCommand):
    help = "Register smtp2go webhooks to receive delivery-failure notifications (spam, bounce, reject, unsubscribe)."

    def handle(self, *args, **options) -> None:
        register_email_failure_webhooks()
        self.stdout.write(self.style.SUCCESS("smtp2go webhooks registered successfully."))
