"""Firefly III transaction synchronization service.

This service syncs Firefly transactions to the local cache for matching purposes.
It implements the Firefly introspection capabilities as specified in Spark v1.0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from paperless_firefly.schemas.linkage import is_linked_to_spark

if TYPE_CHECKING:
    from paperless_firefly.config import Config
    from paperless_firefly.firefly_client import FireflyClient
    from paperless_firefly.state_store import StateStore

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a Firefly sync operation."""

    transactions_synced: int
    transactions_skipped: int  # Already linked to Spark
    categories_synced: int
    duration_ms: int
    errors: list[str]

    @property
    def success(self) -> bool:
        """Return True if sync completed without errors."""
        return len(self.errors) == 0


class FireflySyncService:
    """Service for synchronizing Firefly III transactions to local cache.

    This service pulls transactions from Firefly III, filters out those
    already linked to Spark/Paperless, and caches unlinked ones for
    the matching engine.
    """

    def __init__(
        self,
        firefly_client: FireflyClient,
        state_store: StateStore,
        config: Config,
    ) -> None:
        """Initialize the sync service.

        Args:
            firefly_client: Client for Firefly III API.
            state_store: State store for caching transactions.
            config: Application configuration.
        """
        self.firefly = firefly_client
        self.store = state_store
        self.config = config
        self._categories: dict[int, str] = {}

    def sync_transactions(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        full_sync: bool = False,
    ) -> SyncResult:
        """Sync Firefly transactions to local cache.

        Args:
            start_date: Start of date range to sync (inclusive).
            end_date: End of date range to sync (inclusive).
            full_sync: If True, clear cache and sync all transactions.

        Returns:
            SyncResult with statistics about the sync operation.
        """
        start_time = datetime.now()
        errors: list[str] = []
        synced = 0
        skipped = 0

        try:
            # Sync categories first
            categories_synced = self._sync_categories()
        except Exception as e:
            logger.exception("Failed to sync categories")
            errors.append(f"Category sync failed: {e}")
            categories_synced = 0

        if full_sync:
            logger.info("Performing full sync - clearing existing cache")
            self.store.clear_firefly_cache()

        try:
            # Use unlinked transactions endpoint for efficiency
            if not full_sync and start_date is None and end_date is None:
                # Fast path: only get unlinked transactions
                transactions = self.firefly.get_unlinked_transactions()
            else:
                # Full path: get all and filter locally
                transactions = self.firefly.list_transactions(
                    start_date=start_date, end_date=end_date
                )

            for tx in transactions:
                try:
                    # Double-check linkage status
                    if is_linked_to_spark(
                        external_id=tx.external_id,
                        internal_reference=tx.internal_reference,
                        notes=tx.notes,
                    ):
                        skipped += 1
                        continue

                    # Cache the transaction
                    self.store.upsert_firefly_cache(
                        firefly_id=tx.id,
                        type_=tx.type,
                        date=tx.date.isoformat() if isinstance(tx.date, datetime) else tx.date,
                        amount=str(tx.amount),
                        description=tx.description,
                        external_id=tx.external_id,
                        internal_reference=tx.internal_reference,
                        source_account=tx.source_name,
                        destination_account=tx.destination_name,
                        notes=tx.notes,
                        category_name=tx.category_name,
                        tags=tx.tags,
                    )
                    synced += 1

                except Exception as e:
                    logger.warning("Failed to cache transaction %s: %s", tx.id, e)
                    errors.append(f"Transaction {tx.id}: {e}")

        except Exception as e:
            logger.exception("Failed to list transactions")
            errors.append(f"Transaction listing failed: {e}")

        duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

        result = SyncResult(
            transactions_synced=synced,
            transactions_skipped=skipped,
            categories_synced=categories_synced,
            duration_ms=duration_ms,
            errors=errors,
        )

        logger.info(
            "Sync completed: %d synced, %d skipped (linked), %d categories, %d errors in %dms",
            result.transactions_synced,
            result.transactions_skipped,
            result.categories_synced,
            len(result.errors),
            result.duration_ms,
        )

        return result

    def _sync_categories(self) -> int:
        """Sync Firefly categories to local cache.

        Returns:
            Number of categories synced.
        """
        categories = self.firefly.list_categories()
        self._categories = {cat.id: cat.name for cat in categories}
        logger.debug("Synced %d categories", len(categories))
        return len(categories)

    def get_category_name(self, category_id: int) -> str | None:
        """Get category name by ID.

        Args:
            category_id: Firefly category ID.

        Returns:
            Category name or None if not found.
        """
        return self._categories.get(category_id)

    def get_unmatched_transactions(self) -> list[dict]:
        """Get cached transactions that haven't been matched yet.

        Returns:
            List of unmatched transaction records.
        """
        return self.store.get_unmatched_firefly_transactions()

    def mark_matched(
        self,
        firefly_id: int,
        document_id: int,
        confidence: float,
    ) -> None:
        """Mark a transaction as matched to a document.

        Args:
            firefly_id: Firefly transaction ID.
            document_id: Paperless document ID.
            confidence: Match confidence score (0-1).
        """
        self.store.update_firefly_match_status(
            firefly_id=firefly_id,
            status="MATCHED",
            document_id=document_id,
            confidence=confidence,
        )

    def get_sync_stats(self) -> dict:
        """Get synchronization statistics.

        Returns:
            Dict with cache statistics.
        """
        unmatched = self.store.get_unmatched_firefly_transactions()
        return {
            "cached_unmatched": len(unmatched),
            "categories_loaded": len(self._categories),
        }
