"""Tests for CLI commands.

These tests verify that all CLI commands are properly registered and callable.
"""

from paperless_firefly.runner.main import create_cli


class TestCLICommandRegistry:
    """Tests for CLI command registration."""

    def test_cli_has_reconcile_command(self):
        """CLI should have reconcile command registered."""
        parser = create_cli()
        # Parse without --help to avoid SystemExit
        args = parser.parse_args(["reconcile"])
        assert args.command == "reconcile"

    def test_reconcile_command_has_sync_option(self):
        """Reconcile command should accept --sync/--no-sync."""
        parser = create_cli()

        # Default should be sync=True
        args = parser.parse_args(["reconcile"])
        assert args.sync is True

        # --no-sync should set sync=False
        args = parser.parse_args(["reconcile", "--no-sync"])
        assert args.sync is False

    def test_reconcile_command_has_match_option(self):
        """Reconcile command should accept --match/--no-match."""
        parser = create_cli()

        # Default should be match=True
        args = parser.parse_args(["reconcile"])
        assert args.match is True

        # --no-match should set match=False
        args = parser.parse_args(["reconcile", "--no-match"])
        assert args.match is False

    def test_reconcile_command_has_full_sync_option(self):
        """Reconcile command should accept --full-sync."""
        parser = create_cli()

        args = parser.parse_args(["reconcile", "--full-sync"])
        assert args.full_sync is True

    def test_reconcile_command_has_dry_run_option(self):
        """Reconcile command should accept --dry-run."""
        parser = create_cli()

        args = parser.parse_args(["reconcile", "--dry-run"])
        assert args.dry_run is True

    def test_all_commands_registered(self):
        """Verify all expected commands are registered."""
        parser = create_cli()

        # Get all subcommand names
        subparsers_action = None
        for action in parser._actions:
            if action.dest == "command":
                subparsers_action = action
                break

        assert subparsers_action is not None

        commands = list(subparsers_action.choices.keys())

        # Core commands
        assert "scan" in commands
        assert "extract" in commands
        assert "review" in commands
        assert "import" in commands
        assert "pipeline" in commands
        assert "status" in commands

        # Spark v1.0 command
        assert "reconcile" in commands


class TestReconcileCommandArguments:
    """Tests for reconcile command argument combinations."""

    def test_reconcile_default_arguments(self):
        """Reconcile should have correct defaults."""
        parser = create_cli()
        args = parser.parse_args(["reconcile"])

        assert args.command == "reconcile"
        assert args.sync is True
        assert args.match is True
        assert args.full_sync is False
        assert args.dry_run is False

    def test_reconcile_all_options_together(self):
        """Reconcile should accept all options together."""
        parser = create_cli()
        args = parser.parse_args([
            "reconcile",
            "--no-sync",
            "--no-match",
            "--full-sync",
            "--dry-run",
        ])

        assert args.sync is False
        assert args.match is False
        assert args.full_sync is True
        assert args.dry_run is True

    def test_reconcile_typical_usage_sync_only(self):
        """Typical usage: sync and match (defaults)."""
        parser = create_cli()
        args = parser.parse_args(["reconcile"])

        assert args.sync is True
        assert args.match is True

    def test_reconcile_dry_run_preserves_defaults(self):
        """Dry run should preserve sync/match defaults."""
        parser = create_cli()
        args = parser.parse_args(["reconcile", "--dry-run"])

        assert args.sync is True
        assert args.match is True
        assert args.dry_run is True


class TestHelpOutput:
    """Tests for CLI help output."""

    def test_reconcile_in_help(self):
        """Reconcile should appear in main help output."""
        parser = create_cli()

        # Format help text
        help_text = parser.format_help()

        assert "reconcile" in help_text
        assert (
            "bank reconciliation" in help_text.lower()
            or "reconciliation" in help_text.lower()
        )

    def test_reconcile_subcommand_help(self):
        """Reconcile subcommand should have descriptive help."""
        parser = create_cli()

        # Get the reconcile subparser
        subparsers_action = None
        for action in parser._actions:
            if action.dest == "command":
                subparsers_action = action
                break

        assert "reconcile" in subparsers_action.choices

        reconcile_parser = subparsers_action.choices["reconcile"]
        help_text = reconcile_parser.format_help()

        # Check that options are documented
        assert "--sync" in help_text or "sync" in help_text
        assert "--match" in help_text or "match" in help_text
        assert "--full-sync" in help_text
        assert "--dry-run" in help_text


class TestCmdReconcileFunction:
    """Tests for cmd_reconcile function existence and signature."""

    def test_cmd_reconcile_exists(self):
        """cmd_reconcile function should exist."""
        from paperless_firefly.runner.main import cmd_reconcile

        assert callable(cmd_reconcile)

    def test_cmd_reconcile_signature(self):
        """cmd_reconcile should have expected parameters."""
        import inspect

        from paperless_firefly.runner.main import cmd_reconcile

        sig = inspect.signature(cmd_reconcile)
        params = list(sig.parameters.keys())

        assert "config" in params
        assert "sync" in params
        assert "match" in params
        assert "full_sync" in params
        assert "dry_run" in params

    def test_cmd_reconcile_returns_int(self):
        """cmd_reconcile should be annotated to return int."""
        import inspect

        from paperless_firefly.runner.main import cmd_reconcile

        sig = inspect.signature(cmd_reconcile)
        assert sig.return_annotation is int
