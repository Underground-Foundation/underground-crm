import uuid

from django.db import models


class Interaction(models.Model):
    """
    A logged interaction between a staff member and a person.
    Corresponds to NationBuilder's 'contact' records.
    """

    METHOD_FACE_TO_FACE = "face_to_face"
    METHOD_PHONE_CALL = "phone_call"
    METHOD_EMAIL = "email"
    METHOD_SMS = "sms"
    METHOD_TEXT_BLAST = "text_blast"
    METHOD_DOOR_KNOCK = "door_knock"
    METHOD_LETTER = "letter"
    METHOD_SOCIAL_MEDIA = "social_media"
    METHOD_OTHER = "other"

    METHOD_CHOICES = [
        (METHOD_FACE_TO_FACE, "Face to face"),
        (METHOD_PHONE_CALL, "Phone call"),
        (METHOD_EMAIL, "Email"),
        (METHOD_SMS, "SMS"),
        (METHOD_TEXT_BLAST, "Text blast"),
        (METHOD_DOOR_KNOCK, "Door knock"),
        (METHOD_LETTER, "Letter"),
        (METHOD_SOCIAL_MEDIA, "Social media"),
        (METHOD_OTHER, "Other"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    legacy_contact_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Contact ID from the legacy system, used to deduplicate imports.",
    )
    person = models.ForeignKey(
        "underground_crm.Person",
        on_delete=models.CASCADE,
        related_name="interactions",
    )
    author = models.ForeignKey(
        "underground_crm.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="authored_interactions",
    )
    method = models.CharField(max_length=50, choices=METHOD_CHOICES)
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=100,
        blank=True,
        help_text="Outcome status, e.g. 'meaningful_interaction', 'no_response'.",
    )
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "interaction"
        verbose_name_plural = "interactions"

    def __str__(self):
        return f"{self.get_method_display()} with {self.person} on {self.created_at:%Y-%m-%d}"
