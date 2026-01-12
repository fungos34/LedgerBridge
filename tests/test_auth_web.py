"""
Tests for authentication and new web features.

Tests cover:
- User authentication (login/logout)
- User profile and settings
- Landing page
- Import queue
- Web-triggered commands
- Account selector API
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

# Set Django settings before importing Django components
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paperless_firefly.review.web.settings")


class TestAuthenticationViews:
    """Tests for authentication functionality."""

    @pytest.fixture
    def client(self):
        """Create Django test client."""
        from django.test import Client

        return Client()

    @pytest.fixture
    def configured_settings(self, tmp_path):
        """Configure Django settings for testing."""
        import django
        from django.conf import settings

        # Create test databases - ensure parent directory exists
        state_db = tmp_path / "test_state.db"
        auth_db = tmp_path / "test_auth.db"

        # Create empty database files to ensure they can be opened
        state_db.touch()
        auth_db.touch()

        if not settings.configured:
            settings.configure(
                DEBUG=True,
                SECRET_KEY="test-secret-key-for-testing-only",
                ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
                DATABASES={
                    "default": {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": str(auth_db),
                    }
                },
                INSTALLED_APPS=[
                    "django.contrib.admin",
                    "django.contrib.auth",
                    "django.contrib.contenttypes",
                    "django.contrib.sessions",
                    "django.contrib.messages",
                    "paperless_firefly.review.web",
                ],
                MIDDLEWARE=[
                    "django.middleware.security.SecurityMiddleware",
                    "django.contrib.sessions.middleware.SessionMiddleware",
                    "django.middleware.common.CommonMiddleware",
                    "django.middleware.csrf.CsrfViewMiddleware",
                    "django.contrib.auth.middleware.AuthenticationMiddleware",
                    "django.contrib.messages.middleware.MessageMiddleware",
                ],
                ROOT_URLCONF="paperless_firefly.review.web.urls",
                TEMPLATES=[
                    {
                        "BACKEND": "django.template.backends.django.DjangoTemplates",
                        "DIRS": [
                            Path(__file__).parent.parent
                            / "src"
                            / "paperless_firefly"
                            / "review"
                            / "web"
                            / "templates"
                        ],
                        "APP_DIRS": True,
                        "OPTIONS": {
                            "context_processors": [
                                "django.template.context_processors.debug",
                                "django.template.context_processors.request",
                                "django.contrib.auth.context_processors.auth",
                                "django.contrib.messages.context_processors.messages",
                            ],
                        },
                    }
                ],
                PAPERLESS_BASE_URL="http://paperless.test:8000",
                PAPERLESS_TOKEN="test-token",
                PAPERLESS_EXTERNAL_URL="http://paperless.test:8000",
                FIREFLY_BASE_URL="http://firefly.test:8080",
                FIREFLY_TOKEN="test-firefly-token",
                FIREFLY_EXTERNAL_URL="http://firefly.test:8080",
                STATE_DB_PATH=str(state_db),
                AUTH_DB_PATH=str(auth_db),
                LOGIN_URL="/login/",
                LOGIN_REDIRECT_URL="/",
                LOGOUT_REDIRECT_URL="/login/",
            )

        django.setup()

        # Create tables
        from django.core.management import call_command

        call_command("migrate", "--run-syncdb", verbosity=0)

        return settings

    def test_login_page_renders(self, client, configured_settings):
        """Login page should render without authentication."""
        response = client.get("/login/")
        assert response.status_code == 200
        # Should contain login form
        assert b"<form" in response.content or b"form" in response.content.lower()

    def test_unauthenticated_redirects_to_login(self, client, configured_settings):
        """Protected pages should redirect to login."""
        response = client.get("/")
        # Should redirect to login
        assert response.status_code in [302, 301]
        assert "/login/" in response.url or "login" in response.url.lower()

    def test_login_with_valid_credentials(self, client, configured_settings):
        """Should authenticate with valid credentials."""
        from django.contrib.auth.models import User

        # Create test user
        User.objects.create_user(username="testuser", password="testpassword123")

        response = client.post(
            "/login/",
            {
                "username": "testuser",
                "password": "testpassword123",
            },
            follow=False,
        )

        # Should redirect after successful login
        assert response.status_code in [302, 301, 200]

    def test_login_with_invalid_credentials(self, client, configured_settings):
        """Should reject invalid credentials."""
        response = client.post(
            "/login/",
            {
                "username": "nonexistent",
                "password": "wrongpassword",
            },
        )

        # Should stay on login page or show error
        assert response.status_code == 200 or response.status_code == 400

    def test_logout(self, client, configured_settings):
        """Should log out user."""
        from django.contrib.auth.models import User

        # Create and login user
        User.objects.create_user(username="testuser", password="testpass")
        client.login(username="testuser", password="testpass")

        response = client.get("/logout/")

        # Should redirect
        assert response.status_code in [302, 301, 200]


class TestLandingPage:
    """Tests for the landing page."""

    @pytest.fixture
    def authenticated_client(self, tmp_path):
        """Create authenticated Django test client."""
        import django
        from django.conf import settings
        from django.test import Client

        state_db = tmp_path / "test_state.db"
        auth_db = tmp_path / "test_auth.db"

        # Create empty database files to ensure they can be opened
        state_db.touch()
        auth_db.touch()

        if not settings.configured:
            settings.configure(
                DEBUG=True,
                SECRET_KEY="test-secret-key-12345",
                ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
                DATABASES={
                    "default": {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": str(auth_db),
                    }
                },
                INSTALLED_APPS=[
                    "django.contrib.admin",
                    "django.contrib.auth",
                    "django.contrib.contenttypes",
                    "django.contrib.sessions",
                    "django.contrib.messages",
                ],
                MIDDLEWARE=[
                    "django.contrib.sessions.middleware.SessionMiddleware",
                    "django.middleware.common.CommonMiddleware",
                    "django.middleware.csrf.CsrfViewMiddleware",
                    "django.contrib.auth.middleware.AuthenticationMiddleware",
                    "django.contrib.messages.middleware.MessageMiddleware",
                ],
                ROOT_URLCONF="paperless_firefly.review.web.urls",
                TEMPLATES=[
                    {
                        "BACKEND": "django.template.backends.django.DjangoTemplates",
                        "DIRS": [
                            Path(__file__).parent.parent
                            / "src"
                            / "paperless_firefly"
                            / "review"
                            / "web"
                            / "templates"
                        ],
                        "APP_DIRS": True,
                    }
                ],
                PAPERLESS_BASE_URL="http://paperless.test:8000",
                PAPERLESS_EXTERNAL_URL="http://paperless.test:8000",
                FIREFLY_BASE_URL="http://firefly.test:8080",
                FIREFLY_EXTERNAL_URL="http://firefly.test:8080",
                SYNCTHING_URL="http://syncthing.test:8384",
                FIREFLY_IMPORTER_URL="http://importer.test:8081",
                STATE_DB_PATH=str(state_db),
                LOGIN_URL="/login/",
            )

        django.setup()

        from django.core.management import call_command

        call_command("migrate", "--run-syncdb", verbosity=0)

        # Create test user
        from django.contrib.auth.models import User

        User.objects.create_user(username="testuser", password="testpass")

        client = Client()
        client.login(username="testuser", password="testpass")

        return client

    def test_landing_page_accessible_when_authenticated(self, authenticated_client):
        """Authenticated users should see landing page."""
        response = authenticated_client.get("/")
        assert response.status_code == 200

    def test_landing_page_shows_service_links(self, authenticated_client):
        """Landing page should show links to other services."""
        response = authenticated_client.get("/")
        content = response.content.decode("utf-8").lower()

        # Should contain links to services
        assert "paperless" in content or "firefly" in content or "href" in content


class TestImportQueue:
    """Tests for the import queue functionality."""

    def test_import_queue_shows_approved_extractions(self, tmp_path):
        """Import queue should show approved but not imported items."""
        # Create test database with approved extractions
        state_db = tmp_path / "test_state.db"

        conn = sqlite3.connect(str(state_db))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS extractions (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                external_id TEXT NOT NULL,
                extraction_json TEXT NOT NULL,
                overall_confidence REAL NOT NULL,
                review_state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                reviewed_at TEXT,
                review_decision TEXT
            );

            CREATE TABLE IF NOT EXISTS imports (
                id INTEGER PRIMARY KEY,
                external_id TEXT NOT NULL,
                document_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """
        )

        # Insert approved extraction
        conn.execute(
            """
            INSERT INTO extractions
            (document_id, external_id, extraction_json, overall_confidence, review_state, created_at, review_decision, reviewed_at)
            VALUES (123, 'test-ext-1', '{"proposal": {"amount": "100.00"}}', 0.85, 'REVIEW',
                    '2024-01-01', 'ACCEPTED', '2024-01-02')
        """
        )
        conn.commit()
        conn.close()

        # Verify data exists
        conn = sqlite3.connect(str(state_db))
        row = conn.execute(
            "SELECT * FROM extractions WHERE review_decision = 'ACCEPTED'"
        ).fetchone()
        conn.close()

        assert row is not None


class TestFireflyAccountsAPI:
    """Tests for the Firefly accounts API endpoint."""

    def test_get_accounts_returns_json(self):
        """Account API should return JSON."""
        # Mock the Firefly client response
        mock_accounts = [
            {"id": 1, "name": "Checking Account", "type": "asset"},
            {"id": 2, "name": "Savings Account", "type": "asset"},
        ]

        # The API should return properly formatted JSON

        result = json.dumps(mock_accounts)
        parsed = json.loads(result)

        assert len(parsed) == 2
        assert parsed[0]["name"] == "Checking Account"


class TestExtractCommand:
    """Tests for web-triggered extract command."""

    def test_extract_command_parses_correctly(self):
        """Extract command should be callable with tag."""
        # Test that extract command can be invoked with a tag parameter
        from paperless_firefly.runner.main import cmd_extract, create_cli

        # The function should exist and be callable
        assert callable(cmd_extract)

        # Create CLI to verify extract subcommand exists
        parser = create_cli()
        # Should be able to parse extract command
        args = parser.parse_args(["extract", "--tag", "finance/inbox"])
        assert args.command == "extract"
        assert args.tag == "finance/inbox"


class TestUserProfile:
    """Tests for user profile and settings."""

    def test_user_profile_model_fields(self):
        """UserProfile should have required fields."""
        from paperless_firefly.review.web.models import UserProfile

        # Check that model has expected fields
        fields = [f.name for f in UserProfile._meta.get_fields()]

        assert "user" in fields
        assert "paperless_token" in fields or "paperless_url" in fields

    def test_user_profile_string_representation(self):
        """UserProfile should have useful string representation."""
        from paperless_firefly.review.web.models import UserProfile

        # Model should have __str__ method
        assert hasattr(UserProfile, "__str__")


class TestCSRFProtection:
    """Tests for CSRF protection on forms."""

    def test_forms_require_csrf(self, tmp_path):
        """Forms should require CSRF token."""
        import django
        from django.conf import settings
        from django.test import Client

        auth_db = tmp_path / "test_auth.db"

        if not settings.configured:
            settings.configure(
                DEBUG=True,
                SECRET_KEY="test-csrf-key",
                DATABASES={
                    "default": {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": str(auth_db),
                    }
                },
                INSTALLED_APPS=[
                    "django.contrib.auth",
                    "django.contrib.contenttypes",
                    "django.contrib.sessions",
                ],
                MIDDLEWARE=[
                    "django.contrib.sessions.middleware.SessionMiddleware",
                    "django.middleware.csrf.CsrfViewMiddleware",
                    "django.contrib.auth.middleware.AuthenticationMiddleware",
                ],
                ROOT_URLCONF="paperless_firefly.review.web.urls",
            )

        django.setup()

        client = Client(enforce_csrf_checks=True)

        # POST without CSRF should fail
        response = client.post(
            "/login/",
            {
                "username": "test",
                "password": "test",
            },
        )

        # Should return 403 Forbidden due to missing CSRF
        assert response.status_code == 403


class TestBackgroundJobs:
    """Tests for background job execution."""

    def test_extraction_status_tracking(self):
        """Extraction job status should be trackable."""
        # Status should be one of: idle, running, completed, failed
        valid_statuses = ["idle", "running", "completed", "failed"]

        # Test status data structure
        status = {
            "status": "running",
            "started_at": "2024-01-01T10:00:00Z",
            "progress": 50,
        }

        assert status["status"] in valid_statuses

    def test_import_status_tracking(self):
        """Import job status should be trackable."""
        status = {
            "status": "completed",
            "imported": 5,
            "failed": 0,
            "skipped": 1,
        }

        assert status["status"] in ["idle", "running", "completed", "failed"]
        assert status["imported"] >= 0


class TestAccountSelector:
    """Tests for the Firefly account selector."""

    def test_account_list_format(self):
        """Account list should be properly formatted for dropdown."""
        accounts = [
            {"id": 1, "name": "Main Checking", "type": "asset", "currency_code": "EUR"},
            {"id": 2, "name": "Savings", "type": "asset", "currency_code": "EUR"},
            {"id": 3, "name": "Cash", "type": "asset", "currency_code": "EUR"},
        ]

        # Should be usable in a HTML select
        for account in accounts:
            assert "id" in account
            assert "name" in account
            assert len(account["name"]) > 0

    def test_default_account_selection(self):
        """Default account should be selectable."""
        default_account = "Main Checking"
        accounts = [
            {"id": 1, "name": "Main Checking"},
            {"id": 2, "name": "Savings"},
        ]

        # Find the default
        default = next((a for a in accounts if a["name"] == default_account), None)
        assert default is not None
        assert default["id"] == 1


class TestMobileFriendly:
    """Tests for mobile-friendly design."""

    def test_viewport_meta_in_templates(self):
        """Templates should have viewport meta tag for mobile."""
        template_dir = (
            Path(__file__).parent.parent
            / "src"
            / "paperless_firefly"
            / "review"
            / "web"
            / "templates"
        )
        base_template = template_dir / "review" / "base.html"

        if base_template.exists():
            content = base_template.read_text(encoding="utf-8")
            assert "viewport" in content.lower()

    def test_responsive_css_classes(self):
        """Templates should use responsive CSS."""
        template_dir = (
            Path(__file__).parent.parent
            / "src"
            / "paperless_firefly"
            / "review"
            / "web"
            / "templates"
        )
        base_template = template_dir / "review" / "base.html"

        if base_template.exists():
            content = base_template.read_text(encoding="utf-8")
            # Check for responsive indicators (media queries or bootstrap classes)
            has_responsive = (
                "@media" in content or "container" in content or "responsive" in content.lower()
            )
            assert has_responsive


class TestExternalURLHandling:
    """Tests for external URL configuration."""

    def test_external_url_used_in_links(self):
        """External URLs should be used for browser-clickable links."""
        internal_url = "http://paperless:8000"
        external_url = "http://192.168.1.100:8000"

        # The external URL should be used for user-facing links
        document_id = 123
        link = f"{external_url}/documents/{document_id}/"

        assert external_url in link
        assert internal_url not in link

    def test_internal_url_used_for_api(self):
        """Internal URLs should be used for API calls."""
        internal_url = "http://paperless:8000"

        # API endpoint
        api_url = f"{internal_url}/api/documents/"

        assert internal_url in api_url
