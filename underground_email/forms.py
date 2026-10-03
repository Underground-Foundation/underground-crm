from typing import Iterable, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet
from wagtail.admin.forms import WagtailAdminModelForm


def sender_candidates(
    allowed_hosts: Optional[Iterable[str]] = None, keep_pk: Optional[object] = None
) -> QuerySet:
    """
    Return the users who may be chosen as an email sender: staff, plus anybody whose
    email address is at one of `allowed_hosts` (default: settings.ALLOWED_HOSTS).

    An allowed host with a leading dot (Django's subdomain wildcard, such as
    ".example.org") matches the bare domain and all of its subdomains.  The bare "*"
    wildcard is ignored, since it names no domain.  `keep_pk` is always included, so that
    editing an existing sender never loses its current user.
    """
    hosts = settings.ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts
    condition = Q(is_staff=True)
    if keep_pk is not None:
        condition |= Q(pk=keep_pk)
    for host in hosts:
        domain = host.lstrip(".").lower()
        if not domain or domain == "*":
            continue
        condition |= Q(email__iendswith=f"@{domain}")
        if host.startswith("."):
            condition |= Q(email__iendswith=f".{domain}")
    return get_user_model().objects.filter(condition)


class EmailSenderForm(WagtailAdminModelForm):
    """Restricts the "attributed sender" choices to staff and users at our own domains."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields.get("sender")
        if field is not None:
            # On the edit form, `sender` is the primary key and so already set.
            field.queryset = sender_candidates(keep_pk=self.instance.pk)
