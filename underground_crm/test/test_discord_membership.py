from datetime import timedelta
from uuid import uuid4

from allauth.socialaccount.models import SocialAccount
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from underground_crm.models import Membership, MembershipType, Person

DISCORD_ID = "123456789012345678"
INTEGRATION_TOKEN = "synthetic-integration-secret-at-least-32-characters"


@override_settings(DISCORD_MEMBERSHIP_INTEGRATION_TOKEN=INTEGRATION_TOKEN)
class DiscordMembershipTests(TestCase):
    def setUp(self) -> None:
        self.person = Person.objects.create_user(email="alex.member@example.org")
        self.membership_type = MembershipType.objects.create(name="Voting membership")
        self.settings_override = override_settings(
            DISCORD_MEMBERSHIP_TYPE_ID=str(self.membership_type.pk)
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        SocialAccount.objects.create(user=self.person, provider="discord", uid=DISCORD_ID)
        self.url = reverse("discord-membership", args=[DISCORD_ID])
        self.membership = Membership.objects.create(
            person=self.person,
            type=self.membership_type,
            started_at=timezone.now() - timedelta(days=1),
        )

    def lookup(self):
        return self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {INTEGRATION_TOKEN}")

    def test_active_linked_member_returns_only_boolean_and_no_cache(self) -> None:
        response = self.lookup()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"eligible": True})
        self.assertIn("no-store", response["Cache-Control"])
        self.membership.expires_on = timezone.localdate()
        self.membership.save()
        self.assertEqual(self.lookup().json(), {"eligible": True}, "Expiry today is inclusive")

    def test_missing_wrong_and_staff_session_credentials_cannot_read(self) -> None:
        self.client.force_login(self.person)
        for authorization in ("", "Bearer incorrect-secret", "Basic synthetic-secret"):
            with self.subTest(authorization=authorization):
                response = self.client.get(self.url, HTTP_AUTHORIZATION=authorization)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("eligible", response.json())

    def test_disabled_or_invalid_configuration_fails_closed(self) -> None:
        for configuration in (
            {"DISCORD_MEMBERSHIP_INTEGRATION_TOKEN": ""},
            {"DISCORD_MEMBERSHIP_TYPE_ID": ""},
            {"DISCORD_MEMBERSHIP_TYPE_ID": "invalid-uuid"},
        ):
            with self.subTest(configuration=configuration), override_settings(**configuration):
                self.assertEqual(self.lookup().status_code, 503)

    def test_ineligible_membership_states(self) -> None:
        now = timezone.now()
        for changes in (
            {"started_at": now + timedelta(days=1)},
            {"suspended_at": now},
            {"expires_on": timezone.localdate() - timedelta(days=1)},
        ):
            with self.subTest(changes=changes):
                Membership.objects.filter(pk=self.membership.pk).update(**changes)
                self.assertEqual(self.lookup().json(), {"eligible": False})
                Membership.objects.filter(pk=self.membership.pk).update(
                    started_at=now - timedelta(days=1), suspended_at=None, expires_on=None
                )

    def test_missing_configured_membership_type_is_unavailable(self) -> None:
        with override_settings(DISCORD_MEMBERSHIP_TYPE_ID=str(uuid4())):
            self.assertEqual(self.lookup().status_code, 503)

    def test_membership_type_created_after_a_failed_lookup_becomes_available(self) -> None:
        not_yet_created_type_id = uuid4()
        with override_settings(DISCORD_MEMBERSHIP_TYPE_ID=str(not_yet_created_type_id)):
            self.assertEqual(self.lookup().status_code, 503)
            self.membership.type = MembershipType.objects.create(
                pk=not_yet_created_type_id, name="Founding membership"
            )
            self.membership.save()
            self.assertEqual(
                self.lookup().json(),
                {"eligible": True},
                "A failed configuration check must not be cached, or fixing it would need a restart",
            )

    def test_short_token_configuration_is_unavailable(self) -> None:
        with override_settings(DISCORD_MEMBERSHIP_INTEGRATION_TOKEN="too-short"):
            self.assertEqual(self.lookup().status_code, 503)

    def test_unlinked_unknown_and_other_membership_type_are_ineligible(self) -> None:
        other_type = MembershipType.objects.create(name="Associate membership")
        with override_settings(DISCORD_MEMBERSHIP_TYPE_ID=str(other_type.pk)):
            self.assertEqual(self.lookup().json(), {"eligible": False})
        SocialAccount.objects.filter(user=self.person).update(provider="github")
        self.assertEqual(self.lookup().json(), {"eligible": False})
        self.url = reverse("discord-membership", args=["987654321098765432"])
        self.assertEqual(self.lookup().json(), {"eligible": False})

    def test_methods_and_malformed_ids_are_rejected(self) -> None:
        response = self.client.post(self.url, HTTP_AUTHORIZATION=f"Bearer {INTEGRATION_TOKEN}")
        self.assertEqual(response.status_code, 405)
        self.url = reverse("discord-membership", args=["alex-member"])
        self.assertEqual(self.lookup().status_code, 400)
