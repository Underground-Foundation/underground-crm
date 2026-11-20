"""
The form of the SubscriptionPage.

We have that page, rather than unsubscribing people as soon as they follow the
link at the foot of an email, to get around over-eager email clients and
security scanners, which follow the links in a message (to check
them for malware, or to preview them) before its recipient has read it. Had
following the link been enough, such a client would have unsubscribed people
who never asked to be. So following the link only shows this form; nothing
changes until a person submits it.
"""

import logging
from typing import Any, Optional

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.http import HttpRequest
from wagtail.models import Site
from django.utils.translation import gettext, gettext_lazy as _

from underground_crm.forms.form_submission import FormSubmissionForm, _field_name
from underground_crm.models import Engagement
from underground_crm.models.form_submission import FormSubmission
from underground_crm.models.membership import Membership
from underground_crm.models.person import Tag
from underground_crm.signals import subscription_created
from underground_email.models import EmailCampaign, SubscriptionPageSubmission
from underground_email.unsubscription import (
    add_self_queryable_tag,
    cancel_own_membership,
    decode_unsubscription_token,
    remove_self_queryable_tag,
    self_queryable_tags,
)

logger = logging.getLogger(__name__)

Person = get_user_model()

TOKEN_FIELD_NAME = "address"


class MembershipChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj: Membership) -> str:
        if not obj.is_approved:
            return gettext("%(membership_type)s (awaiting approval)") % {
                "membership_type": obj.type.name
            }
        return obj.type.name


class SubscriptionForm(FormSubmissionForm):
    """
    Lists the visitor's current memberships, each with a ticked checkbox, and
    every tag named in SELF_QUERYABLE_TAGS, ticked if the visitor holds it.
    Submitting the form with a membership unticked cancels it; ticking or
    unticking a tag subscribes the visitor to it or unsubscribes them from it.
    Nothing else about the visitor's record can be changed through it, because
    only their own memberships and those tags are offered as choices.

    The visitor is whoever is logged in, or else whoever the signed token in an
    email's unsubscription link names (see make_unsubscription_url). The token
    arrives as the "address" query parameter of the link, and is carried in a
    hidden field so that it is decoded again, with decode_unsubscription_token,
    when the form is submitted. A visitor known by their token is not asked for
    their email address or name; nor is anybody else, because a typed address
    proves nothing (see SubscriptionPage.should_request_signup_fields).

    Any questions an editor places on the page are answered in the same form,
    and recorded as a FormSubmission, as on any FormPage.
    """

    address = forms.CharField(required=False, widget=forms.HiddenInput)
    memberships = MembershipChoiceField(
        queryset=Membership.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Your memberships"),
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Your subscriptions"),
    )
    departure_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label=_("Sorry to see you go. Feel free to leave any notes about why this is:"),
    )
    # Drawn by the template above the page's body, rather than among the trailing fields.
    TEMPLATE_DRAWN_FIELD_NAMES = ("memberships", "tags", "departure_notes")

    def __init__(self, *args: Any, request: HttpRequest, page: Any, **kwargs: Any) -> None:
        super().__init__(*args, request=request, page=page, **kwargs)
        # The token is in the query string when somebody arrives from an email,
        # and in the hidden field when they submit the form.
        self.token = request.POST.get(TOKEN_FIELD_NAME) or request.GET.get(TOKEN_FIELD_NAME, "")
        self.fields[TOKEN_FIELD_NAME].initial = self.token
        self.campaign_utm_id: Optional[str] = None
        self.token_is_invalid = False
        self.subscribed_tags: list[Tag] = []
        self.visitor = self._find_visitor()
        self.is_identified_by_token = self.visitor is not None and not request.user.is_authenticated

        if self.visitor is not None:
            current_memberships = self.visitor.memberships.current().select_related("type")
            self.fields["memberships"].queryset = current_memberships.order_by("type__name")
            self.fields["memberships"].initial = list(current_memberships)
            self.subscribed_tags = list(
                self_queryable_tags().filter(tagged_people__content_object=self.visitor)
            )
            self.fields["tags"].queryset = self_queryable_tags()
            self.fields["tags"].initial = self.subscribed_tags

    def _find_visitor(self) -> Optional[Any]:
        """
        A logged-in visitor is identified by their login, which makes the token
        unnecessary: they may have reached the page without one, or kept a link
        from an old email. The token then only says which campaign's email
        brought them here, and only if it names them; otherwise it is ignored.
        A visitor who is not logged in is identified by the token alone.
        """
        user = self.request.user
        token_names = self._decode_token()
        if user.is_authenticated:
            if token_names is None or token_names.pk != user.pk:
                self.campaign_utm_id = None
            self.token_is_invalid = False
            return user
        return token_names

    def _decode_token(self) -> Optional[Any]:
        """The person whom the token names, also noting the campaign it came from."""
        if not self.token:
            return None
        try:
            email_address, self.campaign_utm_id = decode_unsubscription_token(self.token)
        except (signing.BadSignature, KeyError, ValueError):
            logger.warning(
                "Invalid unsubscription token received from %s: %s", self.request.user, self.token
            )
            self.token_is_invalid = True
            return None
        person = Person.objects.filter(email=email_address).first()
        if person is None:
            logger.warning("Nobody has the address %s, so they cannot unsubscribe", email_address)
            self.token_is_invalid = True
        return person

    @property
    def trailing_fields(self) -> list[forms.BoundField]:
        return [
            field
            for field in super().trailing_fields
            if field.name not in self.TEMPLATE_DRAWN_FIELD_NAMES
        ]

    def clean(self) -> dict[str, Any]:
        if self.visitor is None:
            # Only somebody who followed an email's link or has logged in can be
            # told apart from anybody else, so nobody else may change anyone's
            # subscriptions.
            raise forms.ValidationError(
                _(
                    "Please log in or follow the unsubscription link from your email in order to manage your "
                    "memberships and subscriptions."
                )
            )
        if not self.is_identified_by_token:
            return super().clean()
        cleaned = forms.Form.clean(self)
        # FormSubmissionForm.save() files an anonymous submission under an
        # email address, which the token has proved to be the visitor's.
        cleaned["email"] = self.visitor.email
        cleaned["first_name"] = self.visitor.first_name
        cleaned["last_name"] = self.visitor.last_name
        return cleaned

    def has_anything_to_record(self) -> bool:
        """
        Whether the visitor left departure notes or answered any question an
        editor placed on the page. Only then is a submission saved: the changes
        to their memberships and subscriptions are recorded where they are made
        (on the Membership, as Engagements and on the campaign), so a
        submission with nothing else in it would say nothing new.
        """
        if self.cleaned_data.get("departure_notes"):
            return True
        for input_block in self.page.inputs:
            answer = self.cleaned_data.get(_field_name(input_block))
            if answer not in (None, "", [], False):
                return True
        return False

    def save(self) -> Optional[FormSubmission]:
        """Make the changes the visitor asked for, and save a submission if
        there is anything to record (see has_anything_to_record). Returns the
        submission, or None if none was saved."""
        with transaction.atomic():
            kept_memberships = set(self.cleaned_data["memberships"])
            cancelled = [
                membership
                for membership in self.fields["memberships"].queryset
                if membership not in kept_memberships
                and cancel_own_membership(self.visitor, membership_id=str(membership.pk))
            ]
            ticked_tags = set(self.cleaned_data["tags"])
            removed = [
                tag
                for tag in self.subscribed_tags
                if tag not in ticked_tags
                and remove_self_queryable_tag(
                    self.visitor, tag_id=str(tag.pk), page_title=self.page.title
                )
            ]
            was_authenticated = self.request.user.is_authenticated
            added = [
                tag
                for tag in sorted(ticked_tags, key=lambda tag: tag.name)
                if add_self_queryable_tag(self.visitor, tag, was_authenticated=was_authenticated)
            ]
            if cancelled or removed:
                self._record_campaign_unsubscription()
            # Skipping the submission also skips the page's tags_to_apply,
            # which FormSubmissionForm.save applies along with it.
            submission = super().save() if self.has_anything_to_record() else None

        for tag in added:
            subscription_created.send(
                sender=None,
                person=self.visitor,
                tag=tag,
                was_authenticated=was_authenticated,
                person_created=False,
                site=Site.find_for_request(self.request),
            )

        for membership in cancelled:
            messages.success(
                self.request,
                gettext("Your %(membership_type)s membership has been cancelled.")
                % {"membership_type": membership.type.name},
            )
        for tag in removed:
            messages.success(
                self.request,
                gettext("You will no longer receive %(tag)s emails.") % {"tag": tag.name},
            )
        for tag in added:
            messages.success(
                self.request,
                gettext("You will now receive %(tag)s emails.") % {"tag": tag.name},
            )
        return submission

    def _build_submission(self) -> SubscriptionPageSubmission:
        return SubscriptionPageSubmission(
            is_authenticated=self.request.user.is_authenticated,
            page=self.page,
            departure_notes=self.cleaned_data.get("departure_notes") or "",
        )

    def _record_campaign_unsubscription(self) -> None:
        """Count the unsubscription against the campaign whose email's link
        brought the visitor here, if it still exists."""
        if self.campaign_utm_id is None:
            return
        campaign = EmailCampaign.objects.filter(utm_id=self.campaign_utm_id).first()
        if campaign is None:
            return
        Engagement(
            person=self.visitor,
            action_type=Engagement.UNSUBSCRIBED,
            page_title=campaign.subject,
            metadata={"campaign_utm_id": campaign.utm_id},
        ).save()
        campaign.unsubscription_count = (campaign.unsubscription_count or 0) + 1
        campaign.save(update_fields=["unsubscription_count"])
