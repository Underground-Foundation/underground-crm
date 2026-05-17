"""
Base Django settings for any site that uses underground_crm.

Downstream sites should import these with ``from underground_crm.settings import *``
and then override or extend as needed.  At minimum they must supply:

  - WSGI_APPLICATION
  - STATIC_ROOT / MEDIA_ROOT  (paths depend on the deployment's BASE_DIR)

All other settings have sensible defaults and can be overridden as needed.
"""

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
    "underground_email",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "wagtail.contrib.redirects.middleware.RedirectMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PGDATABASE", ""),
        "USER": os.environ.get("PGUSER", ""),
        "PASSWORD": os.environ.get("PGPASSWORD", ""),
        "HOST": os.environ.get("PGHOST", ""),
        "PORT": os.environ.get("PGPORT", "5432"),
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

UNDERGROUND_EMAIL_BUTTON_TEXT_COLORS = [
    ("#ffffff", "White"),
    ("#000000", "Black"),
]
