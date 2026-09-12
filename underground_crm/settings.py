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
    "wagtail.contrib.routable_page",
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
    # https://pypi.org/project/Collectfast/
    # Collectfast compares checksums locally with S3/R2. It must come before
    # django.contrib.staticfiles.
    "collectfast",
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
    "allauth.socialaccount.providers.openid_connect",  # used for LinkedIn
    "compressor",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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

COMPRESS_CSS_FILTERS = [
    "compressor.filters.css_default.CssAbsoluteFilter",
    "compressor.filters.csscompressor.CSSCompressorFilter",
]

_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

# https://docs.djangoproject.com/en/6.0/topics/cache/#redis
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _REDIS_URL,
    },
    # A dedicated cache holding collectfast's record of the checksum each static file
    # currently has in the remote bucket. With a warm cache, a deployment without any changed
    # assets will avoid any S3 round trips − collectfast falls back to one HEAD per file.
    #
    # The cache only takes effect for COLLECTFAST_ENABLED=true, see below.
    "collectfast": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _REDIS_URL,
        "KEY_PREFIX": "collectfast",
        "TIMEOUT": None,
    },
}

# https://django-q2.readthedocs.io/en/master/brokers.html#redis
Q_CLUSTER = {
    "name": "underground_crm",
    "redis": _REDIS_URL,
    # Two minutes, after which a task is killed with a TimeoutException.  Left unset, a
    # task that hangs — an outbound API call with no socket timeout of its own, say —
    # would occupy one of the cluster's workers indefinitely, and enough of those would
    # stall the queue with nothing in the logs to explain why.
    #
    # Individual tasks override this with async_task(..., timeout=N) where the work is
    # legitimately longer; underground_email.tasks.send_emails is the one that does.  Any
    # new task that can outlive two minutes needs the same treatment, so weigh that up
    # before raising this ceiling for everything.
    "timeout": 120,
    # Only brokers that acknowledge deliveries re-queue an unacknowledged task after this
    # many seconds, and the Redis broker above is not one of them — Broker.acknowledge()
    # is a no-op for it, so nothing is ever redelivered and this value has no practical
    # effect here.  django-q2 nonetheless validates the pair at startup and warns unless
    # timeout is set and no larger than retry, so the two are kept in the documented
    # relationship: correct if the broker is ever swapped for one that does acknowledge,
    # and quiet in the logs either way.
    "retry": 180,
}

_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})


def _env_flag(name: str, *, default: bool) -> bool:
    """
    Reads a boolean environment variable, accepting the spellings people actually
    type ("0", "no", "off", …) rather than "true" alone.

    A comparison such as ``os.environ.get(name, "true") == "true"`` silently reads
    an unrecognized value as False, which is a trap for a variable that defaults to
    on: setting it to "1" to mean "yes" would turn the feature off.  Anything
    unrecognized therefore keeps the default and says so, rather than quietly
    inverting the operator's intent.
    """
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    _logging.getLogger(__name__).warning(
        "Ignoring unrecognized value %r for %s; using the default of %s. Use one of %s or %s.",
        raw,
        name,
        default,
        ", ".join(sorted(_TRUE_VALUES)),
        ", ".join(sorted(_FALSE_VALUES)),
    )
    return default


# collectfast — https://github.com/antonagestam/collectfast
#
# Only needed when we're using remote caching (S3/R2).
#
# This covers only the copying phase. The ManifestStaticFilesStorage post-processing is Django's
# own and still runs in full on every deployment.
COLLECTFAST_ENABLED = _env_flag("COLLECTFAST_ENABLED", default=False)
# django-storages' S3 backend, which also drives Cloudflare R2. Named here rather than left
# to each theme project because it is the only strategy any current deployment uses; a
# deployment on a different backend can still override it. Read only when enabled.
COLLECTFAST_STRATEGY = os.environ.get(
    "COLLECTFAST_STRATEGY", "collectfast.strategies.boto3.Boto3Strategy"
)
COLLECTFAST_CACHE = "collectfast"

# Error tracking — GlitchTip (self-hosted, Sentry-API-compatible) or Sentry itself.
# Point SENTRY_DSN at either service; leave it unset (the default) to disable error
# tracking entirely, e.g. in tests or local development.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
# The master switch for everything sent to the error tracker: issues, log records,
# performance traces, the lot.  SENTRY_ENABLED=false skips sentry_sdk.init() outright,
# so no client is ever configured and nothing can reach the network no matter what the
# other SENTRY_* variables say.  It is a single toggle for silencing a deployment (or a
# local session) without having to blank out a DSN you want to keep.
SENTRY_ENABLED = _env_flag("SENTRY_ENABLED", default=True)
# Whether a deployment actually asked for error tracking, as opposed to inheriting the
# default above.  The two are indistinguishable from SENTRY_ENABLED alone, but they want
# opposite treatment when no DSN is configured: an unset variable means ordinary local
# development, where silence is correct, while SENTRY_ENABLED=true is an explicit request
# that cannot be honoured and must not pass quietly.  See the warning below.
_SENTRY_ENABLED_EXPLICITLY = bool(os.environ.get("SENTRY_ENABLED", "").strip())
SENTRY_ENVIRONMENT = os.environ.get("SENTRY_ENVIRONMENT", "development" if DEBUG else "production")
# Off by default: this CRM holds personal data about members and donors, and the error
# tracker is a third-party service even when self-hosted, so PII (e.g. the logged-in
# user on a request) is only attached to events once a deployment opts in explicitly.
SENTRY_SEND_DEFAULT_PII = _env_flag("SENTRY_SEND_DEFAULT_PII", default=False)
# Identifies which build produced an event.  Left empty by default; a deployment
# should set it to a commit SHA or version tag so regressions can be traced to a release.
SENTRY_RELEASE = os.environ.get("SENTRY_RELEASE", "")
try:
    SENTRY_TRACES_SAMPLE_RATE = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0"))
except ValueError:
    SENTRY_TRACES_SAMPLE_RATE = 0.0

# Governs which ordinary log records (not just errors) are forwarded to the tracker's
# log view.  Such records travel in the same envelopes over the same endpoint as error
# events, so no separate OpenTelemetry collector is needed; GlitchTip wraps them into
# its OTel-format log store on receipt.
#
# Set a threshold — DEBUG, INFO, WARNING, ERROR or CRITICAL — to forward records at that
# level and above; the default INFO is deliberately independent of VERBOSE/_LOG_LEVEL
# below, since turning up console verbosity while debugging locally should not start
# shipping DEBUG records to a remote service.  Set it to "off" (or "none") to forward no
# log records at all, leaving error *events* still reported; to silence those as well,
# use the SENTRY_ENABLED master switch above.
_SENTRY_LOGS_OFF_VALUES = frozenset({"off", "none", "disabled"})
SENTRY_LOGS_LEVEL = os.environ.get("SENTRY_LOGS_LEVEL", "INFO").strip().upper()


def _resolve_sentry_logs_level(setting: str) -> int | None:
    """
    Translates SENTRY_LOGS_LEVEL into a numeric logging threshold, or ``None`` to
    mean "forward no log records".  An unrecognized value keeps the INFO default
    and warns, rather than silently forwarding nothing or everything.
    """
    if setting.lower() in _SENTRY_LOGS_OFF_VALUES:
        return None
    level = _logging.getLevelNamesMapping().get(setting)
    if level is not None:
        return level
    _logging.getLogger(__name__).warning(
        "Ignoring unrecognized value %r for SENTRY_LOGS_LEVEL; forwarding logs at "
        "INFO and above. Use a level name (DEBUG, INFO, WARNING, ERROR, CRITICAL) "
        "or one of %s to forward no logs.",
        setting,
        ", ".join(sorted(_SENTRY_LOGS_OFF_VALUES)),
    )
    return _logging.INFO


_SENTRY_LOGS_LEVEL = _resolve_sentry_logs_level(SENTRY_LOGS_LEVEL)

if SENTRY_DSN and SENTRY_ENABLED:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        release=SENTRY_RELEASE or None,
        integrations=[
            DjangoIntegration(),
            LoggingIntegration(
                # Breadcrumbs from INFO and above, attached to whatever event follows.
                level=_logging.INFO,
                # Only ERROR and above are raised as issues to be triaged.
                event_level=_logging.ERROR,
                # Everything from SENTRY_LOGS_LEVEL up is also forwarded verbatim to
                # the log view, which is what makes routine INFO/WARNING records
                # visible there rather than only as breadcrumbs on an error.  When log
                # forwarding is switched off this is left at its INFO default and takes
                # no effect, since enable_logs below is what gates the log envelopes.
                sentry_logs_level=_SENTRY_LOGS_LEVEL or _logging.INFO,
            ),
        ],
        # None from _resolve_sentry_logs_level() means SENTRY_LOGS_LEVEL was "off":
        # forward no log records, while still reporting error events.
        enable_logs=_SENTRY_LOGS_LEVEL is not None,
        traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=SENTRY_SEND_DEFAULT_PII,
        # GlitchTip has no session-tracking feature, so the release-health sessions the
        # SDK uploads by default are discarded on arrival; not sending them saves the
        # request. Sentry proper would use them, so flip this if the DSN points there.
        auto_session_tracking=False,
    )

    from sentry_sdk.integrations.logging import ignore_logger

    # django-q2's monitor logs "Failed '<task>' — <result>" at ERROR for every failed
    # task, in addition to the exception the reporter below sends. Without this, each
    # failure would raise two issues: the reporter's, carrying a real stack trace, and
    # a bare duplicate from this log line. Only event creation is suppressed, so the
    # line still reaches the log view; note the logger's name is hyphenated, unlike the
    # "django_q" module path.
    ignore_logger("django-q")

    # django-q2 only reports queued-task exceptions (e.g. a failed queued email send)
    # through this hook: its worker stores the exception in the task result instead of
    # re-raising or logging it, so the integrations above cannot see it.
    # https://django-q2.readthedocs.io/en/master/configure.html#error-reporter
    #
    # The reporter is resolved through the "djangoq.errorreporters" entry point group,
    # and this library registers its own (see underground_crm/error_reporting.py) rather
    # than using django-q-sentry, whose reporter re-runs sentry_sdk.init() and thereby
    # replaces the client configured above.  It needs no configuration of its own.
    Q_CLUSTER["error_reporter"] = {"underground_crm": {}}

elif _SENTRY_ENABLED_EXPLICITLY and SENTRY_ENABLED:
    # SENTRY_ENABLED=true with no SENTRY_DSN is the one combination that fails silently:
    # sentry_sdk.init() above is skipped, so no client exists, every capture_exception()
    # call in the codebase becomes a no-op, and the queued-task reporter is never even
    # registered — yet nothing anywhere says so, and a deployment looks healthy precisely
    # because no errors are arriving.  Since the operator explicitly asked for error
    # tracking, say plainly that it is off and why.
    _logging.getLogger(__name__).error(
        "SENTRY_ENABLED is set but SENTRY_DSN is empty, so error tracking is OFF: "
        "no exceptions, log records or queued-task failures will reach GlitchTip/Sentry. "
        "Set SENTRY_DSN to your GlitchTip project DSN, or set SENTRY_ENABLED=false to "
        "turn error tracking off deliberately and silence this message."
    )

# todo: add a Content-Security-Policy (report-only to start) so injected-script attempts
#  (e.g. via Wagtail's RawHTML StreamField block) get reported to GlitchTip/Sentry via
#  SENTRY_SECURITY_ENDPOINT. Needs an audit of inline <script>/<style> usage across
#  templates first — likely via django-csp, which handles per-request nonces for those.
#  Malicious scripts could also get in through supply-chain attacks:
#  https://www.web3isgoinggreat.com/?id=polymarket-vendor-exploit

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

# Sender for transactional email (e.g. subscription confirmations), dispatched
# through SMTP2Go — the domain must be registered there.
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "webmaster@localhost")

# Allow users to query these tags in calls to /me (?has-tag=<slug>). Every
# other tag on a Person is internal CRM data and is never exposed to them.
SELF_QUERYABLE_TAGS = ["Newsletter", "Press releases"]

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

# The People filter map in the Django admin draws its markers with Leaflet and its base
# map with OpenStreetMap tiles. Neither is vendored into this library, so that it does
# not carry a copy of somebody else's minified JavaScript, and every URL below is
# overridable for deployments that self-host their assets, cannot reach a public CDN, or
# have their own tile server. The OpenStreetMap Foundation's tile usage policy allows the
# incidental traffic an internal admin page generates, but not a public-facing map.
LEAFLET_CSS_URL = os.environ.get(
    "LEAFLET_CSS_URL", "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
)
LEAFLET_JS_URL = os.environ.get("LEAFLET_JS_URL", "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js")

# Subresource integrity digests, as published alongside those two files. They are what
# stops a compromised CDN from running its own code inside the admin, so leave them set
# whenever the URLs above point at a third party. A self-hosted copy will not match these
# digests, so point the URLs at it and set both of these to an empty string together.
LEAFLET_CSS_INTEGRITY = os.environ.get(
    "LEAFLET_CSS_INTEGRITY", "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
)
LEAFLET_JS_INTEGRITY = os.environ.get(
    "LEAFLET_JS_INTEGRITY", "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
)

# A Leaflet tile URL template: {s} is the subdomain, {z}/{x}/{y} the tile coordinates.
MAP_TILE_URL = os.environ.get("MAP_TILE_URL", "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png")
MAP_TILE_ATTRIBUTION = os.environ.get(
    "MAP_TILE_ATTRIBUTION",
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
)

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
            # Adds "picture" (returned as a large image) to allauth's default FIELDS list,
            # so PersonSocialAccountAdapter can save a profile picture URL. Facebook's Graph
            # API doesn't surface this via extract_common_fields, so it's read from the raw
            # extra_data instead — see PersonSocialAccountAdapter._facebook_picture_url.
            "FIELDS": [
                "id",
                "email",
                "name",
                "first_name",
                "last_name",
                "verified",
                "locale",
                "timezone",
                "link",
                "gender",
                "updated_time",
                "picture.type(large)",
            ],
            "APP": {
                "client_id": os.environ.get("FACEBOOK_APP_ID"),
                "secret": os.environ.get("FACEBOOK_APP_SECRET"),
            },
        }
        enabled.append("facebook")

    # LinkedIn is now an OpenID Connect provider — configured via the generic
    # openid_connect provider rather than a dedicated LinkedIn app entry.
    # https://docs.allauth.org/en/latest/socialaccount/providers/linkedin.html
    # No explicit SCOPE is set, so allauth requests its default "openid profile
    # email" — this is already the maximum LinkedIn grants under its self-serve
    # "Sign In with LinkedIn using OpenID Connect" product, and covers every
    # field we can get (name, given_name, family_name, picture, email). Wider
    # profile data (skills, positions, etc.) needs LinkedIn's gated Profile API
    # and partner approval, not something reachable via extra scopes here.
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
