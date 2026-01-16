"""
CLI main entry point.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from ..config import Config, load_config
from ..extractors import ExtractorRouter
from ..firefly_client import FireflyClient
from ..paperless_client import PaperlessClient
from ..schemas.dedupe import compute_file_hash
from ..schemas.firefly_payload import build_firefly_payload_with_splits
from ..state_store import StateStore

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def create_cli() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="paperless-firefly",
        description="Extract finance data from Paperless and import to Firefly III",
    )

    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Find candidate documents in Paperless")
    scan_parser.add_argument(
        "--tag",
        type=str,
        default="finance/inbox",
        help="Tag to filter documents (default: finance/inbox)",
    )
    scan_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum documents to scan (default: 100)",
    )

    # extract command
    extract_parser = subparsers.add_parser("extract", help="Extract finance data from documents")
    extract_parser.add_argument(
        "--doc-id",
        type=int,
        help="Process specific document ID",
    )
    extract_parser.add_argument(
        "--tag",
        type=str,
        default="finance/inbox",
        help="Tag to filter documents (default: finance/inbox)",
    )
    extract_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum documents to process (default: 10)",
    )

    # review command
    review_parser = subparsers.add_parser(
        "review", help="Interactive review of extractions via web UI"
    )
    review_parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind the web server (default: 127.0.0.1)",
    )
    review_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for the web server (default: 8080)",
    )

    # import command
    import_parser = subparsers.add_parser(
        "import", help="Import approved transactions to Firefly III"
    )
    import_parser.add_argument(
        "--auto-only",
        action="store_true",
        help="Only import AUTO confidence transactions",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without actually importing",
    )

    # pipeline command
    pipeline_parser = subparsers.add_parser(
        "pipeline", help="Run full pipeline (scan → extract → review → import)"
    )
    pipeline_parser.add_argument(
        "--tag",
        type=str,
        default="finance/inbox",
        help="Tag to filter documents (default: finance/inbox)",
    )
    pipeline_parser.add_argument(
        "--auto-only",
        action="store_true",
        help="Skip review, only import AUTO confidence transactions",
    )
    pipeline_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum documents to process (default: 10)",
    )

    # status command
    subparsers.add_parser("status", help="Show pipeline status and statistics")

    # reconcile command (Spark v1.0)
    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help="Run bank reconciliation (sync Firefly transactions, match documents)",
    )
    reconcile_parser.add_argument(
        "--sync/--no-sync",
        dest="sync",
        action="store_true",
        default=True,
        help="Sync Firefly transactions to local cache (default: enabled)",
    )
    reconcile_parser.add_argument(
        "--no-sync",
        dest="sync",
        action="store_false",
        help="Skip syncing, use cached transactions only",
    )
    reconcile_parser.add_argument(
        "--match/--no-match",
        dest="match",
        action="store_true",
        default=True,
        help="Run matching engine to find matches (default: enabled)",
    )
    reconcile_parser.add_argument(
        "--no-match",
        dest="match",
        action="store_false",
        help="Skip matching, only sync transactions",
    )
    reconcile_parser.add_argument(
        "--full-sync",
        action="store_true",
        help="Clear cache and sync all transactions (not just recent)",
    )
    reconcile_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    return parser


def cmd_scan(config: Config, tag: str, limit: int) -> int:
    """Scan for candidate documents."""
    print(f"🔍 Scanning for documents with tag '{tag}'...")

    client = PaperlessClient(
        base_url=config.paperless.base_url,
        token=config.paperless.token,
    )

    if not client.test_connection():
        print("❌ Failed to connect to Paperless")
        return 1

    count = 0
    for doc in client.list_documents(tags=[tag]):
        if count >= limit:
            break
        print(f"  📄 [{doc.id}] {doc.title}")
        count += 1

    print(f"\n✓ Found {count} document(s)")
    return 0


def cmd_extract(
    config: Config, doc_id: int | None, tag: str, limit: int, user_id: int | None = None
) -> int:
    """Extract finance data from documents.

    Args:
        config: Configuration object.
        doc_id: Specific document ID to extract (optional).
        tag: Tag to filter documents by.
        limit: Maximum number of documents to process.
        user_id: Owner user ID for the extractions (None = shared/legacy).
    """
    print("📊 Extracting finance data...")

    paperless = PaperlessClient(
        base_url=config.paperless.base_url,
        token=config.paperless.token,
    )

    store = StateStore(config.state_db_path)
    router = ExtractorRouter()

    # Get documents to process
    if doc_id:
        docs = [paperless.get_document(doc_id)]
    else:
        docs = list(paperless.list_documents(tags=[tag]))[:limit]

    if not docs:
        print("No documents to process")
        return 0

    extracted = 0
    skipped = 0

    for doc in docs:
        # Check if already processed
        existing = store.get_extraction_by_document(doc.id)
        if existing:
            print(f"  ⏭ [{doc.id}] Already extracted (external_id: {existing.external_id})")
            skipped += 1
            continue

        print(f"  📄 [{doc.id}] {doc.title}")

        try:
            # Download original file
            file_bytes, filename = paperless.download_original(doc.id)
            source_hash = compute_file_hash(file_bytes)

            # Extract
            extraction = router.extract(
                document=doc,
                file_bytes=file_bytes,
                source_hash=source_hash,
                paperless_base_url=config.paperless.base_url,
                default_source_account=config.firefly.default_source_account,
            )

            # Save to store (with user_id for ownership)
            store.upsert_document(
                document_id=doc.id,
                source_hash=source_hash,
                title=doc.title,
                document_type=doc.document_type,
                correspondent=doc.correspondent,
                tags=doc.tags,
                user_id=user_id,
            )

            store.save_extraction(
                document_id=doc.id,
                external_id=extraction.proposal.external_id,
                extraction_json=json.dumps(extraction.to_dict()),
                overall_confidence=extraction.confidence.overall,
                review_state=extraction.confidence.review_state.value,
                user_id=user_id,
            )

            conf = extraction.confidence
            print(f"     → Amount: {extraction.proposal.amount} {extraction.proposal.currency}")
            print(f"     → Date: {extraction.proposal.date}")
            print(f"     → Confidence: {conf.overall:.0%} ({conf.review_state.value})")

            extracted += 1

        except Exception as e:
            logger.exception(f"Failed to extract doc {doc.id}")
            print(f"     ❌ Error: {e}")

    print(f"\n✓ Extracted: {extracted}, Skipped: {skipped}")
    return 0


def cmd_review(config: Config, host: str = "127.0.0.1", port: int = 8080) -> int:
    """Start web-based review interface."""
    from ..review.web.app import run_server

    print("🌐 Starting review web interface...")

    try:
        run_server(
            host=host,
            port=port,
            state_db_path=config.state_db_path,
            paperless_url=config.paperless.base_url,
            paperless_token=config.paperless.token,
        )
    except KeyboardInterrupt:
        print("\n✓ Review server stopped")

    return 0


def cmd_import(
    config: Config,
    auto_only: bool,
    dry_run: bool,
    source_account_override: str | None = None,
) -> int:
    """Import transactions to Firefly III.

    Args:
        config: Application configuration
        auto_only: Only import AUTO-confidence transactions
        dry_run: Don't actually import, just show what would be done
        source_account_override: Override the default source account from config
    """
    print("📤 Importing transactions to Firefly III...")

    store = StateStore(config.state_db_path)
    firefly = FireflyClient(
        base_url=config.firefly.base_url,
        token=config.firefly.token,
    )

    # Use override if provided, otherwise use config
    default_source_account = source_account_override or config.firefly.default_source_account

    if not firefly.test_connection():
        print("❌ Failed to connect to Firefly III")
        return 1

    # Get approved extractions
    from ..state_store.sqlite_store import ImportStatus

    # Query extractions ready for import
    conn = store._get_connection()
    try:
        if auto_only:
            # Only AUTO confidence, no review required
            rows = conn.execute(
                """
                SELECT e.*, l.firefly_id as linked_firefly_id, l.link_type
                FROM extractions e
                LEFT JOIN imports i ON e.external_id = i.external_id
                LEFT JOIN linkage l ON e.id = l.extraction_id
                WHERE e.review_state = 'AUTO'
                AND (i.id IS NULL OR i.status = 'FAILED')
            """
            ).fetchall()
        else:
            # AUTO + reviewed/accepted/orphan_confirmed (includes failed imports for retry)
            rows = conn.execute(
                """
                SELECT e.*, l.firefly_id as linked_firefly_id, l.link_type
                FROM extractions e
                LEFT JOIN imports i ON e.external_id = i.external_id
                LEFT JOIN linkage l ON e.id = l.extraction_id
                WHERE (i.id IS NULL OR i.status = 'FAILED')
                AND (e.review_state = 'AUTO' OR e.review_decision IN ('ACCEPTED', 'EDITED', 'ORPHAN_CONFIRMED'))
            """
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No transactions ready for import")
        return 0

    imported = 0
    failed = 0

    for row in rows:
        external_id = row["external_id"]
        document_id = row["document_id"]

        logger.info(f"Processing extraction: document_id={document_id}, external_id={external_id}")

        try:
            extraction = json.loads(row["extraction_json"])
            from ..schemas.finance_extraction import FinanceExtraction

            ext = FinanceExtraction.from_dict(extraction)
            logger.debug(f"Parsed extraction: {ext.proposal.description}")

            # Check if already imported (safety check) - includes failed imports
            existing_import = store.get_import_by_external_id(external_id)
            if existing_import:
                if existing_import.status == ImportStatus.IMPORTED.value:
                    logger.info(f"[{document_id}] Already imported, skipping")
                    print(f"  ⏭ [{ext.paperless_document_id}] Already imported")
                    continue
                elif existing_import.status == ImportStatus.FAILED.value:
                    # Reset failed imports so they can be retried
                    logger.info(f"[{document_id}] Retrying previously failed import")
                    store.delete_import(external_id)

            # Check if linked to existing Firefly transaction
            # Note: sqlite3.Row supports bracket notation but not .get() method
            linked_firefly_id = row["linked_firefly_id"] if row["linked_firefly_id"] else None
            link_type = row["link_type"] if row["link_type"] else None

            # Build Firefly payload (handles splits automatically)
            logger.debug(f"Building payload with source_account={default_source_account}")
            payload = build_firefly_payload_with_splits(
                ext,
                default_source_account=default_source_account,
                paperless_external_url=config.paperless.base_url,
            )
            logger.debug(f"Built payload: {payload.to_json()}")

            print(f"  📤 [{ext.paperless_document_id}] {ext.proposal.description}")
            print(f"     → {ext.proposal.amount} {ext.proposal.currency} on {ext.proposal.date}")

            if dry_run:
                if linked_firefly_id:
                    print(
                        f"     → [DRY RUN] Would UPDATE existing Firefly transaction {linked_firefly_id}"
                    )
                else:
                    print("     → [DRY RUN] Would CREATE new transaction")
                continue

            # Create import record BEFORE sending to Firefly
            store.create_import(
                external_id=external_id,
                document_id=ext.paperless_document_id,
                payload_json=payload.to_json(),
                status=ImportStatus.PENDING,
            )
            logger.debug(f"Created PENDING import record for {external_id}")

            # Handle linked vs orphan vs unlinked transactions
            if linked_firefly_id and link_type == "LINKED":
                # UPDATE existing Firefly transaction instead of creating new one
                # This prevents duplicates when importing linked documents
                logger.info(
                    f"Updating existing Firefly transaction {linked_firefly_id} with document data"
                )
                print(f"     → Updating linked Firefly transaction {linked_firefly_id}")

                try:
                    success = firefly.update_transaction(linked_firefly_id, payload)
                    if success:
                        store.update_import_success(external_id, linked_firefly_id)
                        logger.info(
                            f"[{document_id}] Update successful, firefly_id={linked_firefly_id}"
                        )
                        print(f"     ✓ Updated (Firefly ID: {linked_firefly_id})")
                        imported += 1
                    else:
                        store.update_import_failed(
                            external_id, "Failed to update Firefly transaction"
                        )
                        logger.warning(f"[{document_id}] Update failed")
                        print("     ⚠ Update failed")
                        failed += 1
                except Exception as update_err:
                    # If update fails, record the error
                    error_msg = str(update_err)
                    store.update_import_failed(external_id, f"Update failed: {error_msg}")
                    logger.warning(f"[{document_id}] Update failed: {error_msg}")
                    print(f"     ⚠ Update failed: {error_msg}")
                    failed += 1
            else:
                # CREATE new transaction (orphan or unlinked)
                logger.info(f"Sending new transaction to Firefly: {ext.proposal.description}")
                firefly_id = firefly.create_transaction(payload, skip_duplicates=True)

                if firefly_id:
                    store.update_import_success(external_id, firefly_id)
                    logger.info(f"[{document_id}] Import successful, firefly_id={firefly_id}")
                    print(f"     ✓ Imported (Firefly ID: {firefly_id})")
                    imported += 1
                else:
                    store.update_import_failed(external_id, "Duplicate detected by Firefly")
                    logger.warning(f"[{document_id}] Duplicate detected")
                    print("     ⚠ Skipped (duplicate)")
                    failed += 1

        except Exception as e:
            error_msg = str(e)
            logger.exception(f"Import failed for document_id={document_id}: {error_msg}")
            print(f"     ❌ Error: {error_msg}")

            # Always record the failure, creating import record if needed
            if not dry_run and external_id:
                try:
                    store.create_or_update_failed_import(
                        external_id=external_id,
                        document_id=document_id,
                        error_message=error_msg,
                    )
                    logger.debug(f"Recorded failed import for {external_id}")
                except Exception as store_error:
                    logger.error(f"Failed to record import failure: {store_error}")
            failed += 1

    logger.info(f"Import complete: imported={imported}, failed={failed}")
    print(f"\n✓ Imported: {imported}, Failed: {failed}")
    return failed  # Return actual failure count


def cmd_pipeline(config: Config, tag: str, auto_only: bool, limit: int) -> int:
    """Run full pipeline."""
    print("🚀 Running full pipeline...\n")

    # Step 1: Extract
    print("Step 1/3: Extracting...")
    result = cmd_extract(config, None, tag, limit)
    if result != 0:
        return result

    # Step 2: Review (unless auto-only)
    if not auto_only:
        print("\nStep 2/3: Review...")
        print("  ℹ️  To review extractions, run: paperless-firefly review")
        print("  ℹ️  This will start a web interface at http://127.0.0.1:8080/")
        print("  ℹ️  Skipping interactive review in pipeline mode.")
        print("  ℹ️  Only AUTO-confidence transactions will be imported.")
        auto_only = True  # Force auto-only since we can't do interactive review in pipeline
    else:
        print("\nStep 2/3: Review (skipped - auto-only mode)")

    # Step 3: Import
    print("\nStep 3/3: Importing...")
    result = cmd_import(config, auto_only, dry_run=False)

    return result


def cmd_reconcile(
    config: Config,
    sync: bool = True,
    match: bool = True,
    full_sync: bool = False,
    dry_run: bool = False,
) -> int:
    """Run bank reconciliation pipeline (Spark v1.0).

    Args:
        config: Application configuration.
        sync: If True, sync Firefly transactions to local cache.
        match: If True, run matching engine.
        full_sync: If True, clear cache and sync all transactions.
        dry_run: If True, show what would be done without making changes.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    from ..firefly_client import FireflyClient
    from ..services.reconciliation import ReconciliationService

    print("🔄 Starting bank reconciliation (Spark v1.0)...")

    if dry_run:
        print("  ℹ️  DRY RUN mode - no changes will be made")

    # Validate connectivity before starting
    firefly = FireflyClient(
        base_url=config.firefly.base_url,
        token=config.firefly.token,
    )

    print(f"  → Connecting to Firefly: {config.firefly.base_url}")
    if not firefly.test_connection():
        print("❌ Failed to connect to Firefly III")
        print("   Check FIREFLY_URL and FIREFLY_TOKEN")
        return 1
    print("  ✓ Firefly connection OK")

    # Initialize state store
    store = StateStore(config.state_db_path)

    # Create reconciliation service
    service = ReconciliationService(
        firefly_client=firefly,
        state_store=store,
        config=config,
    )

    # Print what we're going to do
    print()
    print("Configuration:")
    print(f"  Sync transactions: {'yes' if sync else 'no'}")
    print(f"  Full sync (clear cache): {'yes' if full_sync else 'no'}")
    print(f"  Run matching: {'yes' if match else 'no'}")
    print(f"  Auto-link threshold: {config.reconciliation.auto_match_threshold:.0%}")
    print()

    # Run reconciliation
    if sync or match:
        result = service.run_reconciliation(
            full_sync=full_sync,
            dry_run=dry_run,
        )

        # Print results
        print()
        print("📊 Reconciliation Results")
        print("=" * 40)
        print(f"  Status:              {result.state.value}")
        print(f"  Transactions synced: {result.transactions_synced}")
        print(f"  Transactions cached: {result.transactions_skipped}")
        print(f"  Proposals created:   {result.proposals_created}")
        print(f"  Proposals existing:  {result.proposals_existing}")
        print(f"  Auto-linked:         {result.auto_linked}")
        print(f"  Duration:            {result.duration_ms}ms")
        print()

        if result.errors:
            print("⚠️  Errors encountered:")
            for error in result.errors:
                print(f"   - {error}")

        if result.success:
            print("✓ Reconciliation completed successfully")
            return 0
        else:
            print("❌ Reconciliation failed")
            return 1
    else:
        print("⚠️  Nothing to do (both --no-sync and --no-match specified)")
        return 0


def cmd_status(config: Config) -> int:
    """Show pipeline status."""
    store = StateStore(config.state_db_path)
    stats = store.get_stats()

    print("\n📊 Pipeline Status")
    print("=" * 40)
    print(f"  Documents processed:    {stats['documents_processed']}")
    print(f"  Extractions total:      {stats['extractions_total']}")
    print(f"  Pending review:         {stats['pending_review']}")
    print(f"  Imports total:          {stats['imports_total']}")
    print(f"  Imports successful:     {stats['imports_success']}")
    print(f"  Imports failed:         {stats['imports_failed']}")
    print()

    return 0


def main(args: list[str] | None = None) -> int:
    """Main entry point."""
    parser = create_cli()
    parsed = parser.parse_args(args)

    setup_logging(parsed.verbose)

    if not parsed.command:
        parser.print_help()
        return 1

    # Load config
    try:
        config = load_config(parsed.config)
    except Exception as e:
        print(f"❌ Failed to load config: {e}")
        return 1

    # Route to command
    if parsed.command == "scan":
        return cmd_scan(config, parsed.tag, parsed.limit)
    elif parsed.command == "extract":
        return cmd_extract(config, parsed.doc_id, parsed.tag, parsed.limit)
    elif parsed.command == "review":
        return cmd_review(config, parsed.host, parsed.port)
    elif parsed.command == "import":
        return cmd_import(config, parsed.auto_only, parsed.dry_run)
    elif parsed.command == "pipeline":
        return cmd_pipeline(config, parsed.tag, parsed.auto_only, parsed.limit)
    elif parsed.command == "status":
        return cmd_status(config)
    elif parsed.command == "reconcile":
        return cmd_reconcile(
            config,
            sync=parsed.sync,
            match=parsed.match,
            full_sync=parsed.full_sync,
            dry_run=parsed.dry_run,
        )
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
