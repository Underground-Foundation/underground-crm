from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from underground_crm.models import Person


class APIPermissionsTestCase(APITestCase):

    def setUp(self) -> None:
        # Create users
        self.public_user = Person.objects.create_user(
            email="public@example.com",
            password="publicpassword",
            is_staff=False,
            is_admin=False,
        )
        self.staff_user = Person.objects.create_user(
            email="staff@example.com",
            password="staffpassword",
            is_staff=True,
            is_admin=False,
        )
        self.admin_user = Person.objects.create_user(
            email="admin@example.com",
            password="adminpassword",
            is_staff=True,
            is_admin=True,
        )

        # Endpoints to test
        self.endpoints = [
            "tag-list",
            "note-list",
            "interaction-list",
            "engagement-list",
            "donation-list",
            "address-list",
        ]

    def test_anonymous_user_is_denied(self) -> None:
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint, method="GET"):
                url = reverse(endpoint)
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                    f"Anonymous user should be denied access to {endpoint}",
                )

    def test_public_user_is_denied(self) -> None:
        self.client.force_authenticate(user=self.public_user)
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint, method="GET"):
                url = reverse(endpoint)
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                    f"Public user should be denied access to {endpoint}",
                )

            # Test write access as well
            with self.subTest(endpoint=endpoint, method="POST"):
                url = reverse(endpoint)
                response = self.client.post(url, data={})
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                    f"Public user should be denied write access to {endpoint}",
                )

    def test_staff_user_is_allowed(self) -> None:
        self.client.force_authenticate(user=self.staff_user)
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint, method="GET"):
                url = reverse(endpoint)
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_200_OK,
                    f"Staff user should be allowed access to {endpoint}",
                )

    def test_admin_user_is_allowed(self) -> None:
        self.client.force_authenticate(user=self.admin_user)
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint, method="GET"):
                url = reverse(endpoint)
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_200_OK,
                    f"Admin user should be allowed access to {endpoint}",
                )
