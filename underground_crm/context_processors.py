from django.conf import settings
from django.http import HttpRequest


def enabled_social_providers(request: HttpRequest) -> dict:
    """Exposes ENABLED_SOCIAL_PROVIDERS to every template as ``enabled_social_providers``."""
    return {"enabled_social_providers": settings.ENABLED_SOCIAL_PROVIDERS}
