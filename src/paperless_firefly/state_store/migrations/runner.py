"""
Migration runner for versioned database schema changes.

Migrations are named with format: {version}_{name}.py
E.g., 001_firefly_cache.py, 002_match_proposals.py

Each migration must define:
- VERSION: int
- NAME: str
- upgrade(conn: Connection) -> None
- downgrade(conn: Connection) -> None  # May raise NotImplementedError
"""

import importlib
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Migration:
    """Represents a database migration."""

    version: int
    name: str
    upgrade: Callable[[sqlite3.Connection], None]
    downgrade: Callable[[sqlite3.Connection], None] | None


def get_all_migrations() -> list[Migration]:
    """
    Load all migrations from the migrations directory.

    Returns migrations sorted by version.
    """
    migrations = []
    migrations_dir = Path(__file__).parent

    for py_file in sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.py")):
        module_name = py_file.stem
        full_module = f"paperless_firefly.state_store.migrations.{module_name}"

        try:
            module = importlib.import_module(full_module)
            migrations.append(
                Migration(
                    version=module.VERSION,
                    name=module.NAME,
                    upgrade=module.upgrade,
                    downgrade=getattr(module, "downgrade", None),
                )
            )
        except (ImportError, AttributeError) as e:
            logger.warning(f"Failed to load migration {module_name}: {e}")

    return sorted(migrations, key=lambda m: m.version)


class MigrationRunner:
    """
    Runs database migrations in order.

    Tracks applied migrations in a `migrations` table.
    """

    def __init__(self, conn: sqlite3.Connection):
        """Initialize with a database connection."""
        self.conn = conn
        self._ensure_migrations_table()

    def _ensure_migrations_table(self) -> None:
        """Create migrations tracking table if it doesn't exist."""
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
        """
        )
        self.conn.commit()

    def get_applied_versions(self) -> set[int]:
        """Get set of applied migration versions."""
        cursor = self.conn.execute("SELECT version FROM migrations ORDER BY version")
        return {row[0] for row in cursor.fetchall()}

    def get_current_version(self) -> int:
        """Get the highest applied migration version."""
        cursor = self.conn.execute("SELECT MAX(version) FROM migrations")
        result = cursor.fetchone()[0]
        return result if result is not None else 0

    def apply_migration(self, migration: Migration) -> None:
        """Apply a single migration."""
        logger.info(f"Applying migration {migration.version}: {migration.name}")

        try:
            migration.upgrade(self.conn)

            # Record migration
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self.conn.execute(
                "INSERT INTO migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, now),
            )
            self.conn.commit()
            logger.info(f"Migration {migration.version} applied successfully")

        except Exception as e:
            self.conn.rollback()
            logger.error(f"Migration {migration.version} failed: {e}")
            raise

    def rollback_migration(self, migration: Migration) -> None:
        """Rollback a single migration."""
        if migration.downgrade is None:
            raise NotImplementedError(
                f"Migration {migration.version} ({migration.name}) does not support rollback"
            )

        logger.info(f"Rolling back migration {migration.version}: {migration.name}")

        try:
            migration.downgrade(self.conn)

            # Remove migration record
            self.conn.execute("DELETE FROM migrations WHERE version = ?", (migration.version,))
            self.conn.commit()
            logger.info(f"Migration {migration.version} rolled back successfully")

        except Exception as e:
            self.conn.rollback()
            logger.error(f"Migration {migration.version} rollback failed: {e}")
            raise

    def run_pending(self) -> list[int]:
        """
        Run all pending migrations.

        Returns list of applied migration versions.
        """
        applied = self.get_applied_versions()
        all_migrations = get_all_migrations()
        pending = [m for m in all_migrations if m.version not in applied]

        applied_versions = []
        for migration in pending:
            self.apply_migration(migration)
            applied_versions.append(migration.version)

        if applied_versions:
            logger.info(f"Applied {len(applied_versions)} migrations: {applied_versions}")
        else:
            logger.info("No pending migrations")

        return applied_versions

    def migrate_to(self, target_version: int) -> None:
        """
        Migrate to a specific version (up or down).

        Args:
            target_version: Target schema version
        """
        current = self.get_current_version()
        all_migrations = get_all_migrations()
        migration_map = {m.version: m for m in all_migrations}

        if target_version > current:
            # Upgrade
            for version in range(current + 1, target_version + 1):
                if version in migration_map:
                    self.apply_migration(migration_map[version])

        elif target_version < current:
            # Downgrade
            for version in range(current, target_version, -1):
                if version in migration_map:
                    self.rollback_migration(migration_map[version])
