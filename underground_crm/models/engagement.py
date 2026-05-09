import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from .person import Person


class Engagement(models.Model):
    """
    Records a single interaction between a person and the organisation.
    Acts as an audit trail and the basis for engagement scoring.
    """

    # Action type constants
    SIGNUP = "signup"
    DONATED = "donated"
    PETITIONED = "petitioned"
    RSVP = "rsvp"
    ATTENDED_EVENT = "attended_event"
    VOLUNTEERED = "volunteered"
    CONTACTED = "contacted"
    EMAIL_OPENED = "email_opened"
    EMAIL_CLICKED = "email_clicked"
    FOLLOWED_PAGE = "followed_page"
    SURVEY_RESPONDED = "survey_responded"
    MEMBERSHIP_JOINED = "membership_joined"
    MEMBERSHIP_RENEWED = "membership_renewed"
    UNSUBSCRIBED = "unsubscribed"

    ACTION_CHOICES = [
        (SIGNUP, _("Signed up")),
        (DONATED, _("Donated")),
        (PETITIONED, _("Signed petition")),
        (RSVP, _("RSVP'd to event")),
        (ATTENDED_EVENT, _("Attended event")),
        (VOLUNTEERED, _("Volunteered")),
        (CONTACTED, _("Contacted by staff")),
        (EMAIL_OPENED, _("Opened email")),
        (EMAIL_CLICKED, _("Clicked link in email")),
        (FOLLOWED_PAGE, _("Followed page")),
        (SURVEY_RESPONDED, _("Responded to survey")),
        (MEMBERSHIP_JOINED, _("Joined as member")),
        (MEMBERSHIP_RENEWED, _("Renewed membership")),
        (UNSUBSCRIBED, _("Unsubscribed")),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="engagements")
    action_type = models.CharField(max_length=50, choices=ACTION_CHOICES, db_index=True)

    # Loose reference to the page or campaign that triggered this engagement.
    # Using URL + title rather than a FK keeps this decoupled from any specific
    # page model hierarchy.
    page_url = models.CharField(max_length=500, blank=True)
    page_title = models.CharField(max_length=200, blank=True)

    # Staff member who recorded a manual contact, if applicable
    recorded_by = models.ForeignKey(
        Person,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recorded_engagements",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Flexible store for action-specific data (e.g. donation amount, email campaign ID)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "engagement"
        verbose_name_plural = "engagements"

    def __str__(self):
        return f"{self.person} — {self.get_action_type_display()}"
