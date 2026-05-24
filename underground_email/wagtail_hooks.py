import logging

from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ngettext
from wagtail import hooks
from wagtail.admin.ui.tables import Column
from wagtail.snippets.bulk_actions.snippet_bulk_action import SnippetBulkAction
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import EmailCampaign, EmailSender, TemplatedGreeting


@hooks.register("insert_global_admin_js")
def colorfield_wagtail_js():
    return format_html(
        '<script src="{}"></script>', static("underground_email/js/colorfield_wagtail.js")
    )


logger = logging.getLogger(__name__)


class EmailSenderViewSet(SnippetViewSet):
    model = EmailSender
    icon = "user"
    menu_label = "Email senders"
    menu_order = 220
    list_display = ["sender"]
    search_fields = ["sender__email", "sender__first_name", "sender__last_name"]


class TemplatedGreetingViewSet(SnippetViewSet):
    model = TemplatedGreeting
    icon = "openquote"
    menu_label = "Email greetings"
    menu_order = 210
    list_display = ["greeting"]
    search_fields = ["greeting"]


class EmailCampaignViewSet(SnippetViewSet):
    model = EmailCampaign
    icon = "mail"
    menu_label = "Email campaigns"
    menu_order = 200
    add_to_admin_menu = True
    list_display = [
        "subject",
        "sender",
        Column("state", label=_("State"), accessor="get_state_display"),
        "sending_date",
    ]
    list_filter = ["state"]
    search_fields = ["subject"]
    preview_modes = [("", "Email preview")]


register_snippet(EmailSenderViewSet)
register_snippet(TemplatedGreetingViewSet)
register_snippet(EmailCampaignViewSet)


class ScheduleEmailCampaignAction(SnippetBulkAction):
    display_name = _("Send at scheduled time")
    action_type = "schedule_email"
    aria_label = _("Schedule selected email campaigns for sending")
    template_name = "underground_email/bulk_actions/confirm_schedule.html"
    models = [EmailCampaign]

    @classmethod
    def execute_action(cls, objects, **kwargs):
        from django_q.models import Schedule
        from django_q.tasks import async_task, schedule as q_schedule

        count = 0
        for campaign in objects:
            if campaign.state != 0:
                logger.warning(
                    "Campaign %s is not in Draft state; skipping schedule.", campaign.utm_id
                )
                continue
            if not campaign.people_filter_id:
                logger.warning(
                    "Campaign %s has no people filter; skipping schedule.", campaign.utm_id
                )
                continue

            task_name = f"email_campaign_{campaign.utm_id}"
            try:
                if campaign.sending_date:
                    logger.info(
                        "Scheduling campaign %s (%r) as task %r to run at %s.",
                        campaign.utm_id,
                        campaign.subject,
                        task_name,
                        campaign.sending_date,
                    )
                    # Monitor the queue: https://django-q.readthedocs.io/en/latest/monitor.html
                    q_schedule(
                        "underground_email.tasks.send_emails",
                        campaign.utm_id,
                        task_name=task_name,
                        schedule_type=Schedule.ONCE,
                        next_run=campaign.sending_date,
                        cluster="email",
                    )
                else:
                    logger.info(
                        "Enqueuing campaign %s (%r) as task %r for immediate sending.",
                        campaign.utm_id,
                        campaign.subject,
                        task_name,
                    )
                    async_task(
                        "underground_email.tasks.send_emails",
                        campaign.utm_id,
                        task_name=task_name,
                        cluster="email",
                    )
            except Exception:
                logger.exception(
                    "Failed to enqueue campaign %s (%r) — is the Q broker (Redis) reachable?",
                    campaign.utm_id,
                    campaign.subject,
                )
                continue

            campaign.state = 1  # Scheduled
            campaign.save(update_fields=["state"])
            count += 1
            logger.info(
                "Campaign %s (%r) state set to Scheduled.", campaign.utm_id, campaign.subject
            )

        return count, 0

    def get_success_message(self, num_parent_objects, num_child_objects):
        return ngettext(
            "%(count)d email campaign has been scheduled for sending.",
            "%(count)d email campaigns have been scheduled for sending.",
            num_parent_objects,
        ) % {"count": num_parent_objects}


class CancelEmailCampaignAction(SnippetBulkAction):
    display_name = _("Cancel sending")
    action_type = "cancel_email"
    aria_label = _("Cancel scheduled sending for selected email campaigns")
    template_name = "underground_email/bulk_actions/confirm_cancel.html"
    models = [EmailCampaign]

    @classmethod
    def execute_action(cls, objects, **kwargs):
        from django_q.models import OrmQ, Schedule

        count = 0
        for campaign in objects:
            if campaign.state != 1:
                logger.warning(
                    "Campaign %s is not in Scheduled state; skipping cancel.", campaign.utm_id
                )
                continue

            task_name = f"email_campaign_{campaign.utm_id}"
            Schedule.objects.filter(name=task_name).delete()
            # Also purge any item already sitting in the ORM queue but not yet started.
            OrmQ.objects.filter(key=task_name).delete()

            campaign.state = 0  # Draft
            campaign.save(update_fields=["state"])
            count += 1

        return count, 0


hooks.register("register_bulk_action")(ScheduleEmailCampaignAction)
hooks.register("register_bulk_action")(CancelEmailCampaignAction)
