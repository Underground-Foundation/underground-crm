import base64
import unittest
from unittest.mock import MagicMock, patch

from django.core.mail import EmailMultiAlternatives

from underground_email import app_settings
from underground_email.backends import SMTP2GoEmailBackend, message_payload

SENDER = "Fusion Party <info@fusionparty.org.au>"
RECIPIENT = "jordan.nguyen@example.com.au"
SUBJECT = "Password reset"
TEXT_BODY = "Follow the link below to choose a new password."
HTML_BODY = "<p>Follow the link below to choose a new password.</p>"
REPLY_TO = "membership@fusionparty.org.au"
ATTACHMENT_NAME = "privacy-policy.txt"
ATTACHMENT_CONTENT = b"We never share your details with third parties."
API_KEY = "api-0123456789ABCDEF"


def _password_reset_message() -> EmailMultiAlternatives:
    """Shaped like the message that allauth's DefaultAccountAdapter.render_mail builds."""
    message = EmailMultiAlternatives(SUBJECT, TEXT_BODY, SENDER, [RECIPIENT], reply_to=[REPLY_TO])
    message.attach_alternative(HTML_BODY, "text/html")
    return message


class MessagePayloadTest(unittest.TestCase):
    def test_text_and_html_parts_are_both_sent(self) -> None:
        payload = message_payload(_password_reset_message())
        self.assertEqual(payload["sender"], SENDER)
        self.assertEqual(payload["to"], [RECIPIENT])
        self.assertEqual(payload["subject"], SUBJECT)
        self.assertEqual(payload["text_body"], TEXT_BODY)
        self.assertEqual(
            payload["html_body"],
            HTML_BODY,
            "The text/html alternative must become SMTP2Go's html_body, so that mail "
            "clients that render HTML do not fall back to the plain-text part",
        )

    def test_reply_to_is_sent_as_a_custom_header(self) -> None:
        payload = message_payload(_password_reset_message())
        self.assertIn(
            {"header": "Reply-To", "value": REPLY_TO},
            payload["custom_headers"],
            "SMTP2Go's send endpoint has no reply-to field, so Reply-To must be a custom header",
        )

    def test_attachments_are_base64_encoded(self) -> None:
        message = _password_reset_message()
        message.attach(ATTACHMENT_NAME, ATTACHMENT_CONTENT, "text/plain")
        (attachment,) = message_payload(message)["attachments"]
        self.assertEqual(attachment["filename"], ATTACHMENT_NAME)
        self.assertEqual(attachment["mimetype"], "text/plain")
        self.assertEqual(base64.b64decode(attachment["fileblob"]), ATTACHMENT_CONTENT)

    def test_html_only_message_has_no_text_body(self) -> None:
        message = EmailMultiAlternatives(SUBJECT, HTML_BODY, SENDER, [RECIPIENT])
        message.content_subtype = "html"
        payload = message_payload(message)
        self.assertEqual(payload["html_body"], HTML_BODY)
        self.assertNotIn("text_body", payload)


class SMTP2GoEmailBackendTest(unittest.TestCase):
    """
    SMTP2Go is mocked here because a real request would deliver an email. The
    response bodies follow https://developers.smtp2go.com/reference/send-standard-email.
    """

    def _response(self, status_code: int, data: dict) -> MagicMock:
        response = MagicMock(status_code=status_code, ok=status_code < 400)
        response.json.return_value = {
            "request_id": "aa253464-0bd0-467a-b24b-6159dcd7be60",
            "data": data,
        }
        return response

    @patch.dict("os.environ", {"SMTP_KEY": API_KEY})
    @patch("underground_email.backends.requests.post")
    def test_accepted_message_is_counted_as_sent(self, post: MagicMock) -> None:
        from underground_email.tasks import _api_key

        _api_key.cache_clear()
        post.return_value = self._response(200, {"succeeded": 1, "failed": 0, "failures": []})

        sent = SMTP2GoEmailBackend().send_messages([_password_reset_message()])

        self.assertEqual(sent, 1, "SMTP2Go accepted the only message that was sent")
        (url,), kwargs = post.call_args
        self.assertEqual(url, app_settings.SMTP2GO_API_URL + "email/send")
        self.assertEqual(kwargs["headers"]["X-Smtp2go-Api-Key"], API_KEY)
        _api_key.cache_clear()

    @patch.dict("os.environ", {"SMTP_KEY": API_KEY})
    @patch("underground_email.backends.requests.post")
    def test_rejected_message_raises_unless_failing_silently(self, post: MagicMock) -> None:
        from underground_email.tasks import _api_key

        _api_key.cache_clear()
        post.return_value = self._response(
            400, {"succeeded": 0, "failed": 1, "failures": [RECIPIENT], "error": "Invalid sender"}
        )

        with self.assertRaises(RuntimeError):
            SMTP2GoEmailBackend().send_messages([_password_reset_message()])
        sent = SMTP2GoEmailBackend(fail_silently=True).send_messages([_password_reset_message()])
        self.assertEqual(sent, 0, "SMTP2Go rejected the only message that was sent")
        _api_key.cache_clear()
