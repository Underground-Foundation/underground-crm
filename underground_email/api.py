import enum
from typing import TypedDict, Dict, Union, List, Optional

# https://developers.smtp2go.com/docs/introduction-guide


class SMTP2GoEventType(enum.StrEnum):
    PROCESSED = "processed"
    SOFT_BOUNCED = "soft-bounced"
    HARD_BOUNCED = "hard-bounced"
    REJECTED = "rejected"
    SPAM = "spam"
    DELIVERED = "delivered"
    UNSUBSCRIBED = "unsubscribed"
    RESUBSCRIBED = "resubscribed"
    OPENED = "opened"
    CLICKED = "clicked"


BAD_OUTCOMES = (
    SMTP2GoEventType.SOFT_BOUNCED,
    SMTP2GoEventType.HARD_BOUNCED,
    SMTP2GoEventType.REJECTED,
    SMTP2GoEventType.SPAM,
    SMTP2GoEventType.UNSUBSCRIBED,
)


class DeliveryAttempt:
    smptime: str  # RFC3339
    host: str
    smtpresponse: str


class SMTPEvent(TypedDict, total=False):
    # https://developers.smtp2go.com/reference/search-activity
    from_: str  # "rob@example.co.uk"
    recipient: str  # "jo@another_example.com"
    subaccount_name: str  # "Master account"
    email_id: str  # "1u0SwL-B9zBpi9ffUq-JAB2" The unique ID of the email which generated the event
    date: str  # "2022-11-12T07:44:58Z". An RFC3339 encoded timestamp with UTC timezone indicating the timestamp of the event
    event: SMTP2GoEventType  # "delivered"
    subject: str  # "My Test Email"
    username: str  # "api-5BFDE1E62529"
    reply_to: Optional[str]
    sender: str  # "rob@example.co.uk"
    sender_full: Optional[str]
    to: str  # "jo@another_example.com"
    bcc: str  # "audit@example.co.uk"
    smtp_response: str  # "250 Message received"
    reason: Optional[str]  # The reason for an event occurring if present
    host: str  # "136.143.191.44"
    originating_host: Optional[
        str
    ]  # The originating IP address of the host associated with the processed event
    error: Optional[str]
    email_client: dict
    metadata: dict
    outbound_ip: str
    byte_size: int
    headers: str  # "Content-Type: text/html\nTo: to@example.com..."
    custom_headers: Dict[str, str]  # {"X-MyCustomID": "01HMSACEHXHDG4X1CZV89SQMP7"}
    delivery_attempts: List[DeliveryAttempt]


class SMTPActivityData(TypedDict):
    events: List[SMTPEvent]
    total_events: int
    continue_token: Optional[str]


class SMTPActivityResponse(TypedDict):
    data: SMTPActivityData
    request_id: str


# https://developers.smtp2go.com/docs/webhooks-overview#webhook-parameters---email


class GeoIPFields(TypedDict, total=False):
    geoip_content: str  # 2 character continent code
    geoip_country: str  # 2 character country code
    geoip_city: str  # Name of the city


class ClientInfoFields(TypedDict, total=False):
    user_agent: str
    read_secs: int
    client: str
    client_device: str
    client_os: str
    srchost: str


class WebhookEmailDict(TypedDict, total=False):
    # Core event fields
    event: str  # "processed", "delivered", "open", "click", "bounce", "spam", "unsubscribe", "resubscribe", or "reject"
    time: str  # UTC timestamp of when the event happened
    sendtime: str  # UTC timestamp of when the email was sent to our server

    # Sender/recipient fields
    sender: str  # The 'envelope-from' email address
    from_: str  # Display name and email address the email was sent from
    from_address: str  # The email address the email was sent from
    from_name: Optional[
        str
    ]  # The display name set to accompany the email address (where available)
    rcpt: str  # The email address the email was addressed to
    recipients: Union[str, List[str]]  # The email addresses the email was sent to

    # Authentication and routing
    auth: str  # The SMTP Username, API Key or IP Address used to send the email
    host: str  # The recipient server that bounced the message (bounce only)

    # Message details
    message: Optional[str]  # The error message we got (where available)
    context: Optional[str]  # Contains additional information on the event (where available)
    email_id: str  # Unique identifier for the email
    id: str  # The unique id for the webhook
    message_id: str  # The unique id from the sender (note: field name has hyphen)
    subject: str  # The subject of the email

    # Bounce-specific field
    bounce: Optional[str]  # "hard" or "soft" (bounce event only)

    # Open/click event fields
    user_agent: Optional[str]  # The "User-Agent" header of the device that opened the email
    read_secs: Optional[int]  # Number of seconds email was open (5-second increments, max 30)
    client: Optional[str]  # The reported client that clicked/opened the link
    client_device: Optional[str]  # The reported device type associated with the open/click event
    client_os: Optional[
        str
    ]  # The reported device operating system associated with the open/click event

    # GeoIP fields (open/click events)
    geoip_content: Optional[str]  # 2 character continent code
    geoip_country: Optional[str]  # 2 character country code
    geoip_city: Optional[str]  # Name of the city

    # IP address fields
    srchost: Optional[
        str
    ]  # IP address of end-user (open/click) or IP that submitted email (processed)
