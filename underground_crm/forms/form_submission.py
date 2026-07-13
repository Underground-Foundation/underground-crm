import copy
from typing import Any

from django import forms
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from wagtail.blocks.stream_block import StreamValue

from ..blocks import PERSON_FIELD_AUTOCOMPLETE
from ..models.form_submission import FormSubmission, SubmittedField

Person = get_user_model()


def _field_name(input_block: StreamValue.StreamChild) -> str:
    # Keyed by the stream child's UUID, which is stable across reordering and
    # editing of the page body — the same key SubmittedField.block_id records.
    return f"input_{input_block.id}"


def apply_bootstrap_classes(widget: forms.Widget) -> None:
    """
    Stamp the Bootstrap form-control classes onto a widget Django built for us.

    Bootstrap styles form controls by class rather than by element, so a widget
    that carries no class renders unstyled — and, more than being merely plain,
    an unstyled input keeps its intrinsic width instead of filling the grid
    column it sits in, which defeats the naming grid entirely.

    Idempotent, so a form that re-stamps a widget it has already stamped does
    not accumulate duplicate classes.
    """
    if isinstance(widget, forms.MultiWidget):
        for subwidget in widget.widgets:
            apply_bootstrap_classes(subwidget)
        return
    if isinstance(widget, forms.HiddenInput):
        return
    if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
        css_class = "form-check-input"
    else:
        css_class = "form-control"
    existing: list[str] = widget.attrs.get("class", "").split()
    if css_class not in existing:
        widget.attrs["class"] = " ".join(existing + [css_class])


def find_identity_conflict(email: str, first_name: str, last_name: str) -> str | None:
    """
    Returns an error message if a Person already exists for this email address
    and the supplied names don't match it, else None. This stops an anonymous
    visitor from typing in an existing member's email to attach tags/engagement
    to their account without proving they own it (matching by name is a weak
    proof, but stronger than nothing, and consistent with never touching
    FormSubmission.person for anonymous submissions in the first place).
    """
    existing = Person.objects.filter(email=Person.objects.normalize_email(email)).first()
    if existing is None:
        return None
    if (first_name or "").strip().casefold() != (existing.first_name or "").strip().casefold() or (
        last_name or ""
    ).strip().casefold() != (existing.last_name or "").strip().casefold():
        return _("An account already exists for this email address. Please log in to continue.")
    return None


def get_or_create_person(email: str, first_name: str, last_name: str) -> Any:
    """
    Resolves the Person an anonymous submission's tags and engagement should
    apply to. Never attached to FormSubmission.person (see that field's
    comment) — this is a read model for downstream effects, not a claim that
    the visitor authenticated as this Person.

    A newly created placeholder has no usable password and has never logged
    in (empty password, empty last_login — both are simply left at their
    model defaults here, not explicitly set), and is_active=True so it
    behaves like any other Person record for filtering/lookup purposes.
    """
    person, _created = Person.objects.get_or_create(
        email=Person.objects.normalize_email(email),
        defaults={
            "first_name": first_name or "",
            "last_name": last_name or "",
            "is_active": True,
        },
    )
    return person


class FormSubmissionForm(forms.Form):
    """
    Renders one form field per input block placed on a FormPage's body (see
    FORM_INPUT_BLOCKS in models/pages.py), plus identity fields for anonymous
    visitors.

    Authenticated visitors are identified by their session — no email/name
    fields are shown, and their submission is tied straight to their Person
    record via FormSubmission.person.

    Anonymous visitors supply email/first_name/last_name. FormSubmission.person
    is never set for them (see that field's own comment) — but we still need
    a concrete Person to apply tags/engagement to, so:
      - if no Person exists for that email, a placeholder one is created
        (no usable password, never logged in — see _get_or_create_person());
      - if a Person already exists for that email, first_name/last_name must
        match it, or the submission is rejected and the visitor is asked to
        log in instead. This stops an anonymous visitor from typing in an
        existing member's email to attach tags/engagement to their account
        without proving they own it (matching by name is a weak proof, but
        stronger than nothing, and consistent with never touching `person`
        directly for anonymous submissions in the first place).

    Subclasses that need to build a different FormSubmission subclass (e.g.
    EventGuestForm building an EventGuest) should override _build_submission()
    rather than save() itself, to keep the identity/input-field/tag handling
    below in one place.
    """

    email = forms.EmailField(
        required=False,
        label=_("Email address"),
        widget=forms.EmailInput(attrs={"autocomplete": PERSON_FIELD_AUTOCOMPLETE["email"]}),
    )
    first_name = forms.CharField(
        required=False,
        max_length=100,
        label=_("First name"),
        widget=forms.TextInput(attrs={"autocomplete": PERSON_FIELD_AUTOCOMPLETE["first_name"]}),
    )
    last_name = forms.CharField(
        required=False,
        max_length=100,
        label=_("Last name"),
        widget=forms.TextInput(attrs={"autocomplete": PERSON_FIELD_AUTOCOMPLETE["last_name"]}),
    )

    def __init__(self, *args: Any, request: HttpRequest, page: Any, **kwargs: Any) -> None:
        self.request = request
        self.page = page
        super().__init__(*args, **kwargs)

        if request.user.is_authenticated:
            del self.fields["email"]
            del self.fields["first_name"]
            del self.fields["last_name"]

        for input_block in page.inputs:
            self.fields[_field_name(input_block)] = self._field_for_input(input_block)

        for field in self.fields.values():
            apply_bootstrap_classes(field.widget)

    @property
    def layout_fields(self) -> list[forms.BoundField]:
        """
        The fields form_page.html lays out one per row. Subclasses that gather
        some of their fields into a grid of their own (see
        RegistrationForm.naming_fields) drop those fields from here, so that a
        field rendered by the grid is not also rendered a second time by the
        ordinary loop.
        """
        return list(self.visible_fields())

    @staticmethod
    def _field_for_input(input_block: StreamValue.StreamChild) -> forms.Field:
        """
        Every input block wraps a built-in Wagtail FieldBlock, and a FieldBlock
        is itself defined by a Django form field — so reuse that field directly
        rather than re-mapping block types by hand. Deep-copied because the
        block definition (and its field) is module-level shared state, and the
        editor-supplied block value becomes this visitor's pre-filled initial.
        """
        field = copy.deepcopy(input_block.block.field)
        field.required = False
        field.label = input_block.block.label
        field.initial = input_block.value
        return field

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        if self.request.user.is_authenticated:
            return cleaned

        email = cleaned.get("email")
        if not email:
            self.add_error("email", _("Email address is required."))
            return cleaned

        conflict = find_identity_conflict(
            email, cleaned.get("first_name") or "", cleaned.get("last_name") or ""
        )
        if conflict:
            self.add_error(None, conflict)
        return cleaned

    def _build_submission(self) -> FormSubmission:
        """Returns an unsaved FormSubmission (or subclass) with only
        is_authenticated/page set. Override to set model-specific fields
        from self.cleaned_data before the identity fields are applied below."""
        return FormSubmission(is_authenticated=self.request.user.is_authenticated, page=self.page)

    def save(self) -> FormSubmission:
        user = self.request.user
        is_authenticated = user.is_authenticated

        submission = self._build_submission()
        if is_authenticated:
            submission.person = user
            target_person = user
        else:
            email = Person.objects.normalize_email(self.cleaned_data["email"])
            submission.email_address = email
            submission.ip_address = self.request.META.get("REMOTE_ADDR") or None
            submission.language_preferences = self.request.META.get("HTTP_ACCEPT_LANGUAGE", "")
            target_person = get_or_create_person(
                email,
                self.cleaned_data.get("first_name") or "",
                self.cleaned_data.get("last_name") or "",
            )

        submission.full_clean()
        submission.save()

        for input_block in self.page.inputs:
            field_value = self.cleaned_data.get(_field_name(input_block))
            if isinstance(field_value, bool):
                # A checkbox's answer is has_value alone; str(True) in value
                # would just duplicate it.
                has_value, value = field_value, ""
            else:
                has_value = field_value is not None and field_value != ""
                value = str(field_value) if has_value else ""
            SubmittedField.objects.create(
                submission=submission,
                block_id=str(input_block.id),
                name=input_block.block_type,
                label=str(input_block.block.label),
                has_value=has_value,
                value=value,
            )

        # Tags (and, for EventPage RSVPs, engagement — see tasks.record_rsvp_engagement)
        # apply to target_person regardless of authentication status: a
        # placeholder/matched Person from an anonymous submission still gets
        # them. FormSubmission.person itself stays unset for anonymous
        # submissions either way (see that field's comment). PersonTag.was_authenticated
        # records which of those two cases applied — only used for a newly-created
        # relation, so an existing tag's recorded value is never downgraded by a
        # later anonymous submission.
        target_person.tags.add(
            *self.page.tags_to_apply.all(),
            through_defaults={"was_authenticated": is_authenticated},
        )

        return submission
