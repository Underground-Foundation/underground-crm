import uuid

from django.db import models


class PersonNote(models.Model):
    """A note associated with a person, visible only to staff."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(
        "underground_crm.Person",
        on_delete=models.CASCADE,
        related_name="notes",
    )
    text = models.TextField()
    legacy_activity_id = models.PositiveIntegerField(
        null=True, blank=True, unique=True, db_index=True,
        help_text="Activity ID from legacy system, used to deduplicate imports.",
    )
    created_by = models.ForeignKey(
        "underground_crm.Person",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="authored_notes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Note on {self.person} ({self.created_at:%Y-%m-%d})"
