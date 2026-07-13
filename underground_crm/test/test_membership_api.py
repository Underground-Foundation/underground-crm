import datetime

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from underground_crm.models import Membership, MembershipType, Person

MEMBERSHIP_TYPE_NAME = "Pirate Party"
# How close the server-set started_at must land to the moment of the test's
# own timezone.now() call for the two to count as "the same creation", given
# the request/response round trip in between.
STARTED_AT_TOLERANCE = datetime.timedelta(seconds=5)
# A client-supplied value the serializer must never honor, standing in for
# either an attempt to backdate a membership or to attach it to someone else.
SPOOFED_STARTED_AT = timezone.now() - datetime.timedelta(days=3650)


class MembershipAPITestCase(APITestCase):
    def setUp(self) -> None:
        self.member = Person.objects.create_user(
            email="priya.sharma@example.com", password="correct-horse-battery"
        )
        self.other_member = Person.objects.create_user(
            email="lucas.nguyen@example.com", password="correct-horse-battery"
        )
        self.membership_type = MembershipType.objects.create(name=MEMBERSHIP_TYPE_NAME)
        self.list_url = reverse("membership-list")

    def _detail_url(self, membership: Membership) -> str:
        return reverse("membership-detail", kwargs={"pk": membership.pk})

    def test_anonymous_user_cannot_create(self) -> None:
        response = self.client.post(self.list_url, data={"type": self.membership_type.pk})
        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
            "An anonymous visitor must not be able to create a membership.",
        )

    def test_authenticated_user_can_create_own_membership(self) -> None:
        self.client.force_authenticate(user=self.member)
        response = self.client.post(self.list_url, data={"type": self.membership_type.pk})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        membership = Membership.objects.get(pk=response.data["id"])
        self.assertEqual(
            membership.person,
            self.member,
            "A self-service membership must belong to the requesting visitor.",
        )
        self.assertEqual(membership.type, self.membership_type)
        self.assertAlmostEqual(
            membership.started_at,
            timezone.now(),
            delta=STARTED_AT_TOLERANCE,
            msg="A newly created membership must start now, not at some other time.",
        )
        self.assertIsNone(
            membership.expires_on,
            "A freshly created membership must not already carry an expiry.",
        )

    def test_client_supplied_person_and_started_at_are_ignored(self) -> None:
        self.client.force_authenticate(user=self.member)
        response = self.client.post(
            self.list_url,
            data={
                "type": self.membership_type.pk,
                "person": self.other_member.pk,
                "started_at": SPOOFED_STARTED_AT.isoformat(),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        membership = Membership.objects.get(pk=response.data["id"])
        self.assertEqual(
            membership.person,
            self.member,
            "The person field must always be the requesting visitor, "
            "regardless of what the request body claims.",
        )
        self.assertNotEqual(
            membership.started_at,
            SPOOFED_STARTED_AT,
            "started_at is server-set and must not be backdated by the client.",
        )

    def test_user_can_delete_own_membership(self) -> None:
        membership = Membership.objects.create(
            person=self.member, type=self.membership_type, started_at=timezone.now()
        )
        self.client.force_authenticate(user=self.member)
        response = self.client.delete(self._detail_url(membership))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            Membership.objects.filter(pk=membership.pk).exists(),
            "Deleting through the API must remove the membership record "
            "entirely, unlike the soft cancellation on the RegistrationPage.",
        )

    def test_user_cannot_delete_someone_elses_membership(self) -> None:
        membership = Membership.objects.create(
            person=self.other_member, type=self.membership_type, started_at=timezone.now()
        )
        self.client.force_authenticate(user=self.member)
        response = self.client.delete(self._detail_url(membership))

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
            "A membership id alone must not be enough to delete it — it must "
            "also belong to the requesting visitor.",
        )
        self.assertTrue(
            Membership.objects.filter(pk=membership.pk).exists(),
            "The other visitor's rejected request must leave the membership " "untouched.",
        )

    def test_anonymous_user_cannot_delete(self) -> None:
        membership = Membership.objects.create(
            person=self.member, type=self.membership_type, started_at=timezone.now()
        )
        response = self.client.delete(self._detail_url(membership))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Membership.objects.filter(pk=membership.pk).exists())

    def test_list_and_retrieve_are_not_exposed(self) -> None:
        membership = Membership.objects.create(
            person=self.member, type=self.membership_type, started_at=timezone.now()
        )
        self.client.force_authenticate(user=self.member)

        list_response = self.client.get(self.list_url)
        self.assertEqual(
            list_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
            "This endpoint is create/delete only — the RegistrationPage's "
            "memberships section is the visitor-facing read surface.",
        )

        detail_response = self.client.get(self._detail_url(membership))
        self.assertEqual(detail_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
