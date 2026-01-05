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
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "paperless_firefly.review.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
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
            ],
        },
    },
]

# Database - we use our own SQLite state store, not Django ORM
DATABASES = {}

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
PAPERLESS_BASE_URL = os.environ.get("PAPERLESS_URL", "http://localhost:8000")
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_TOKEN", "")
FIREFLY_BASE_URL = os.environ.get("FIREFLY_URL", "http://localhost:8080")
STATE_DB_PATH = os.environ.get("STATE_DB_PATH", "state.db")
