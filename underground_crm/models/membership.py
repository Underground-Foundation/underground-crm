import datetime
import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class MembershipType(models.Model):
    """A category of membership offered by the organization (e.g. "Fusion Party", "Pirate Party")."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class MembershipQuerySet(models.QuerySet):
    def current(self, today: datetime.date | None = None) -> "MembershipQuerySet":
        """
        Narrow to the memberships for which Membership.is_current holds: those which
        have not been suspended and have not expired as of ``today``, whether or not
        staff have approved them yet. ``today`` defaults to the current date in the
        site's time zone, and a membership lasts through the whole of its expiry date.
        """
        today = today or timezone.localdate()
        return self.filter(
            Q(expires_on__isnull=True) | Q(expires_on__gte=today), suspended_at__isnull=True
        )

    def active(self, today: datetime.date | None = None) -> "MembershipQuerySet":
        """Narrow to the memberships for which Membership.is_active holds: the
        current ones that staff have approved."""
        return self.current(today).filter(is_approved=True)

    def awaiting_approval(self, today: datetime.date | None = None) -> "MembershipQuerySet":
        """Narrow to the current memberships that staff have yet to approve. An
        application which has since expired or been suspended is no longer
        awaiting anything."""
        return self.current(today).filter(is_approved=False)


class Membership(models.Model):
    """A person's membership of a particular type, with its lifecycle dates."""

    id = models.UUIDField(
        primary_key=True, verbose_name=_("id"), default=uuid.uuid4, editable=False
    )
    person = models.ForeignKey(
        "underground_crm.Person",
        verbose_name=_("Person"),
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    type = models.ForeignKey(
        MembershipType,
        verbose_name=_("Membership type"),
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    started_at = models.DateTimeField(verbose_name=_("Start date"))
    expires_on = models.DateField(null=True, verbose_name=_("Expiration date"), blank=True)
    suspended_at = models.DateTimeField(null=True, verbose_name=_("Suspension date"), blank=True)
    # New memberships await approval by staff. Migration 0030 approved every
    # membership that existed before this field did, and memberships imported
    # from a previous CRM are approved too, having been granted there already.
    is_approved = models.BooleanField(
        default=False,
        verbose_name=_("Is approved"),
        help_text=_("Whether staff have approved this membership."),
    )

    objects = MembershipQuerySet.as_manager()

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.person} — {self.type}"

    @property
    def is_current(self) -> bool:
        """Whether the membership has neither been suspended nor expired, whether
        or not staff have approved it yet. See MembershipQuerySet.current."""
        if self.suspended_at:
            return False
        if self.expires_on and self.expires_on < timezone.localdate():
            return False
        return True

    @property
    def is_active(self) -> bool:
        """Whether the membership is current and staff have approved it. See
        MembershipQuerySet.active."""
        return self.is_current and self.is_approved
