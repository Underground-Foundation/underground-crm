import secrets
import uuid
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from wagtail.admin.panels import FieldPanel, HelpPanel, MultiFieldPanel, ObjectList, TabbedInterface
from wagtail.fields import RichTextField, StreamField
from wagtail.models import PreviewableMixin, Site
from wagtail.rich_text import expand_db_html

from underground_crm.models import PeopleFilter
from underground_crm.models.form_submission import FormSubmission
from underground_crm.models.pages import FormPage
from underground_crm.models.person import Tag
from .blocks import EmailBodyBlock, _RICH_TEXT_FEATURES
from .forms import EmailSenderForm


class RichTextPreviewPanel(HelpPanel):
    """Read-only panel that renders a RichTextField using the same path as the richtext template filter.

    Use this alongside a FieldPanel for the same field to show editors an accurate preview of
    how the content will appear in the final output, since Draftail's own display differs."""

    def __init__(self, field_name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.field_name = field_name

    def clone_kwargs(self) -> dict:
        return {**super().clone_kwargs(), "field_name": self.field_name}

    class BoundPanel(HelpPanel.BoundPanel):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            raw = getattr(self.instance, self.panel.field_name, "") or ""
            self.content = expand_db_html(raw)


class EmailSender(models.Model):
    sender = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        primary_key=True,
        on_delete=models.CASCADE,
        verbose_name=_("Attributed sender"),
        help_text=_(
            "Emails will be sent from this user's email address. This assumes the domain has been registered with SMTP2Go. Choose someone who is a staff member or who has an email address at one of the domains specified in DJANGO_ALLOWED_HOSTS."
        ),
    )
    sending_permission_group = models.ForeignKey(
        Group,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Sending permission group"),
        help_text=_("Who can send emails attributed to this identity?"),
    )
    signature = RichTextField(
        features=_RICH_TEXT_FEATURES,
        blank=True,
        verbose_name=_("Signature"),
        help_text=_("This editing view looks slightly different to the final version"),
    )

    panels = [
        FieldPanel("sender"),
        FieldPanel("sending_permission_group"),
        FieldPanel("signature"),
        RichTextPreviewPanel("signature", heading="Signature preview"),
    ]

    base_form_class = EmailSenderForm

    class Meta:
        verbose_name = "email sender"
        verbose_name_plural = "email senders"

    def __str__(self) -> str:
        return str(self.sender)


class TemplatedGreeting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    greeting = models.CharField(
        max_length=255,
        verbose_name=_("greeting"),
        # Notice in tasks.py that these name options are the only ones provided to render_greeting
        help_text=_(
            "Use {{ first_name }}, {{ preferred_or_first_name }}, {{ last_name }}, or {{ full_name }}, along with a greeting. "
            "This will be included in every email."
        ),
    )

    class Meta:
        verbose_name = "templated greeting"
        verbose_name_plural = "templated greetings"

    def __str__(self) -> str:
        return self.greeting


EMAIL_STATES = (
    (0, _("Draft")),
    (1, _("Scheduled")),
    (2, _("Sent")),
)


def generate_base64_id() -> str:
    return secrets.token_urlsafe(7)  # Produces a 10-character URL-safe string


def default_site_id() -> Optional[int]:
    return Site.objects.filter(is_default_site=True).values_list("pk", flat=True).first()


class EmailCampaign(PreviewableMixin, models.Model):
    # https://en.wikipedia.org/wiki/UTM_parameters
    utm_id = models.CharField(
        verbose_name=_("UTM identifier"),
        help_text=_("A unique identifier for the Urchin Traffic Module to track conversions"),
        default=generate_base64_id,
        max_length=10,
        primary_key=True,
    )
    subject = models.CharField(
        verbose_name=_("subject"),
        max_length=255,
        help_text=_("The campaign's subject line, as you'd like it to be seen by the public"),
    )
    preview_text = models.CharField(
        verbose_name=_("preview text"),
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Short summary shown by email clients after the subject line"),
    )
    sender = models.ForeignKey(EmailSender, verbose_name=_("sender"), on_delete=models.PROTECT)
    site = models.ForeignKey(
        Site,
        default=default_site_id,
        on_delete=models.PROTECT,
        verbose_name=_("site"),
        help_text=_("This will be used when generating URLs in the email body"),
    )
    greeting = models.ForeignKey(
        TemplatedGreeting,
        null=True,
        blank=True,
        verbose_name=_("greeting"),
        on_delete=models.SET_NULL,
    )
    template = models.CharField(
        verbose_name=_("template"),
        max_length=200,
        help_text=_("The Django template used to render this email"),
    )
    body = StreamField(EmailBodyBlock(), blank=True, use_json_field=True)
    people_filter = models.ForeignKey(
        PeopleFilter,
        null=True,
        blank=True,
        verbose_name=_("people filter"),
        help_text=_("This must be specified before sending"),
        on_delete=models.SET_NULL,
    )
    unsubscription_tag = models.ForeignKey(
        Tag,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        verbose_name=_("Unsubscription tag"),
        help_text=_(
            "Which tag should be removed if the recipient clicks to unsubscribe? If not set, such recipients will be converted to tombstones (losing their membership)."
        ),
    )
    state = models.PositiveSmallIntegerField(
        choices=EMAIL_STATES,
        default=0,
        help_text=_(
            "To actually send the email campaign, please run the action from the previous menu"
        ),
    )
    sending_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("sending date"),
        help_text=_("Leave this blank to send immediately"),
    )
    sent_count = models.PositiveIntegerField(verbose_name=_("sent count"), null=True, blank=True)
    clicked_count = models.PositiveIntegerField(
        verbose_name=_("clicked count"),
        help_text=_("A count of how many users clicked links from this email"),
        null=True,
        blank=True,
    )
    opened_count = models.PositiveIntegerField(
        verbose_name=_("opened count"),
        null=True,
        blank=True,
        help_text=_(
            "Google and Apple have measures preventing us from measuring this, so it should not be trusted"
        ),
    )
    spam_count = models.PositiveIntegerField(
        verbose_name=_("spam count"),
        help_text=_("A count of how many recipients marked this as spam or rejected the message"),
        null=True,
        blank=True,
    )
    unsubscription_count = models.PositiveIntegerField(
        verbose_name=_("unsubscription count"), null=True, blank=True
    )
    smtp2go_template_id = models.CharField(
        verbose_name=_("smtp2go template ID"),
        max_length=50,
        blank=True,
    )

    panels = [
        TabbedInterface(
            [
                ObjectList(
                    [
                        FieldPanel("subject"),
                        FieldPanel("preview_text"),
                        FieldPanel("greeting"),
                        FieldPanel("template"),
                        FieldPanel("body"),
                    ],
                    heading="Content",
                ),
                ObjectList(
                    [
                        FieldPanel("sender"),
                        FieldPanel("site"),
                        FieldPanel("people_filter"),
                        FieldPanel("state", read_only=True),
                        FieldPanel("sending_date"),
                    ],
                    heading="Sending",
                ),
                ObjectList(
                    [
                        MultiFieldPanel(
                            [
                                FieldPanel("sent_count", read_only=True),
                                FieldPanel("clicked_count", read_only=True),
                                FieldPanel("opened_count", read_only=True),
                                FieldPanel("spam_count", read_only=True),
                                FieldPanel("unsubscription_count", read_only=True),
                            ],
                            heading="Statistics",
                        ),
                    ],
                    heading="Stats",
                ),
            ]
        )
    ]

    class Meta:
        verbose_name = "email campaign"
        verbose_name_plural = "email campaigns"

    def __str__(self) -> str:
        return self.subject

    def get_potential_recipients(self):
        if not self.people_filter:
            return None
        from django.contrib.auth import get_user_model

        base_qs = get_user_model().objects.filter(
            unsubscribed_at__isnull=True,
            email_opt_in=True,
            do_not_contact=False,
            email__gt="",
            email__isnull=False,
            is_active=True,
        )
        return self.people_filter.apply(base_qs)

    @property
    def potential_recipients(self) -> Optional[int]:
        qs = self.get_potential_recipients()
        if qs is None:
            return None
        return qs.count()

    @classmethod
    def possible_templates(cls) -> list[tuple[str, str]]:
        """Return (template_path, display_name) pairs for all discovered email templates."""
        from django.apps import apps

        templates = []
        for app_config in apps.get_app_configs():
            base = Path(app_config.path) / "templates" / "underground_email"
            if base.is_dir():
                for html_file in sorted(base.glob("*.html")):
                    path = f"underground_email/{html_file.name}"
                    label = html_file.stem.replace("_", " ").replace("-", " ").title()
                    templates.append((path, label))
        return templates

    def get_preview_template(self, request, mode_name: str) -> str:
        return self.template or "underground_email/smtp2go_base.html"

    def get_preview_context(self, request, mode_name: str) -> dict:
        context = super().get_preview_context(request, mode_name)
        context.update(
            {
                "subject": self.subject,
                "greeting": self.greeting.greeting if self.greeting else "",
                "body": self.body,
                "signature": None if not self.sender else self.sender.signature,
                "preview_text": self.preview_text,
            }
        )
        return context


class SubscriptionPage(FormPage):
    """
    The page on which a person manages what they receive from us. Its form
    (see underground_email.subscription_form.SubscriptionForm) lists their
    current memberships, each with a ticked checkbox, and every tag named in
    SELF_QUERYABLE_TAGS, ticked if they hold it. Unticking a membership cancels
    it; ticking or unticking a tag subscribes or unsubscribes them. Being a
    FormPage, it may also ask questions (such as why they are leaving).

    The link at the foot of each campaign email leads here. We have this page,
    rather than unsubscribing people as soon as they follow that link, to get
    around over-eager email clients and security scanners, which follow the
    links in a message before its recipient has read it. Following the link
    only shows this page, with the link's signed token carried in a hidden
    field of the form; nothing changes until somebody submits it.

    The visitor is identified by their login or by that token, never by an
    email address and name typed into the form, which would let anybody manage
    anybody else's subscriptions; hence should_request_signup_fields is off.

    Every site is given one of these pages by migration 0018, and it is always
    served at /unsubscribe (by underground_email.views.email_click_handler.
    subscriptions_view), wherever it sits in the page tree, because that is the
    address that the links in our emails have always pointed to. A request that
    reaches the page through its place in the tree is redirected there.
    """

    is_creatable = True
    subpage_types: list[str] = []
    template = "underground_email/pages/subscription_page.html"
    should_request_signup_fields = False

    class Meta:
        verbose_name = _("Subscription Page")

    def serve(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        unsubscription_path = reverse("unsubscribe")
        if request.path != unsubscription_path:
            query_string = request.META.get("QUERY_STRING", "")
            return redirect(
                f"{unsubscription_path}?{query_string}" if query_string else unsubscription_path
            )
        return super().serve(request, *args, **kwargs)

    def get_submission_form_class(self):
        from underground_email.subscription_form import SubscriptionForm

        return SubscriptionForm

    def _calculated_cache_time(self) -> int:
        """Never cache by default, because the page lists the visitor's own
        memberships and subscriptions. Editors can still set
        cache_ttl_override."""
        return 0


class SubscriptionPageSubmission(FormSubmission):
    """
    A submission of a SubscriptionPage's form, called an "unsubscription
    submission" in the admin. Extends FormSubmission, as EventGuest does, for
    the one answer that no input block on the page asks for: the notes a person
    may leave about why they are cancelling a membership or unsubscribing.
    Answers to questions an editor placed on the page are recorded as ordinary
    SubmittedField rows against it. One is saved only when there is something
    to record (see SubscriptionForm.save).
    """

    departure_notes = models.TextField(
        blank=True,
        default="",
        verbose_name=_("Departure notes"),
        help_text=_("Why the person is cancelling a membership or unsubscribing, in their words."),
    )

    class Meta:
        verbose_name = _("Unsubscription submission")
        verbose_name_plural = _("Unsubscription submissions")
