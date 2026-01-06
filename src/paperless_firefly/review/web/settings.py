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
LOGOUT_REDIRECT_URL = "/login/"

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
PAPERLESS_EXTERNAL_URL = os.environ.get("PAPERLESS_EXTERNAL_URL", os.environ.get("PAPERLESS_URL", "http://localhost:8000"))
FIREFLY_EXTERNAL_URL = os.environ.get("FIREFLY_EXTERNAL_URL", os.environ.get("FIREFLY_URL", "http://localhost:8080"))

# Optional service links for landing page
SYNCTHING_URL = os.environ.get("SYNCTHING_URL", "")
FIREFLY_IMPORTER_URL = os.environ.get("FIREFLY_IMPORTER_URL", "")

# Session settings
SESSION_COOKIE_AGE = 86400 * 7  # 1 week
SESSION_SAVE_EVERY_REQUEST = True
