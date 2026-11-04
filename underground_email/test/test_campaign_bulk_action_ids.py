"""
Tests for finding campaigns from the identifiers submitted to their bulk actions.

Wagtail's listing page escapes each primary key, rewriting "_" as "_5F", and generate_base64_id
can produce underscores. These tests submit the requests that the listing page makes, through
the real views, so that a campaign is only reported as found if it was actually acted upon.
"""

from datetime import timedelta

from django.contrib.admin.utils import quote
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from underground_crm.models import PeopleFilter
from underground_email.models import EmailCampaign, EmailSender

DRAFT = 0
SCHEDULED = 1

ID_WITH_UNDERSCORES = "_Kvix_yF9w"
ID_WITHOUT_UNDERSCORES = "AKvixByF9w"
# Not an escaped value: this is the campaign's real identifier, which happens to look like one.
ID_RESEMBLING_AN_ESCAPE_SEQUENCE = "A_5FixByF9"


class CampaignBulkActionIdTests(TestCase):
    def setUp(self) -> None:
        secretary = get_user_model().objects.create_superuser(
            email="secretary@fusionparty.org.au", password="correct horse battery staple"
        )
        self.client.force_login(secretary)
        self.sender = EmailSender.objects.create(sender=secretary)
        self.people_filter = PeopleFilter.objects.create(
            name="Everyone in Victoria", criteria={"logic": "AND", "rules": []}
        )

    def _create_campaign(self, utm_id: str, state: int = DRAFT) -> EmailCampaign:
        return EmailCampaign.objects.create(
            utm_id=utm_id,
            subject="Volunteers needed in Brunswick this weekend",
            sender=self.sender,
            template="underground_email/smtp2go_base.html",
            people_filter=self.people_filter,
            # A sending date means that scheduling writes a Schedule row and needs no broker.
            sending_date=timezone.now() + timedelta(days=1),
            state=state,
        )

    def _action_url(self, action_type: str, submitted_ids: list[str]) -> str:
        path = reverse(
            "wagtail_bulk_action", args=("underground_email", "emailcampaign", action_type)
        )
        return path + "?" + "&".join(f"id={submitted_id}" for submitted_id in submitted_ids)

    def _state_of(self, campaign: EmailCampaign) -> int:
        campaign.refresh_from_db()
        return campaign.state

    def test_listing_page_submits_escaped_identifiers(self) -> None:
        """
        The other tests in this module submit quote(utm_id). This one justifies that, by
        showing that it is the value the listing page gives to its checkboxes.
        """
        self._create_campaign(ID_WITH_UNDERSCORES)

        # register_snippet attaches snippet_viewset to the model at runtime.
        # pylint: disable-next=no-member
        listing = self.client.get(reverse(EmailCampaign.snippet_viewset.get_url_name("list")))

        self.assertContains(listing, f'data-object-id="{quote(ID_WITH_UNDERSCORES)}"')

    def test_confirmation_page_opens_for_a_campaign_with_underscores(self) -> None:
        self._create_campaign(ID_WITH_UNDERSCORES)

        response = self.client.get(self._action_url("schedule_email", [quote(ID_WITH_UNDERSCORES)]))

        self.assertEqual(response.status_code, 200)

    def test_campaign_with_underscores_can_be_scheduled(self) -> None:
        campaign = self._create_campaign(ID_WITH_UNDERSCORES)

        self.client.post(self._action_url("schedule_email", [quote(ID_WITH_UNDERSCORES)]))

        self.assertEqual(self._state_of(campaign), SCHEDULED)

    def test_campaign_with_underscores_can_be_cancelled(self) -> None:
        campaign = self._create_campaign(ID_WITH_UNDERSCORES, state=SCHEDULED)

        self.client.post(self._action_url("cancel_email", [quote(ID_WITH_UNDERSCORES)]))

        self.assertEqual(self._state_of(campaign), DRAFT)

    def test_campaign_with_underscores_is_not_left_out_of_a_mixed_selection(self) -> None:
        """
        Without unescaping, the campaign without underscores is still found, so the request
        succeeds and the other campaign is skipped with no indication of this.
        """
        with_underscores = self._create_campaign(ID_WITH_UNDERSCORES)
        without_underscores = self._create_campaign(ID_WITHOUT_UNDERSCORES)

        self.client.post(
            self._action_url(
                "schedule_email",
                [quote(ID_WITH_UNDERSCORES), quote(ID_WITHOUT_UNDERSCORES)],
            )
        )

        self.assertEqual(self._state_of(with_underscores), SCHEDULED)
        self.assertEqual(self._state_of(without_underscores), SCHEDULED)

    def test_select_all_finds_campaigns_with_any_identifier(self) -> None:
        """
        "Select all" takes primary keys from the database, unescaped. Passing those directly
        to the unescaping lookup would turn the last of these identifiers into "A_ixByF9".
        """
        campaigns = [
            self._create_campaign(utm_id)
            for utm_id in (
                ID_WITH_UNDERSCORES,
                ID_WITHOUT_UNDERSCORES,
                ID_RESEMBLING_AN_ESCAPE_SEQUENCE,
            )
        ]

        self.client.post(self._action_url("schedule_email", ["all"]))

        for campaign in campaigns:
            self.assertEqual(self._state_of(campaign), SCHEDULED, campaign.utm_id)
