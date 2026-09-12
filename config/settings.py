"""
Navvi — settings.py

Assumes a project named `navvi_project` with a single app `navvi` for now
(matches models.py / views.py / urls.py delivered so far). Split settings
into base/dev/prod files once the project grows — this is one file to keep
things simple while you're still building out the core.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Core / security
# ---------------------------------------------------------------------------

# Never hard-code this — set NAVVI_SECRET_KEY in your environment.
SECRET_KEY = os.environ.get("NAVVI_SECRET_KEY", "insecure-dev-key-change-me")

DEBUG = os.environ.get("NAVVI_DEBUG", "True") == "True"

ALLOWED_HOSTS = os.environ.get(
    "NAVVI_ALLOWED_HOSTS",
    "localhost,127.0.0.1"
).split(",")

CSRF_TRUSTED_ORIGINS = (
    os.environ.get("NAVVI_CSRF_TRUSTED_ORIGINS", "").split(",")
    if os.environ.get("NAVVI_CSRF_TRUSTED_ORIGINS")
    else []
)

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "navvi",  # single app for now — split into accounts/nurses/bookings/etc. later
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves manifest.json/sw.js/static in prod without a separate server
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "config.wsgi.application"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "navvi.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Where @login_required sends unauthenticated users, and where the various
# role-specific views redirect after login (adjust once you have a login
# view that branches by role).
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"


# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static & media files
#
# manifest.json and the service worker (sw.js) should live under STATICFILES_DIRS
# (or a dedicated /pwa/ static app) so WhiteNoise serves them with the right
# headers. The service worker MUST be served from the site root scope
# (e.g. /sw.js, not /static/sw.js) to control the whole app — see the PWA
# shell setup for the exact url pattern needed.
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Nurse credential/ID uploads (NurseDocument.file). Move to S3/cloud storage
# (e.g. django-storages) before production — local disk won't survive
# redeploys on most hosting platforms and isn't appropriate for ID documents
# long-term without additional access controls.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# ---------------------------------------------------------------------------
# Third-party integrations
# ---------------------------------------------------------------------------

PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY", "")

# WhatsApp/SMS provider credentials (fill in once the provider is chosen)
WHATSAPP_API_TOKEN = os.environ.get("WHATSAPP_API_TOKEN", "")
SMS_PROVIDER_API_KEY = os.environ.get("SMS_PROVIDER_API_KEY", "")

# IVR provider credentials
IVR_PROVIDER_API_KEY = os.environ.get("IVR_PROVIDER_API_KEY", "")


# ---------------------------------------------------------------------------
# Background jobs
#
# Payment reconciliation, milestone release scheduling, and notification
# delivery (PRD section K) need a task queue. Uncomment and configure once
# Redis/Celery are set up — listed here as a placeholder so it isn't
# forgotten.
# ---------------------------------------------------------------------------

# CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
# CELERY_RESULT_BACKEND = CELERY_BROKER_URL
# CELERY_ACCEPT_CONTENT = ["json"]
# CELERY_TASK_SERIALIZER = "json"
# CELERY_TIMEZONE = TIME_ZONE


# ---------------------------------------------------------------------------
# Logging
#
# Structured logging per PRD section K (reliability/monitoring). Emergency
# referral and payment/escrow events should always be visible here.
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "navvi.payments": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "navvi.emergency": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}