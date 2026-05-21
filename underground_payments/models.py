from django.db import models
from django.utils.translation import gettext_lazy as _
from wagtail.admin.panels import FieldPanel, ObjectList, TabbedInterface

from underground_crm.models.pages import BasicPage, PageWithMetadata


class PaymentPage(BasicPage):
    is_creatable = True

    @property
    def og_type(self) -> str:
        # The Open Graph type for this page. See https://ogp.me/#types for the full list of valid types.
        return "payment.link"

    allow_monthly_payments = models.BooleanField(
        default=True,
        verbose_name=_("Allow monthly payments"),
    )
    allow_annual_payments = models.BooleanField(
        default=True,
        verbose_name=_("Allow annual payments"),
    )
    donor_tag = models.ForeignKey(
        "underground_crm.Tag",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Donor tag"),
        help_text=_("Tag applied to the donor after a successful payment."),
    )
    redirect_to = models.ForeignKey(
        "wagtailcore.Page",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Redirect to"),
        help_text=_(
            "The redirection page after a successful payment. "
            "Leave blank to show a thank-you message on this page."
        ),
    )
    success_email = models.ForeignKey(
        "underground_email.EmailCampaign",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Success email"),
        help_text=_("Email sent to the donor after a successful payment."),
    )
    email_every_time = models.BooleanField(
        default=False,
        verbose_name=_("Email every time?"),
        help_text=_(
            "Send the success email for every donation. "
            "If set to false, only the donor's first donation at this page will trigger an email."
        ),
    )

    content_panels = BasicPage.content_panels + [
        FieldPanel("allow_monthly_payments"),
        FieldPanel("allow_annual_payments"),
    ]

    payment_panels = [
        FieldPanel("donor_tag"),
        FieldPanel("success_email"),
        FieldPanel("email_every_time"),
        FieldPanel("redirect_to"),
    ]

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(payment_panels, heading=_("Payment")),
            ObjectList(PageWithMetadata.promote_panels, heading=_("Metadata")),
            ObjectList(PageWithMetadata.visibility_panels, heading=_("Visibility")),
        ]
    )

    class Meta:
        verbose_name = "Payment Page"

    def get_context(self, request, *args, **kwargs):
        from django.conf import settings as django_settings

        context = super().get_context(request, *args, **kwargs)
        context["stripe_publishable_key"] = getattr(django_settings, "STRIPE_PUBLISHABLE_KEY", "")
        context["redirect_url"] = self.redirect_to.url if self.redirect_to_id else ""
        return context
