"""
A Django email backend that delivers through SMTP2Go's HTTP API, so that mail sent with
django.core.mail (such as allauth's password-reset and email-confirmation messages)
goes out the same way as campaign email, authenticated by the SMTP_KEY environment
variable (see underground_email.tasks._api_key).
"""

import base64
import logging
import mimetypes
from email.mime.base import MIMEBase

import requests
from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend

from underground_email import app_settings

logger = logging.getLogger(__name__)

_SEND_TIMEOUT_SECONDS = 30


def _attachment_payload(attachment) -> dict:
    if isinstance(attachment, MIMEBase):
        content = attachment.get_payload(decode=True)
        filename = attachment.get_filename()
        mimetype = attachment.get_content_type()
    else:
        filename, content, mimetype = attachment
        if isinstance(content, str):
            content = content.encode()
        mimetype = mimetype or mimetypes.guess_type(filename or "")[0]
    return {
        "filename": filename,
        "fileblob": base64.b64encode(content).decode("ascii"),
        "mimetype": mimetype or "application/octet-stream",
    }


def message_payload(message: EmailMessage) -> dict:
    """
    Translate a Django EmailMessage into the body of a request to SMTP2Go's
    email/send endpoint (https://developers.smtp2go.com/reference/send-standard-email),
    omitting the API key, which is sent as a header.
    """
    text_body = None
    html_body = None
    if message.content_subtype == "html":
        html_body = message.body
    else:
        text_body = message.body
    for content, mimetype in getattr(message, "alternatives", []):
        if mimetype == "text/html":
            html_body = content

    headers = dict(message.extra_headers)
    if message.reply_to:
        headers.setdefault("Reply-To", ", ".join(message.reply_to))

    payload = {
        "sender": message.from_email,
        "to": list(message.to),
        "subject": message.subject,
    }
    if message.cc:
        payload["cc"] = list(message.cc)
    if message.bcc:
        payload["bcc"] = list(message.bcc)
    if text_body:
        payload["text_body"] = text_body
    if html_body:
        payload["html_body"] = html_body
    if headers:
        payload["custom_headers"] = [
            {"header": header, "value": str(value)} for header, value in headers.items()
        ]
    if message.attachments:
        payload["attachments"] = [_attachment_payload(a) for a in message.attachments]
    return payload


class SMTP2GoEmailBackend(BaseEmailBackend):
    """
    Sends each message with one request to SMTP2Go's email/send endpoint, at
    app_settings.SMTP2GO_API_URL. A message counts as sent when SMTP2Go reports that
    it accepted every recipient.
    """

    def send_messages(self, email_messages) -> int:
        from underground_email.tasks import _api_key

        sent_count = 0
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                response = requests.post(
                    app_settings.SMTP2GO_API_URL + "email/send",
                    json=message_payload(message),
                    headers={"X-Smtp2go-Api-Key": _api_key()},
                    timeout=_SEND_TIMEOUT_SECONDS,
                )
                data = response.json().get("data", {})
                if response.ok and not data.get("failed") and data.get("succeeded"):
                    sent_count += 1
                    continue
                error = RuntimeError(
                    f"SMTP2Go rejected the message to {message.recipients()} "
                    f"(HTTP {response.status_code}): {data.get('failures') or data.get('error')}"
                )
            except Exception as exc:
                error = exc
            if not self.fail_silently:
                raise error
            logger.error("Failed to send %r through SMTP2Go: %s", message.subject, error)
        return sent_count
