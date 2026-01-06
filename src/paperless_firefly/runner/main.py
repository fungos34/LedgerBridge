"""
CLI main entry point.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from ..config import Config, load_config
from ..extractors import ExtractorRouter
from ..firefly_client import FireflyClient, FireflyError
from ..paperless_client import PaperlessClient, PaperlessError
from ..review import ReviewDecision
from ..schemas.dedupe import compute_file_hash
from ..schemas.finance_extraction import ReviewState
from ..schemas.firefly_payload import build_firefly_payload, validate_firefly_payload
from ..state_store import ImportStatus, StateStore

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
    status_parser = subparsers.add_parser("status", help="Show pipeline status and statistics")

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


def cmd_extract(config: Config, doc_id: Optional[int], tag: str, limit: int) -> int:
    """Extract finance data from documents."""
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

            # Save to store
            store.upsert_document(
                document_id=doc.id,
                source_hash=source_hash,
                title=doc.title,
                document_type=doc.document_type,
                correspondent=doc.correspondent,
                tags=doc.tags,
            )

            store.save_extraction(
                document_id=doc.id,
                external_id=extraction.proposal.external_id,
                extraction_json=json.dumps(extraction.to_dict()),
                overall_confidence=extraction.confidence.overall,
                review_state=extraction.confidence.review_state.value,
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

    print(f"🌐 Starting review web interface...")

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


def cmd_import(config: Config, auto_only: bool, dry_run: bool) -> int:
    """Import transactions to Firefly III."""
    print("📤 Importing transactions to Firefly III...")

    store = StateStore(config.state_db_path)
    firefly = FireflyClient(
        base_url=config.firefly.base_url,
        token=config.firefly.token,
    )

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
                SELECT e.* FROM extractions e
                LEFT JOIN imports i ON e.external_id = i.external_id
                WHERE e.review_state = 'AUTO'
                AND i.id IS NULL
            """
            ).fetchall()
        else:
            # AUTO + reviewed/accepted
            rows = conn.execute(
                """
                SELECT e.* FROM extractions e
                LEFT JOIN imports i ON e.external_id = i.external_id
                WHERE i.id IS NULL
                AND (e.review_state = 'AUTO' OR e.review_decision IN ('ACCEPTED', 'EDITED'))
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
        try:
            extraction = json.loads(row["extraction_json"])
            from ..schemas.finance_extraction import FinanceExtraction

            ext = FinanceExtraction.from_dict(extraction)

            external_id = ext.proposal.external_id

            # Check if already imported (safety check) - includes failed imports
            existing_import = store.get_import_by_external_id(external_id)
            if existing_import:
                if existing_import.status == ImportStatus.IMPORTED.value:
                    print(f"  ⏭ [{ext.paperless_document_id}] Already imported")
                    continue
                elif existing_import.status == ImportStatus.FAILED.value:
                    # Reset failed imports so they can be retried
                    store.delete_import(external_id)

            # Build Firefly payload
            payload = build_firefly_payload(
                ext,
                default_source_account=config.firefly.default_source_account,
                paperless_base_url=config.paperless.base_url,
            )

            print(f"  📤 [{ext.paperless_document_id}] {ext.proposal.description}")
            print(f"     → {ext.proposal.amount} {ext.proposal.currency} on {ext.proposal.date}")

            if dry_run:
                print("     → [DRY RUN] Would import")
                continue

            # Create import record
            store.create_import(
                external_id=external_id,
                document_id=ext.paperless_document_id,
                payload_json=payload.to_json(),
                status=ImportStatus.PENDING,
            )

            # Send to Firefly
            firefly_id = firefly.create_transaction(payload, skip_duplicates=True)

            if firefly_id:
                store.update_import_success(external_id, firefly_id)
                print(f"     ✓ Imported (Firefly ID: {firefly_id})")
                imported += 1
            else:
                store.update_import_failed(external_id, "Duplicate detected")
                print("     ⚠ Skipped (duplicate)")

        except Exception as e:
            logger.exception("Import failed")
            print(f"     ❌ Error: {e}")
            # Note: row is sqlite3.Row, use bracket notation not .get()
            if not dry_run and row["external_id"]:
                # Only update if import record exists
                try:
                    store.update_import_failed(row["external_id"], str(e))
                except Exception:
                    pass  # Import record may not exist
            failed += 1

    print(f"\n✓ Imported: {imported}, Failed: {failed}")
    return 0 if failed == 0 else 1


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


def main(args: Optional[list[str]] = None) -> int:
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
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
