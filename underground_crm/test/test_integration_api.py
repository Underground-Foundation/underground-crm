import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from underground_crm.models import Membership, MembershipType, Person

INTEGRATION_TOKEN = "portal-test-token-2d4c8a1f6b9e4a7c8d3f5e2b1a9c7d4e"
UNSCOPED_TOKEN = "unscoped-test-token-9b1e4d7a2c5f8b3e6a1d4c7f9e2b5a8c"
TOKENS = {
    "tokens": [
        {"name": "member-portal", "token": INTEGRATION_TOKEN, "scopes": ["people:read"]},
        {"name": "other", "token": UNSCOPED_TOKEN, "scopes": ["people:write"]},
    ]
}
PEOPLE_URL = reverse("integration-people-v1")
PAGE_SIZE = 2
A_LONG_AGO = timezone.now() - timedelta(days=3650)


def _authorization(token: str = INTEGRATION_TOKEN) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@override_settings(INTEGRATION_API_TOKENS=json.dumps(TOKENS))
class IntegrationPeopleAPITestCase(TestCase):
    def setUp(self) -> None:
        self.active_type = MembershipType.objects.create(name="Full member")
        self.other_type = MembershipType.objects.create(name="Concession member")
        self.member = Person.objects.create_user(
            email="priya.sharma@example.com",
            first_name="Priya",
            last_name="Sharma",
            preferred_name="Priya",
            federal_district="Cooper",
            state_upper_district="Northern Metropolitan Region",
            state_lower_district="Richmond",
            council_district="City of Yarra",
            ward="Melbourne",
        )
        self.expired_member = Person.objects.create_user(
            email="lucas.nguyen@example.com",
            first_name="Lucas",
            last_name="Nguyen",
            federal_district="Fraser",
        )
        self.suspended_member = Person.objects.create_user(
            email="aisha.rahman@example.com",
            first_name="Aisha",
            last_name="Rahman",
            state_upper_district="Western Metropolitan Region",
        )
        self.supporter = Person.objects.create_user(
            email="no.membership@example.com",
            first_name="Not",
            last_name="AMember",
            is_supporter=True,
            federal_district="Cooper",
        )
        self.inactive_person = Person.objects.create_user(
            email="inactive@example.com",
            first_name="Inactive",
            last_name="Person",
            is_active=False,
            is_deceased=True,
        )
        Membership.objects.create(
            person=self.member,
            type=self.active_type,
            started_at=timezone.now() - timedelta(days=30),
            expires_on=timezone.localdate() + timedelta(days=335),
        )
        Membership.objects.create(
            person=self.expired_member,
            type=self.active_type,
            started_at=timezone.now() - timedelta(days=800),
            expires_on=timezone.localdate() - timedelta(days=1),
        )
        Membership.objects.create(
            person=self.suspended_member,
            type=self.other_type,
            started_at=timezone.now() - timedelta(days=60),
            suspended_at=timezone.now() - timedelta(days=1),
        )

    def _get(self, **params):
        return self.client.get(PEOPLE_URL, params, **_authorization())

    def _rows(self) -> list[dict]:
        response = self._get(limit=1000)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def test_unconfigured_api_fails_closed(self) -> None:
        with override_settings(INTEGRATION_API_TOKENS=""):
            response = self._get()
        self.assertEqual(response.status_code, 503)

    def test_missing_or_unknown_token_is_rejected(self) -> None:
        missing = self.client.get(PEOPLE_URL)
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing["WWW-Authenticate"], "Bearer")

        unknown = self.client.get(
            PEOPLE_URL,
            HTTP_AUTHORIZATION="Bearer not-a-configured-token-000000000000000000",
        )
        self.assertEqual(unknown.status_code, 401)

    def test_token_without_the_read_scope_is_forbidden(self) -> None:
        response = self.client.get(PEOPLE_URL, **_authorization(UNSCOPED_TOKEN))
        self.assertEqual(response.status_code, 403)

    def test_non_get_methods_are_not_exposed(self) -> None:
        response = self.client.post(PEOPLE_URL, **_authorization())
        self.assertEqual(response.status_code, 405)

    def test_first_page_contains_only_people_with_memberships(self) -> None:
        rows = self._rows()
        returned_ids = {row["id"] for row in rows}
        self.assertIn(str(self.member.pk), returned_ids)
        self.assertIn(str(self.expired_member.pk), returned_ids)
        self.assertIn(str(self.suspended_member.pk), returned_ids)
        self.assertNotIn(
            str(self.supporter.pk),
            returned_ids,
            "A first read must not export supporters who have no membership record.",
        )

    def test_contact_fields_are_minimal_and_memberships_are_complete(self) -> None:
        row = next(row for row in self._rows() if row["id"] == str(self.member.pk))
        self.assertEqual(
            set(row),
            {
                "id",
                "email",
                "email_is_bad",
                "first_name",
                "last_name",
                "preferred_name",
                "full_name",
                "record_type",
                "is_active",
                "is_deceased",
                "federal_district",
                "state_upper_district",
                "state_lower_district",
                "council_district",
                "ward",
                "updated_at",
                "memberships",
            },
        )
        self.assertEqual(row["email"], "priya.sharma@example.com")
        self.assertEqual(row["full_name"], "Priya Sharma")
        self.assertEqual(row["record_type"], "person")
        self.assertEqual(row["federal_district"], "Cooper")
        self.assertEqual(row["state_upper_district"], "Northern Metropolitan Region")
        self.assertEqual(row["state_lower_district"], "Richmond")
        self.assertEqual(row["council_district"], "City of Yarra")
        self.assertEqual(row["ward"], "Melbourne")
        self.assertEqual(len(row["memberships"]), 1)
        membership = row["memberships"][0]
        self.assertEqual(membership["type_id"], str(self.active_type.pk))
        self.assertEqual(membership["type_name"], "Full member")
        self.assertEqual(membership["status"], "active")
        self.assertTrue(membership["is_active"])

        for forbidden in (
            "password",
            "is_staff",
            "is_superuser",
            "notes",
            "donations",
            "home_address",
            "interactions",
        ):
            self.assertNotIn(forbidden, json.dumps(row))

    def test_membership_status_distinguishes_expiry_and_suspension(self) -> None:
        rows = {row["id"]: row for row in self._rows()}
        expired = rows[str(self.expired_member.pk)]["memberships"][0]
        suspended = rows[str(self.suspended_member.pk)]["memberships"][0]
        self.assertEqual(expired["status"], "expired")
        self.assertFalse(expired["is_active"])
        self.assertEqual(suspended["status"], "suspended")
        self.assertFalse(suspended["is_active"])

    def test_email_lookup_returns_a_person_without_a_membership(self) -> None:
        response = self._get(email="No.Membership@Example.com")
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(self.supporter.pk))
        self.assertEqual(rows[0]["memberships"], [])

    def test_email_lookup_does_not_return_an_ambiguous_set(self) -> None:
        response = self._get(email="nobody@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [])

    def test_person_detail_uses_the_stable_uuid(self) -> None:
        url = reverse("integration-person-detail-v1", kwargs={"person_id": self.member.pk})
        response = self.client.get(url, **_authorization())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["id"], str(self.member.pk))

        missing = reverse(
            "integration-person-detail-v1",
            kwargs={"person_id": "00000000-0000-0000-0000-000000000000"},
        )
        self.assertEqual(self.client.get(missing, **_authorization()).status_code, 404)

    def test_inactive_and_deceased_people_are_visible_to_the_consumer(self) -> None:
        response = self._get(email="inactive@example.com")
        self.assertEqual(response.status_code, 200)
        row = response.json()["data"][0]
        self.assertFalse(row["is_active"])
        self.assertTrue(row["is_deceased"])

    def test_pagination_returns_every_contact_once(self) -> None:
        seen: list[str] = []
        cursor = ""
        for _page in range(10):
            params = {"limit": PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor
            response = self._get(**params)
            self.assertEqual(response.status_code, 200, response.content)
            body = response.json()
            seen.extend(row["id"] for row in body["data"])
            if not body["meta"]["has_more"]:
                break
            cursor = body["meta"]["next_cursor"]
            self.assertTrue(cursor)
        else:
            self.fail("Pagination did not terminate within the expected number of pages.")

        self.assertEqual(
            len(seen),
            len(set(seen)),
            "A cursor-based read must never return the same contact twice.",
        )
        self.assertEqual(
            set(seen),
            {str(self.member.pk), str(self.expired_member.pk), str(self.suspended_member.pk)},
        )

    def test_invalid_cursor_and_since_are_rejected(self) -> None:
        bad_cursor = self.client.get(PEOPLE_URL, {"cursor": "not-a-cursor"}, **_authorization())
        self.assertEqual(bad_cursor.status_code, 400)

        naive_since = self.client.get(
            PEOPLE_URL,
            {"since": "2026-01-01T00:00:00"},
            **_authorization(),
        )
        self.assertEqual(naive_since.status_code, 400)

    def test_incremental_feed_delivers_a_removed_membership(self) -> None:
        # Start from a known, old position so the deletion is the only reason
        # this person can appear in the window.
        Person.objects.filter(pk=self.member.pk).update(updated_at=A_LONG_AGO)
        membership = Membership.objects.get(person=self.member)
        membership.delete()

        refreshed = Person.objects.get(pk=self.member.pk)
        self.assertGreater(
            refreshed.updated_at,
            A_LONG_AGO,
            "Deleting a membership must move its person into the incremental feed.",
        )
        response = self.client.get(
            PEOPLE_URL,
            {"since": A_LONG_AGO.isoformat(), "limit": 100},
            **_authorization(),
        )
        self.assertEqual(response.status_code, 200, response.content)
        row = next(row for row in response.json()["data"] if row["id"] == str(self.member.pk))
        self.assertEqual(
            row["memberships"],
            [],
            "An incremental read must show that the membership is gone, not omit the person.",
        )

    def test_limit_is_bounded(self) -> None:
        too_small = self._get(limit=0)
        self.assertEqual(too_small.status_code, 400)

        bounded = self._get(limit=10000)
        self.assertEqual(bounded.status_code, 200)
        self.assertLessEqual(bounded.json()["meta"]["limit"], 200)


@override_settings(INTEGRATION_API_TOKENS=json.dumps(TOKENS))
class IntegrationTokenConfigurationTestCase(TestCase):
    def test_short_tokens_are_ignored(self) -> None:
        short = json.dumps(
            {"tokens": [{"name": "too-short", "token": "abc", "scopes": ["people:read"]}]}
        )
        with override_settings(INTEGRATION_API_TOKENS=short):
            response = self.client.get(PEOPLE_URL, HTTP_AUTHORIZATION="Bearer abc")
        self.assertEqual(response.status_code, 503)

    def test_malformed_json_fails_closed(self) -> None:
        with override_settings(INTEGRATION_API_TOKENS="{not-json"):
            response = self.client.get(PEOPLE_URL, **_authorization())
        self.assertEqual(response.status_code, 503)

    def test_cursor_payload_is_not_a_guessable_person_id(self) -> None:
        person = Person.objects.create_user(email="cursor.check@example.com")
        Membership.objects.create(
            person=person,
            type=MembershipType.objects.create(name="Cursor test type"),
            started_at=timezone.now(),
        )
        response = self.client.get(PEOPLE_URL, limit=1, **_authorization())
        cursor = response.json()["meta"]["next_cursor"]
        self.assertIsNotNone(cursor)
        self.assertNotEqual(cursor, str(person.pk))
        next_page = self.client.get(PEOPLE_URL, {"cursor": cursor}, **_authorization())
        self.assertEqual(next_page.status_code, 200)
