"""
Base Django settings for any site that uses underground_crm.

Downstream sites should import these with ``from underground_crm.settings import *``
and then override or extend as needed.  At minimum, they must supply:

  - WSGI_APPLICATION
  - STATIC_ROOT / MEDIA_ROOT  (paths depend on the deployment's BASE_DIR)

All other settings have sensible defaults and can be overridden as needed.
"""

import logging as _logging
import os
from pathlib import Path

# These paths should be overridden by an inheriting app
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_ROOT = BASE_DIR / "static"
MEDIA_ROOT = BASE_DIR / "media"

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-secret-key-replace-before-deploying-to-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost 127.0.0.1").split()

INSTALLED_APPS = [
    # Underground CRM library
    "underground_crm",
    # Django-money
    "djmoney",
    # Color picker widget
    "colorfield",
    # Wagtail
    "wagtail.contrib.forms",
    "wagtail.contrib.redirects",
    "wagtail.embeds",
    "wagtail.sites",
    "wagtail.users",
    "wagtail.snippets",
    "wagtail.documents",
    "wagtail.images",
    "wagtail.search",
    "wagtail.admin",
    "wagtail",
    "modelcluster",
    "taggit",
    "phonenumber_field",
    # https://django-q2.readthedocs.io/en/master/install.html
    "django_q",
    # Django REST framework
    "rest_framework",
    "rest_framework_simplejwt",
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.sites",
    "underground_email",
    "underground_payments",
    "simple_history",
    # django-allauth — https://docs.allauth.org/en/latest/installation/quickstart.html
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    # Active social login providers:
    "allauth.socialaccount.providers.discord",
    "allauth.socialaccount.providers.facebook",
    "allauth.socialaccount.providers.instagram",
    "allauth.socialaccount.providers.openid_connect",  # used for LinkedIn
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "wagtail.contrib.redirects.middleware.RedirectMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "underground_crm.site_urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "underground_crm.context_processors.enabled_social_providers",
                "underground_crm.context_processors.session_settings",
            ],
        },
    },
]

AUTH_USER_MODEL = "underground_crm.Person"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-au"
PHONE_REGION = "AU"
DEFAULT_COUNTRY = PHONE_REGION  # Used for signup forms
TIME_ZONE = "Australia/Melbourne"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
MEDIA_URL = "/media/"

_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

# https://docs.djangoproject.com/en/6.0/topics/cache/#redis
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _REDIS_URL,
    }
}

# https://django-q2.readthedocs.io/en/master/brokers.html#redis
Q_CLUSTER = {
    "name": "underground_crm",
    "redis": _REDIS_URL,
}


# todo: configure Sentry to notify us of queued email failures:
#  https://django-q.readthedocs.io/en/latest/configure.html#error-reporter

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Stripe — set all three in environment. STRIPE_PUBLISHABLE_KEY is forwarded to
# the browser; STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET stay server-side only.
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
# https://docs.stripe.com/currencies#presentment-currencies
STRIPE_DEFAULT_CURRENCY = os.environ.get("STRIPE_DEFAULT_CURRENCY", "aud")

_pg_name = os.environ.get("PGDATABASE", "")
if _pg_name:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _pg_name,
            "USER": os.environ.get("PGUSER", ""),
            "PASSWORD": os.environ.get("PGPASSWORD", ""),
            "HOST": os.environ.get("PGHOST", ""),
            "PORT": os.environ.get("PGPORT", "5432"),
        }
    }
else:
    # By running in memory, this database is handy for our CI/CD pipeline.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

# Wagtail
WAGTAILADMIN_BASE_URL = os.environ.get("WAGTAILADMIN_BASE_URL", "http://localhost:8000")

WAGTAIL_SITE_NAME = "Underground CRM"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

ADDRESSR_BASE_URL = os.environ.get("ADDRESSR_BASE_URL", "http://localhost:8080")

# VERBOSE controls log verbosity (matches the convention used across services):
#   0 = INFO  (default)
#   1 = DEBUG
#   2 = WARNING  (out of order, preserved for historical consistency)
try:
    _verbose = int(os.environ.get("VERBOSE", "0"))
except (ValueError, TypeError):
    _verbose = 0

if _verbose == 2:
    _LOG_LEVEL = "WARNING"
elif _verbose == 1:
    _LOG_LEVEL = "DEBUG"
else:
    _LOG_LEVEL = "INFO"

# Frameworks and transport libraries that are too chatty at DEBUG; always kept at WARNING.
_NOISY_LOGGERS = [
    "django",
    "django_q",
    "urllib3",
    "wagtail",
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "colored": {
            "()": "underground_crm.logging_config.ColoredFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "colored",
        },
    },
    "loggers": {
        "underground_crm": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        "underground_email": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        # requests is logged at the same verbosity as application code so that
        # outbound HTTP calls to SMTP2Go and similar services appear in traces.
        "requests": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        **{
            name: {"handlers": ["console"], "level": "WARNING", "propagate": False}
            for name in _NOISY_LOGGERS
        },
    },
}

LOGIN_URL = "/account/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Required by django.contrib.sites (used by allauth)
SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# django-allauth account settings
# https://docs.allauth.org/en/latest/account/configuration.html
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_USER_MODEL_USERNAME_FIELD = None

# django-allauth social account settings
# https://docs.allauth.org/en/latest/socialaccount/configuration.html
SOCIALACCOUNT_ADAPTER = "underground_crm.social_adapters.PersonSocialAccountAdapter"
# When a "social" OAuth provider supplies a verified email address that matches an
# existing account in our system, we'll sign the user into that account rather than
# throwing an error.
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
# Upon signing in through a "social" OAuth provider, the OAuth identity will "automatically"
# be linked permanently to a Person record. So even if they go on to change their Facebook
# email address, that Facebook identity is still tied to the user with their original email
# address, in our system.
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

_startup_logger = _logging.getLogger(__name__)


def _missing_env_vars(*names: str) -> list[str]:
    return [name for name in names if not os.environ.get(name, "")]


def _build_social_providers() -> tuple[dict, list[str]]:
    """
    Constructs SOCIALACCOUNT_PROVIDERS from environment variables.
    Any provider whose credentials are absent is omitted, and a warning
    identifying the missing variable(s) is logged at import time.

    Returns a (providers, enabled_names) tuple.  ``enabled_names`` uses the
    public provider ID in every case (e.g. ``"linkedin"``, not the allauth
    key ``"openid_connect"``), so templates can check membership directly.
    """
    providers: dict = {}
    enabled: list[str] = []

    def _warn(provider: str, missing: list[str]) -> None:
        verb = "is" if len(missing) == 1 else "are"
        _startup_logger.warning(
            "%s social login is disabled: %s %s not set.",
            provider,
            " and ".join(missing),
            verb,
        )

    missing = _missing_env_vars("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET")
    if missing:
        _warn("Discord", missing)
    else:
        providers["discord"] = {
            # Discord requires a verified email address before allowing OAuth,
            # so any email returned by Discord is guaranteed to be verified.
            "VERIFIED_EMAIL": True,
            "APP": {
                "client_id": os.environ.get("DISCORD_CLIENT_ID"),
                "secret": os.environ.get("DISCORD_CLIENT_SECRET"),
            },
        }
        enabled.append("discord")

    missing = _missing_env_vars("FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET")
    if missing:
        _warn("Facebook", missing)
    else:
        providers["facebook"] = {
            "SCOPE": ["email", "public_profile"],
            "AUTH_PARAMS": {"auth_type": "reauthenticate"},
            # allauth's Facebook provider hardcodes verified=False on email addresses
            # because data['verified'] (account verification) does not imply email
            # verification. However, Facebook does verify email addresses independently,
            # so we override this here to allow email-based account matching.
            "VERIFIED_EMAIL": True,
            "APP": {
                "client_id": os.environ.get("FACEBOOK_APP_ID"),
                "secret": os.environ.get("FACEBOOK_APP_SECRET"),
            },
        }
        enabled.append("facebook")

    # LinkedIn is now an OpenID Connect provider — configured via the generic
    # openid_connect provider rather than a dedicated LinkedIn app entry.
    # https://docs.allauth.org/en/latest/socialaccount/providers/linkedin.html
    missing = _missing_env_vars("LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET")
    if missing:
        _warn("LinkedIn", missing)
    else:
        providers["openid_connect"] = {
            "APPS": [
                {
                    "provider_id": "linkedin",
                    "name": "LinkedIn",
                    "client_id": os.environ.get("LINKEDIN_CLIENT_ID"),
                    "secret": os.environ.get("LINKEDIN_CLIENT_SECRET"),
                    "settings": {
                        "server_url": "https://www.linkedin.com/oauth",
                    },
                },
            ],
        }
        enabled.append("linkedin")

    return providers, enabled


SOCIALACCOUNT_PROVIDERS, ENABLED_SOCIAL_PROVIDERS = _build_social_providers()


UNDERGROUND_COLOR_PALETTE = [
    ("#77ff33", "Green"),  # Add your own brand colours here for UI widgets
]

UNDERGROUND_BUTTON_BACKGROUND_PALETTE = [
    ("#46d3e0", "Opal"),
]

UNDERGROUND_EMAIL_BUTTON_TEXT_COLORS = [
    ("#ffffff", "White"),
    ("#000000", "Black"),
]
