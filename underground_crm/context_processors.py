from django.conf import settings
from django.http import HttpRequest


def enabled_social_providers(request: HttpRequest) -> dict:
    """Exposes ENABLED_SOCIAL_PROVIDERS to every template as ``enabled_social_providers``."""
    return {"enabled_social_providers": settings.ENABLED_SOCIAL_PROVIDERS}


def session_settings(request: HttpRequest) -> dict:
    """Exposes session-related settings to every template. Anyone else using the cache needs to know a suitable
    duration for their own values to last."""
    return {"session_cookie_age": settings.SESSION_COOKIE_AGE}
