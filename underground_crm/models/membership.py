import uuid

from django.db import models


class MembershipType(models.Model):
    """A category of membership offered by the organisation (e.g. "Fusion Party", "Pirate Party")."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    """A person's membership of a particular type, with its lifecycle dates."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(
        "underground_crm.Person",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    type = models.ForeignKey(
        MembershipType,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    started_at = models.DateTimeField()
    expires_on = models.DateField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.person} — {self.type}"

    @property
    def is_active(self):
        from django.utils import timezone

        if self.suspended_at:
            return False
        if self.expires_on and self.expires_on < timezone.now().date():
            return False
        return True
