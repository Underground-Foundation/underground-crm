from django.conf import settings

# Base URL for the smtp2go API.  Override in your project's settings if you
# need a regional endpoint (e.g. "https://au-api.smtp2go.com/v3/").
SMTP2GO_API_URL: str = getattr(settings, "SMTP2GO_API_URL", "https://api.smtp2go.com/v3/")

# Signing key for unsubscribe link tokens. Set this independently of SECRET_KEY
# so it can be rotated without invalidating sessions or CSRF tokens.
# Falls back to SECRET_KEY if not set.
UNSUBSCRIBE_SIGNING_KEY: str = getattr(settings, "UNSUBSCRIBE_SIGNING_KEY", "")
