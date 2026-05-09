import enum


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
