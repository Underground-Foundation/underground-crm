import uuid

from django.core.exceptions import ValidationError
from django.db import models
from wagtail.admin.panels import FieldPanel
from wagtail.models import Page


class UrlRedirection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    old_path = models.CharField(
        max_length=255,
        unique=True,
        help_text="The URL path to redirect from, starting with a slash (e.g. /old-page/).",
    )
    destination_page = models.ForeignKey(
        Page,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="incoming_redirections",
        help_text="The on-site page to redirect visitors to.",
    )
    destination_url = models.CharField(
        max_length=2048,
        blank=True,
        help_text="External URL to redirect to. Use this when redirecting outside the site.",
    )
    is_permanent = models.BooleanField(
        default=True,
        verbose_name="Permanent redirection",
        help_text=(
            "Send a 301 permanent redirection. Uncheck to send a 302 temporary redirection instead."
        ),
    )

    panels = [
        FieldPanel("old_path"),
        FieldPanel("destination_page"),
        FieldPanel("destination_url"),
        FieldPanel("is_permanent"),
    ]

    def clean(self) -> None:
        if not self.destination_page_id and not self.destination_url:
            raise ValidationError("Specify either a destination page or an external URL.")
        if self.destination_page_id and self.destination_url:
            raise ValidationError("Specify either a destination page or an external URL, not both.")
        if self.old_path and not self.old_path.startswith("/"):
            self.old_path = "/" + self.old_path

    def get_destination_url(self) -> str:
        if self.destination_page_id:
            return self.destination_page.url
        return self.destination_url

    def __str__(self) -> str:
        return self.old_path

    class Meta:
        verbose_name = "Redirection"
        verbose_name_plural = "Redirections"
