import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel
from wagtail.models import Orderable

from .pages import FeedPage


class FeedSubscription(Orderable):
    """
    An external RSS or Atom feed whose items a FeedPage displays alongside
    its own — for example another party's blog or event calendar. Editors
    manage subscriptions inline on the FeedPage; the items are fetched on
    demand when a listing renders, through a TTL cache (see
    underground_crm.external_feeds), so nothing is stored locally.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page = ParentalKey(FeedPage, on_delete=models.CASCADE, related_name="subscriptions")
    title = models.CharField(
        max_length=255,
        verbose_name=_("Source name"),
        help_text=_("Shown beside each item — usually the other organization's name."),
    )
    url = models.URLField(
        max_length=2000,
        verbose_name=_("Feed URL"),
        help_text=_("The address of the RSS or Atom feed."),
    )

    panels = [
        FieldPanel("title"),
        FieldPanel("url"),
    ]

    class Meta(Orderable.Meta):
        verbose_name = _("feed subscription")
        verbose_name_plural = _("feed subscriptions")

    def __str__(self) -> str:
        return f"{self.title} ({self.url})"
