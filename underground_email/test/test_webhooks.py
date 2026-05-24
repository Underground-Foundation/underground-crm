"""
Tests for SMTP2Go email webhook processing.

Verifies that incoming webhook requests are processed asynchronously by enqueuing
them onto the 'email' cluster, rather than executing database operations synchronously.
"""

from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from underground_email.api import SMTP2GoEventType
from underground_email.tasks import process_webhook_event


class EmailWebhookTest(TestCase):
    """Tests for the email webhook view and its asynchronous task handler."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="test-recipient@example.com",
            first_name="Test",
            last_name="Recipient",
            password="testpassword",
        )
        self.url = reverse("email_webhook")

    @patch("underground_email.views.webhooks.async_task")
    def test_webhook_enqueues_task_for_spam_event(self, mock_async_task):
        payload = {
            "event": SMTP2GoEventType.SPAM.value,
            "rcpt": "test-recipient@example.com",
            "subject": "Spam subject",
        }
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        mock_async_task.assert_called_once_with(
            "underground_email.tasks.process_webhook_event",
            payload,
            "test-recipient@example.com",
            cluster="email",
        )

    @patch("underground_email.views.webhooks.async_task")
    def test_webhook_enqueues_task_for_unsubscribe_event(self, mock_async_task):
        payload = {
            "event": SMTP2GoEventType.UNSUBSCRIBED.value,
            "rcpt": "test-recipient@example.com",
            "subject": "Unsubscribe subject",
        }
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        mock_async_task.assert_called_once_with(
            "underground_email.tasks.process_webhook_event",
            payload,
            "test-recipient@example.com",
            cluster="email",
        )

    @patch("underground_email.views.webhooks.async_task")
    def test_webhook_ignores_delivered_event(self, mock_async_task):
        payload = {
            "event": SMTP2GoEventType.DELIVERED.value,
            "rcpt": "test-recipient@example.com",
        }
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        mock_async_task.assert_not_called()

    @patch("underground_email.views.webhooks.async_task")
    def test_webhook_invalid_json_returns_400(self, mock_async_task):
        response = self.client.post(
            self.url,
            data="not-valid-json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        mock_async_task.assert_not_called()

    @patch("underground_email.tasks.handle_spam_or_unsubscription")
    def test_process_webhook_event_task(self, mock_handle_spam):
        payload = {
            "event": SMTP2GoEventType.SPAM.value,
            "rcpt": "test-recipient@example.com",
            "subject": "Spam subject",
        }
        process_webhook_event(payload, "test-recipient@example.com")
        mock_handle_spam.assert_called_once()
        # Verify the recipient passed to handle_spam_or_unsubscription is correct
        called_args, called_kwargs = mock_handle_spam.call_args
        self.assertEqual(called_kwargs["recipient"], self.user)
