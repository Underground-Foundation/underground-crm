from django.contrib import admin

from underground_crm.admin import SubmittedFieldInline
from underground_email.models import SubscriptionPageSubmission


@admin.register(SubscriptionPageSubmission)
class SubscriptionPageSubmissionAdmin(admin.ModelAdmin):
    """The "unsubscription submissions": submissions of the subscription page,
    where staff can read why people cancelled their memberships or unsubscribed."""

    list_display = ["__str__", "person", "email_address", "submission_time", "departure_notes"]
    list_filter = ["is_authenticated"]
    search_fields = [
        "person__email",
        "person__first_name",
        "person__last_name",
        "email_address",
        "departure_notes",
    ]
    readonly_fields = ["submission_time"]
    inlines = [SubmittedFieldInline]
