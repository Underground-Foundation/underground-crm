import string
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

_PUNCTUATION_TABLE = str.maketrans("", "", string.punctuation)


def generate_input_field_name(description: str) -> str:
    """
    Derives a machine-readable name from a human-authored description:
    lowercase, strip punctuation, then join the remaining words with
    underscores, e.g. "Can help with Calling Members" becomes
    "can_help_with_calling_members".
    """
    without_punctuation = description.lower().translate(_PUNCTUATION_TABLE)
    return "_".join(without_punctuation.split())


class InputField(models.Model):
    """
    A single question/checkbox offered on a FormPage (e.g. "Can help with
    calling members"). Editable via the admin/Wagtail snippets UI so
    downstream theme projects can define their own input fields without a
    code change.
    """

    CHECKBOX = "checkbox"
    DATE = "date"
    DATETIME = "datetime"
    TEXT = "text"
    # Input types modelled on the HTML <input> element's "type" attribute, e.g.
    # https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/input/checkbox
    INPUT_TYPE_CHOICES = [
        (CHECKBOX, _("Checkbox")),
        (DATE, _("Date")),
        (DATETIME, _("Date and time")),
        (TEXT, _("Text")),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    description_en = models.CharField(max_length=200, verbose_name=_("Description (English)"))
    name = models.CharField(
        max_length=200,
        unique=True,
        blank=True,
        verbose_name=_("Name"),
        help_text=_(
            "Leave blank to auto-generate from the English description, or specify one "
            "explicitly to override it."
        ),
    )
    input_type = models.CharField(
        max_length=20,
        choices=INPUT_TYPE_CHOICES,
        default=CHECKBOX,
        verbose_name=_("Input type"),
    )

    class Meta:
        ordering = ["description_en"]
        verbose_name = _("Input field")
        verbose_name_plural = _("Input fields")

    def __str__(self) -> str:
        return self.description_en

    def save(self, *args, **kwargs) -> None:
        if not self.name:
            self.name = generate_input_field_name(self.description_en)
        super().save(*args, **kwargs)


class FormSubmission(models.Model):
    """
    Records a single submission of a form-hosting page (see FormPage).

    Deliberately keeps unauthenticated submissions data-light and, more
    importantly, never attaches them to a Person: an anonymous visitor
    supplying someone else's email address must never be able to alter a
    verified member's record. ip_address and language_preferences are only
    ever populated for unauthenticated submissions — there's no need to
    track a known Person's browsing metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    is_authenticated = models.BooleanField(verbose_name=_("Is authenticated"))
    email_address = models.EmailField(
        null=True,
        blank=True,
        verbose_name=_("Email address"),
        help_text=_("Only required for unauthenticated submissions."),
    )
    # This FK is only ever set for a user who can actually log in (has a usable
    # password) and who was logged in at the moment they submitted this form —
    # never for a placeholder Person created/matched from an anonymous
    # submission's email address. See FormSubmissionForm.save() for how
    # anonymous submissions still resolve a target Person (for tags/engagement)
    # without ever writing it here.
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="form_submissions",
        verbose_name=_("Person"),
        help_text=_("Only set for authenticated submissions."),
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("IP address"),
        help_text=_("Only recorded for unauthenticated submissions."),
    )
    language_preferences = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        verbose_name=_("Language preferences"),
        help_text=_("Only recorded for unauthenticated submissions."),
    )
    submission_time = models.DateTimeField(auto_now_add=True, verbose_name=_("Submission time"))
    page = models.ForeignKey(
        "wagtailcore.Page",
        on_delete=models.CASCADE,
        related_name="form_submissions",
        verbose_name=_("Page"),
        help_text=_("The page the visitor was on when they submitted this form."),
    )

    class Meta:
        ordering = ["-submission_time"]
        verbose_name = _("Form submission")
        verbose_name_plural = _("Form submissions")

    def clean(self) -> None:
        if self.is_authenticated:
            if not self.person_id:
                raise ValidationError(
                    {"person": _("Authenticated submissions must reference a Person.")}
                )
        elif not self.email_address:
            raise ValidationError(
                {"email_address": _("Email address is required for unauthenticated submissions.")}
            )

    def __str__(self) -> str:
        who = self.person or self.email_address or _("anonymous")
        return f"{who} — {self.submission_time:%Y-%m-%d %H:%M}"


class SubmittedField(models.Model):
    """
    Records whether — and what — a given InputField was answered on a
    FormSubmission. A row is written for every input field on the page the
    submission came from, even when has_value is False, so "not selected" is
    distinguishable from "not asked".

    value holds the actual answer for date/datetime/text fields (e.g. "How
    many guests will be accompanying you?" -> "2"); it's irrelevant for
    checkboxes, where has_value alone (checked/unchecked) is the answer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(
        FormSubmission, on_delete=models.CASCADE, related_name="submitted_fields"
    )
    input_field = models.ForeignKey(
        InputField, on_delete=models.CASCADE, related_name="submitted_fields"
    )
    has_value = models.BooleanField(verbose_name=_("Has value"))
    value = models.TextField(blank=True, default="", verbose_name=_("Value"))

    class Meta:
        verbose_name = _("Submitted field")
        verbose_name_plural = _("Submitted fields")
        unique_together = [("submission", "input_field")]

    def __str__(self) -> str:
        return f"{self.input_field} = {self.has_value}"
