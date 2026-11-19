from dataclasses import dataclass
from typing import Any, Optional

from django import forms
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from wagtail.blocks.stream_block import StreamValue

from ..blocks import CHOICE_SEPARATOR, PERSON_FIELD_AUTOCOMPLETE
from ..models.form_submission import FormSubmission, SubmittedField

Person = get_user_model()


def _field_name(input_block: StreamValue.StreamChild) -> str:
    # Keyed by the stream child's UUID, which is stable across reordering and
    # editing of the page body — the same key SubmittedField.block_id records.
    return f"input_{input_block.id}"


@dataclass(frozen=True)
class BodyPart:
    """
    One block of a form-serving page's body, together with the form fields that
    are drawn where it sits. A content block (a heading, a paragraph, an image)
    carries no fields and draws its own content; an input block draws nothing
    of its own (see blocks/input_block.html) and carries the field it produced.
    """

    block: StreamValue.StreamChild
    fields: list[forms.BoundField]


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


def _normalized(name: Optional[str]) -> str:
    return (name or "").strip().casefold()


def find_identity_conflict(
    email: str,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    preferred_name: Optional[str] = None,
) -> Optional[str]:
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

    existing_first_name = _normalized(existing.first_name)
    existing_preferred_name = _normalized(existing.preferred_name) or existing_first_name
    given_names_match = True
    if _normalized(first_name) or _normalized(preferred_name):
        if _normalized(first_name):
            given_names_match &= _normalized(first_name) == existing_first_name
        if _normalized(preferred_name):
            given_names_match &= _normalized(preferred_name) == existing_preferred_name
    else:
        given_names_match = not existing_first_name

    if not given_names_match or _normalized(last_name) != _normalized(existing.last_name):
        return _("An account already exists for this email address. Please log in to continue.")
    return None


def get_or_create_person(
    email: str,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    preferred_name: Optional[str] = None,
) -> Any:
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
            "first_name": first_name or None,
            "preferred_name": preferred_name or None,
            "last_name": last_name or None,
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

    def _field_names_at(self, block: StreamValue.StreamChild) -> list[str]:
        """
        The names of the form fields drawn where the given body block sits: the
        field an input block produced, or nothing for a content block (and for
        an input block that produced no field, such as a Person-field block the
        whitelist no longer allows). Subclasses that draw a block's field
        somewhere else, or draw a companion field beside it, override this.
        """
        field_name = _field_name(block)
        return [field_name] if field_name in self.fields else []

    def _body_part(self, block: StreamValue.StreamChild) -> "BodyPart":
        """One entry of body_parts. Overridable so that a subclass can attach
        whatever else its template draws at a block (see RegistrationBodyPart)."""
        return BodyPart(block, [self[name] for name in self._field_names_at(block)])

    def _field_names_laid_out_in_body(self) -> set[str]:
        """The names of the fields drawn among the body's blocks, which
        trailing_fields must therefore leave out."""
        return {name for block in self.page.body for name in self._field_names_at(block)}

    @property
    def body_parts(self) -> list["BodyPart"]:
        """
        Every block of the page body, in the editor's order, each with the form
        fields drawn where it sits. form_page.html draws the body inside the
        <form> from this list, so that a question appears where the editor put
        it among the page's headings and paragraphs, instead of every question
        being gathered up after all of them.
        """
        return [self._body_part(block) for block in self.page.body]

    @property
    def trailing_fields(self) -> list[forms.BoundField]:
        """
        The visible fields that no body block accounts for, drawn one per row
        after the body: the email and name an anonymous visitor identifies
        themself with, and any field a subclass declares outright (such as
        EventGuestForm.extra_guests).
        """
        laid_out = self._field_names_laid_out_in_body()
        return [field for field in self.visible_fields() if field.name not in laid_out]

    @staticmethod
    def _field_for_input(input_block: StreamValue.StreamChild) -> forms.Field:
        """Each input block (a FormInputBlock) builds its own visitor field from
        the label, help text and so on that the editor gave it."""
        return input_block.block.visitor_field(input_block.value)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        if self.request.user.is_authenticated:
            return cleaned

        email = cleaned.get("email")
        if not email:
            self.add_error("email", _("Email address is required."))
            return cleaned

        conflict = find_identity_conflict(
            email,
            first_name=cleaned.get("first_name") or None,
            last_name=cleaned.get("last_name") or None,
            preferred_name=cleaned.get("preferred_name") or None,
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
                first_name=self.cleaned_data.get("first_name") or None,
                last_name=self.cleaned_data.get("last_name") or None,
                preferred_name=self.cleaned_data.get("preferred_name") or None,
            )

        submission.full_clean()
        submission.save()

        for input_block in self.page.inputs:
            field_value = self.cleaned_data.get(_field_name(input_block))
            if isinstance(field_value, bool):
                # A checkbox's answer is has_value alone; str(True) in value
                # would just duplicate it.
                has_value, value = field_value, ""
            elif isinstance(field_value, list):
                # The choices ticked in a set of checkboxes.
                has_value, value = bool(field_value), CHOICE_SEPARATOR.join(field_value)
            else:
                has_value = field_value is not None and field_value != ""
                value = str(field_value) if has_value else ""
            SubmittedField.objects.create(
                submission=submission,
                block_id=str(input_block.id),
                name=input_block.block_type,
                label=input_block.value["label"],
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
