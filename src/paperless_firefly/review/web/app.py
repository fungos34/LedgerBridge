"""
Django application initialization.
"""

import os


def get_wsgi_application(config_path: str = None, state_db_path: str = None):
    """
    Get the Django WSGI application configured with our settings.

    Args:
        config_path: Path to config.yaml (optional)
        state_db_path: Path to state.db (optional, overrides config)
    """
    # Set up Django settings
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paperless_firefly.review.web.settings")

    # Load our config if provided
    # Note: os.environ requires strings, so convert Path objects
    if config_path:
        from ...config import load_config

        config = load_config(config_path)

        os.environ["PAPERLESS_URL"] = config.paperless.base_url
        os.environ["PAPERLESS_TOKEN"] = config.paperless.token
        os.environ["STATE_DB_PATH"] = str(state_db_path or config.state_db_path)
    elif state_db_path:
        os.environ["STATE_DB_PATH"] = str(state_db_path)

    from django.core.wsgi import get_wsgi_application as django_wsgi

    return django_wsgi()


def run_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    config_path: str = None,
    state_db_path: str = None,
    paperless_url: str = None,
    paperless_token: str = None,
):
    """
    Run the Django development server.

    Args:
        host: Host to bind to
        port: Port to listen on
        config_path: Path to config.yaml
        state_db_path: Path to state.db (overrides config)
        paperless_url: Paperless base URL (overrides config)
        paperless_token: Paperless API token (overrides config)
    """

    # Set up Django settings
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paperless_firefly.review.web.settings")

    # Set environment variables from direct parameters or config
    # Note: os.environ requires strings, so convert Path objects
    if paperless_url:
        os.environ["PAPERLESS_URL"] = paperless_url
    if paperless_token:
        os.environ["PAPERLESS_TOKEN"] = paperless_token
    if state_db_path:
        os.environ["STATE_DB_PATH"] = str(state_db_path)

    # Load config file if provided and fill in missing values
    if config_path:
        from ...config import load_config

        config = load_config(config_path)

        if "PAPERLESS_URL" not in os.environ:
            os.environ["PAPERLESS_URL"] = config.paperless.base_url
        if "PAPERLESS_TOKEN" not in os.environ:
            os.environ["PAPERLESS_TOKEN"] = config.paperless.token
        if "FIREFLY_URL" not in os.environ:
            os.environ["FIREFLY_URL"] = config.firefly.base_url
        if "STATE_DB_PATH" not in os.environ:
            os.environ["STATE_DB_PATH"] = str(config.state_db_path)

    # Initialize Django
    import django

    django.setup()

    # Run the development server
    from django.core.management import execute_from_command_line

    print(f"\n🌐 Starting review web interface at http://{host}:{port}/")
    print(f"📄 Paperless URL: {os.environ.get('PAPERLESS_URL', 'not configured')}")
    print(f"💾 State DB: {os.environ.get('STATE_DB_PATH', 'state.db')}")
    print("\nPress Ctrl+C to stop.\n")

    execute_from_command_line(
        [
            "manage.py",
            "runserver",
            f"{host}:{port}",
            "--noreload",  # Disable auto-reload for simpler operation
        ]
    )
