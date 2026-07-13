from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from ..models.pages import EventGuest
from .form_submission import FormSubmissionForm


class EventGuestForm(FormSubmissionForm):
    """
    FormSubmissionForm for EventPage: always asks how many extra guests are
    coming (a real EventGuest field, not a generic SubmittedField
    answer), on top of any admin-added inputs the base class already renders.
    """

    extra_guests = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        label=_("How many guests will be accompanying you?"),
    )

    def _build_submission(self) -> EventGuest:
        return EventGuest(
            is_authenticated=self.request.user.is_authenticated,
            page=self.page,
            extra_guests=self.cleaned_data.get("extra_guests") or 0,
        )
