"""
Django settings for the review web interface.
"""

import os
from pathlib import Path

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent

# Security settings
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key-change-in-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() == "true"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,*").split(",")
# Allow all hosts by default for Docker deployment


# --- Reverse proxy / Cloudflare Tunnel support (CSRF, HTTPS termination) ---
# When TLS terminates at Cloudflare, Django sees HTTP at the origin unless we trust
# X-Forwarded-Proto. Without this, CSRF Origin checks can fail for external access.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# External domain(s) allowed to POST forms (CSRF origin check)
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

# Make cookies compatible with HTTPS (browser will drop "secure" cookies otherwise)
CSRF_COOKIE_SECURE = os.environ.get("DJANGO_CSRF_COOKIE_SECURE", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SESSION_COOKIE_SECURE = os.environ.get("DJANGO_SESSION_COOKIE_SECURE", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "paperless_firefly.review.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "paperless_firefly.review.web.urls"

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

# Database for Django auth
# Use a separate SQLite file for user management
AUTH_DB_PATH = os.environ.get("AUTH_DB_PATH", "/app/data/auth.db")
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": AUTH_DB_PATH,
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

# Authentication
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Static files
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
        },
        "paperless_firefly": {
            "handlers": ["console"],
            "level": "INFO",
        },
    },
}

# Custom settings for our app
# These will be set at runtime from config
# Internal URLs (for container-to-container communication)
PAPERLESS_BASE_URL = os.environ.get("PAPERLESS_URL", "http://localhost:8000")
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_TOKEN", "")
FIREFLY_BASE_URL = os.environ.get("FIREFLY_URL", "http://localhost:8080")
FIREFLY_TOKEN = os.environ.get("FIREFLY_TOKEN", "")
STATE_DB_PATH = os.environ.get("STATE_DB_PATH", "state.db")

# External URLs (for browser links - what users actually access)
# If not set, falls back to internal URLs
PAPERLESS_EXTERNAL_URL = os.environ.get(
    "PAPERLESS_EXTERNAL_URL", os.environ.get("PAPERLESS_URL", "http://localhost:8000")
)
FIREFLY_EXTERNAL_URL = os.environ.get(
    "FIREFLY_EXTERNAL_URL", os.environ.get("FIREFLY_URL", "http://localhost:8080")
)

# Optional service links for landing page
SYNCTHING_URL = os.environ.get("SYNCTHING_URL", "")
FIREFLY_IMPORTER_URL = os.environ.get("FIREFLY_IMPORTER_URL", "")

# Session settings - Keep users logged in for convenience
SESSION_ENGINE = "django.contrib.sessions.backends.db"  # Use database-backed sessions
SESSION_COOKIE_AGE = 86400 * 7  # 1 week in seconds
SESSION_SAVE_EVERY_REQUEST = True  # Refresh session on every request
SESSION_EXPIRE_AT_BROWSER_CLOSE = False  # Don't expire on browser close by default
SESSION_COOKIE_SAMESITE = "Lax"  # Prevent CSRF but allow same-site navigation
SESSION_COOKIE_HTTPONLY = True  # JavaScript cannot access session cookie
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() in (
    "true",
    "1",
    "yes",
)  # Set True in production with HTTPS

# LedgerBridge specific settings
# Debug mode: When True, shows detailed tracebacks in error messages
# Set via environment variable: LEDGERBRIDGE_DEBUG=true
LEDGERBRIDGE_DEBUG = os.environ.get("LEDGERBRIDGE_DEBUG", "false").lower() in ("true", "1", "yes")

# Filter tag for document extraction
PAPERLESS_FILTER_TAG = os.environ.get("PAPERLESS_FILTER_TAG", "finance/inbox")
