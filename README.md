# Paperless → Firefly III Finance Pipeline

A deterministic, testable pipeline that transforms finance documents from Paperless-ngx into Firefly III transactions with confidence scoring, optional human review, and strict deduplication.

## 🎯 Problem Statement

The core challenge is not OCR quality or import UX—it's:

> **How do you transform unstructured, heterogeneous documents into structured, revision-safe individual bookings—without duplicates, with maximum automation, and minimum manual effort?**

Four realities must be satisfied simultaneously:
1. Documents vary wildly in structure quality
2. Automation is never 100% correct
3. Humans should not be burdened with raw data
4. Accounting data must **never** be duplicated or inconsistent

## 🏗️ Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐     ┌────────────┐
│  Paperless  │────▶│  Extractor       │────▶│  Review Queue   │────▶│ Firefly III│
│  (Source)   │     │  + Confidence    │     │  (Web UI)       │     │ (Target)   │
└─────────────┘     └──────────────────┘     └─────────────────┘     └────────────┘
       │                    │                        │                      │
       ▼                    ▼                        ▼                      ▼
   Documents           Extraction              Human Review           Transactions
   + Tags              Confidence Score        Accept/Reject          Deduplicated
   + OCR Text          Field Confidence        Edit Fields            Linked to Docs
```

### Design Principles

- **Single Source of Truth (SSOT)**: Paperless owns documents, Firefly owns transactions
- **Deterministic Pipeline**: Same input → same output, every time
- **Confidence-Based Routing**: High confidence → auto-import, low → human review
- **Idempotent Operations**: Re-running never creates duplicates
- **External ID Tracking**: `paperless:{document_id}` links everything

## 🚀 Quick Start

### Docker (Recommended)

1. **Clone and configure:**
   ```bash
   git clone <repository>
   cd Paperless_FireflyIII_Parser
   cp .env.example .env
   # Edit .env with your Paperless and Firefly credentials
   ```

2. **Start the services:**
   ```bash
   docker compose up -d
   ```

3. **Access the web interface:**
   Open http://localhost:8080 for the review dashboard

### Local Development

1. **Install dependencies:**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   # source .venv/bin/activate  # Linux/macOS
   pip install -e ".[dev]"
   ```

2. **Configure environment:**
   ```bash
   export PAPERLESS_URL=http://paperless.local:8000
   export PAPERLESS_TOKEN=your-token
   export FIREFLY_URL=http://firefly.local:8080
   export FIREFLY_TOKEN=your-token
   ```

3. **Run commands:**
   ```bash
   paperless-firefly status              # Check connectivity
   paperless-firefly extract finance     # Extract from tagged docs
   paperless-firefly review              # Open web review UI
   paperless-firefly import              # Import approved items
   paperless-firefly pipeline finance    # Full automated pipeline
   ```

## 📖 Commands

| Command | Description |
|---------|-------------|
| `status` | Verify connectivity to Paperless and Firefly III |
| `extract <tag> [--limit N]` | Extract finance data from documents with tag |
| `review [--host HOST] [--port PORT]` | Start web-based review interface |
| `import` | Import approved transactions to Firefly III |
| `pipeline <tag> [--limit N]` | Run full extract → review-queue → import cycle |

## 🔧 Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PAPERLESS_URL` | ✅ | - | Paperless-ngx base URL |
| `PAPERLESS_TOKEN` | ✅ | - | Paperless API token |
| `PAPERLESS_FILTER_TAG` | ❌ | `finance/inbox` | Tag to filter documents |
| `FIREFLY_URL` | ✅ | - | Firefly III base URL |
| `FIREFLY_TOKEN` | ✅ | - | Firefly III Personal Access Token |
| `FIREFLY_DEFAULT_ACCOUNT` | ❌ | `Checking Account` | Default asset account |
| `CONFIDENCE_AUTO_THRESHOLD` | ❌ | `0.85` | Auto-import threshold (0-1) |
| `CONFIDENCE_REVIEW_THRESHOLD` | ❌ | `0.60` | Review queue threshold (0-1) |

### Confidence Thresholds

The pipeline uses a two-threshold system:

```
Confidence ≥ 0.85 (AUTO)     → Automatic import, no review needed
Confidence ≥ 0.60 (REVIEW)   → Queued for human review
Confidence < 0.60            → Flagged as low-quality, needs careful review
```

## 🖥️ Web Review Interface

The web interface provides:

- **Document Preview**: Inline iframe showing the original document
- **Extracted Data**: All fields with individual confidence scores
- **Editable Fields**: Modify any extracted value before approval
- **Actions**: Accept (import), Reject (skip), Skip (review later)

Access at http://localhost:8080 when running the review server.

## 🐳 Docker Deployment

### Production Setup

```yaml
# docker-compose.override.yml
services:
  paperless-firefly:
    environment:
      - CONFIDENCE_AUTO_THRESHOLD=0.90  # Be more conservative
    restart: always
```

### With Existing Stack

If you already run Paperless and Firefly in Docker:

```yaml
services:
  paperless-firefly:
    # ... existing config ...
    networks:
      - your-existing-network
    extra_hosts:
      - "paperless.local:host-gateway"
      - "firefly.local:host-gateway"
```

### Scheduled Processing

Use the worker profile for cron-style processing:

```bash
# Run the full pipeline once
docker compose --profile worker up paperless-firefly-worker

# Or use cron/systemd timer
0 */6 * * * docker compose --profile worker up paperless-firefly-worker
```

## 🧪 Testing

```bash
# Run all tests
pytest

# With coverage
pytest --cov=src/paperless_firefly --cov-report=html

# Run specific test module
pytest tests/test_clients.py -v
```

## 📁 Project Structure

```
src/paperless_firefly/
├── clients/           # API clients for Paperless and Firefly
│   ├── paperless.py   # Document fetching, content retrieval
│   └── firefly.py     # Transaction creation, deduplication
├── extractors/        # Data extraction from documents
│   └── ocr.py         # OCR-based extraction with confidence
├── schemas/           # Data contracts (SSOT)
│   ├── extraction.py  # FinanceExtraction schema
│   └── firefly.py     # FireflyPayload schema
├── state/             # Persistence layer
│   └── store.py       # SQLite-based state tracking
├── review/            # Human-in-the-loop interface
│   └── web/           # Django web application
│       ├── views.py   # Review, accept, reject handlers
│       └── templates/ # HTML templates
└── runner/            # CLI entry point
    └── main.py        # Click-based CLI
```

## 🔗 External ID Format

The deterministic external_id ensures no double bookings:

```
paperless:{doc_id}:{sha256[:16]}:{amount}:{date}
```

Example: `paperless:1234:abcdef1234567890:35.70:2024-11-18`

This format allows:
- Tracing any Firefly transaction back to its source document
- Preventing duplicate imports even if the pipeline is re-run
- Auditing the complete document-to-transaction chain

## 🔒 Security Notes

- API tokens are never logged or exposed in the UI
- The web interface is for **LAN use only** (no authentication)
- Consider adding a reverse proxy with auth for exposed deployments
- All transactions are tracked with external IDs for auditability

## 📄 License

MIT

## 🙏 Acknowledgments

Built for the [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) and [Firefly III](https://github.com/firefly-iii/firefly-iii) communities.
