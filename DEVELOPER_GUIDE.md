# Developer Guide: Paperless → Firefly III Pipeline

This guide covers complete setup from scratch on Ubuntu Server (20.04/22.04/24.04) or Raspberry Pi OS.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [System Setup](#system-setup)
3. [Docker Installation](#docker-installation)
4. [Service Configuration](#service-configuration)
5. [Development Setup](#development-setup)
6. [Architecture Overview](#architecture-overview)
7. [E-Invoice Format Support](#e-invoice-format-support)
8. [Testing](#testing)
9. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Hardware Requirements
- **Minimum**: Raspberry Pi 4 (4GB RAM) or equivalent
- **Recommended**: 8GB RAM for running all services together
- **Storage**: 50GB+ for documents and databases

### Software Requirements
- Ubuntu Server 20.04+ or Raspberry Pi OS (64-bit)
- Docker 24.0+ with Docker Compose v2
- Git

---

## System Setup

### 1. Update System

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl wget git vim htop
```

### 2. Configure Firewall (Optional)

```bash
# Allow SSH and web ports
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 8000/tcp   # Paperless
sudo ufw allow 8080/tcp   # Firefly III / Pipeline
sudo ufw enable
```

### 3. Configure Timezone

```bash
sudo timedatectl set-timezone Europe/Vienna  # Or your timezone
```

---

## Docker Installation

### Install Docker Engine

```bash
# Install prerequisites
sudo apt install -y ca-certificates curl gnupg lsb-release

# Add Docker GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker repository (Ubuntu)
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# For Raspberry Pi OS / Debian:
# echo \
#   "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
#   $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
#   sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add your user to docker group (logout/login required)
sudo usermod -aG docker $USER
```

### Verify Installation

```bash
# After logout/login:
docker --version
docker compose version
docker run hello-world
```

---

## Service Configuration

### Directory Structure

```bash
# Create project directories
mkdir -p ~/Code
cd ~/Code

# Clone the repository
git clone https://github.com/your-org/Paperless_FireflyIII_Parser.git LedgerBridge
cd LedgerBridge
```

### Environment Configuration

```bash
# Create .env file from template
cp .env.example .env

# Edit with your values
vim .env
```

**Required `.env` contents:**

```bash
# Paperless-ngx Configuration
# Internal Docker URL (container-to-container)
PAPERLESS_URL=http://paperless:8000
# External URL (what you type in browser)
PAPERLESS_EXTERNAL_URL=http://192.168.1.100:8000
PAPERLESS_TOKEN=your-paperless-api-token-here
PAPERLESS_FILTER_TAG=finance/inbox

# Firefly III Configuration  
# Internal Docker URL
FIREFLY_URL=http://firefly:8080
# External URL
FIREFLY_EXTERNAL_URL=http://192.168.1.100:8080
FIREFLY_TOKEN=your-firefly-personal-access-token-here
FIREFLY_DEFAULT_ACCOUNT=Checking Account

# Authentication
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password-here
DJANGO_SECRET_KEY=your-random-50-character-string-here

# Optional: Other service links
SYNCTHING_URL=http://192.168.1.100:8384
FIREFLY_IMPORTER_URL=http://192.168.1.100:8081

# Confidence Thresholds
CONFIDENCE_AUTO_THRESHOLD=0.85
CONFIDENCE_REVIEW_THRESHOLD=0.60

# Server Port
PF_PORT=8080
```

### Getting API Tokens

#### Paperless-ngx Token

```bash
# Option 1: Via Web UI
# Go to Settings → Users → Your User → Generate Token

# Option 2: Via Django Admin
docker exec -it paperless python manage.py drf_create_token admin
```

#### Firefly III Token

1. Login to Firefly III web interface
2. Go to **Options** → **Profile** → **OAuth**  
3. Click **Create Personal Access Token**
4. Name: "paperless-pipeline"
5. Copy the token (shown only once!)

---

## Development Setup

### Local Python Environment

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install in development mode
pip install -e ".[dev]"

# Verify installation
paperless-firefly --help
```

### Run Tests

```bash
# All tests
pytest

# With coverage report
pytest --cov=src/paperless_firefly --cov-report=html

# Specific test file
pytest tests/test_einvoice.py -v

# Only authentication tests
pytest tests/test_web_review.py -k "test_auth" -v
```

### Start Development Server

```bash
# Set environment variables
export PAPERLESS_URL=http://localhost:8000
export PAPERLESS_TOKEN=your-token
export FIREFLY_URL=http://localhost:8080
export FIREFLY_TOKEN=your-token

# Run web interface
paperless-firefly review --host 0.0.0.0 --port 8080
```

---

## Architecture Overview

### Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Pipeline Container                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │   Paperless  │  │   Firefly    │  │      Web Interface       │  │
│  │    Client    │  │    Client    │  │   (Django + Auth)        │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────────┘  │
│         │                 │                      │                  │
│         ▼                 ▼                      ▼                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                     Extractor Router                          │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐  │  │
│  │  │ E-Invoice  │  │    PDF     │  │     OCR Heuristics     │  │  │
│  │  │ (XML/UBL)  │  │   Parser   │  │   (Pattern Matching)   │  │  │
│  │  │ Priority:  │  │  Priority: │  │   Priority: 10         │  │  │
│  │  │   100      │  │    50      │  │                        │  │  │
│  │  └────────────┘  └────────────┘  └────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    State Store (SQLite)                       │  │
│  │   - Extractions    - Imports    - Reviews    - Auth DB       │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
         │                         │                      │
         ▼                         ▼                      ▼
   ┌───────────┐            ┌───────────┐          ┌───────────┐
   │ Paperless │            │  Firefly  │          │  Browser  │
   │   -ngx    │            │    III    │          │   (User)  │
   └───────────┘            └───────────┘          └───────────┘
```

### Confidence-Based Routing

```
                    Document from Paperless
                            │
                            ▼
                    ┌───────────────┐
                    │   Extractor   │
                    │    Router     │
                    └───────┬───────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
         E-Invoice      PDF Text      OCR Text
         (conf: 95%)   (conf: 75%)   (conf: 40%)
              │             │             │
              └─────────────┼─────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  Confidence   │
                    │    Score      │
                    └───────┬───────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
  conf ≥ 0.85         0.60 ≤ conf < 0.85   conf < 0.60
  ┌─────────┐         ┌─────────┐         ┌─────────┐
  │  AUTO   │         │ REVIEW  │         │ MANUAL  │
  │ Import  │         │  Queue  │         │ Review  │
  └─────────┘         └─────────┘         └─────────┘
```

---

## E-Invoice Format Support

The pipeline supports structured electronic invoice formats for highest-confidence extraction.

### Supported Standards

| Standard | Description | Confidence |
|----------|-------------|------------|
| **ZUGFeRD 2.x** | German hybrid PDF+XML format | 95-98% |
| **Factur-X** | French equivalent of ZUGFeRD | 95-98% |
| **XRechnung** | German government standard | 95-98% |
| **UBL 2.1** | Universal Business Language | 95-98% |
| **PEPPOL BIS** | Pan-European e-invoicing | 95-98% |

### How E-Invoice Extraction Works

1. **PDF Analysis**: Check for embedded XML attachments (ZUGFeRD/Factur-X)
2. **XML Detection**: Look for UBL/CII namespaces in content
3. **Namespace Parsing**: Use appropriate parser based on XML structure
4. **Field Extraction**: Map XML elements to transaction fields

### E-Invoice XML Structure (CII/ZUGFeRD)

```xml
<rsm:CrossIndustryInvoice xmlns:rsm="..." xmlns:ram="...">
  <rsm:ExchangedDocument>
    <ram:ID>INV-2024-001</ram:ID>           <!-- Invoice Number -->
    <ram:IssueDateTime>
      <udt:DateTimeString>20241118</udt:DateTimeString>
    </ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty>
        <ram:Name>Vendor GmbH</ram:Name>   <!-- Vendor Name -->
      </ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:GrandTotalAmount>119.00</ram:GrandTotalAmount>
        <ram:TaxTotalAmount>19.00</ram:TaxTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
```

### Creating Test E-Invoices

```python
# Generate test ZUGFeRD XML
from tests.fixtures.einvoice import create_test_cii_invoice

xml_content = create_test_cii_invoice(
    invoice_id="TEST-001",
    vendor="Test Vendor",
    amount="100.00",
    tax="19.00",
    currency="EUR",
    date="20241118"
)
```

---

## Testing

### Test Categories

| Category | File | Description |
|----------|------|-------------|
| **Unit** | `test_extractors.py` | Extractor logic |
| **Unit** | `test_einvoice.py` | E-invoice XML parsing |
| **Unit** | `test_confidence.py` | Confidence scoring |
| **Integration** | `test_clients.py` | API client behavior |
| **Integration** | `test_integration.py` | End-to-end pipeline |
| **Web** | `test_web_review.py` | Authentication, views |

### Running Tests

```bash
# Full test suite
pytest -v

# With coverage
pytest --cov=src/paperless_firefly --cov-report=html --cov-report=term

# Specific category
pytest tests/test_einvoice.py -v

# Watch mode (requires pytest-watch)
ptw -- tests/
```

### Writing Tests

```python
# tests/test_example.py
import pytest
from decimal import Decimal
from paperless_firefly.extractors.einvoice_extractor import EInvoiceExtractor

class TestEInvoiceExtractor:
    """Tests for e-invoice extraction."""
    
    @pytest.fixture
    def extractor(self):
        return EInvoiceExtractor()
    
    @pytest.fixture
    def sample_cii_xml(self):
        return '''<?xml version="1.0"?>
        <rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100">
          ...
        </rsm:CrossIndustryInvoice>'''
    
    def test_can_extract_cii(self, extractor, sample_cii_xml):
        """Should detect CII XML format."""
        assert extractor.can_extract(sample_cii_xml, None) is True
    
    def test_extracts_amount(self, extractor, sample_cii_xml):
        """Should extract total amount from CII."""
        result = extractor.extract(sample_cii_xml, None)
        assert result.amount == Decimal("119.00")
        assert result.amount_confidence >= 0.95
```

---

## Troubleshooting

### Common Issues

#### "Source account not found"

**Problem**: Firefly III doesn't have an account matching `FIREFLY_DEFAULT_ACCOUNT`.

**Solution**:
1. Check exact account name in Firefly III (Settings → Accounts)
2. Update `.env` with correct name
3. Or use the Account Selector dropdown in the web UI

```bash
# List accounts via API
curl -H "Authorization: Bearer $FIREFLY_TOKEN" \
  "${FIREFLY_URL}/api/v1/accounts?type=asset" | jq '.data[].attributes.name'
```

#### "Connection refused" to Paperless/Firefly

**Problem**: Docker container can't reach external services.

**Solution**:
1. Use container names on same Docker network
2. Or use host IP address (not localhost)
3. Check firewall allows the port

```bash
# Check container networking
docker network ls
docker network inspect bridge

# Test from inside container
docker exec -it paperless-firefly curl http://paperless:8000/api/
```

#### "UNIQUE constraint failed"

**Problem**: Trying to re-import an already processed document.

**Solution**:
1. The document was already imported
2. Check state database for existing import
3. Use `--force` flag to re-process

```bash
# Check state
sqlite3 /app/data/state.db "SELECT * FROM imports WHERE external_id LIKE '%123%'"

# Force re-extraction
paperless-firefly extract finance --force
```

#### "Permission denied" on SQLite database

**Problem**: Container user can't write to mounted volume.

**Solution**:
```bash
# Fix permissions on host
sudo chown -R 1000:1000 ./data

# Or in docker-compose.yml
services:
  paperless-firefly:
    user: "1000:1000"
```

### Debug Mode

```bash
# Enable verbose logging
export LOGLEVEL=DEBUG
paperless-firefly extract finance

# Or in docker-compose.yml
environment:
  - LOGLEVEL=DEBUG
```

### Logs

```bash
# View container logs
docker compose logs -f paperless-firefly

# Last 100 lines
docker compose logs --tail=100 paperless-firefly

# Filter by level
docker compose logs paperless-firefly 2>&1 | grep -i error
```

---

## Contributing

### Code Style

- Use `black` for formatting
- Use `mypy` for type checking
- Follow existing patterns in codebase

```bash
# Format code
black src/ tests/

# Type check
mypy src/

# All checks (pre-commit)
black --check src/ tests/
mypy src/
pytest
```

### Pull Request Process

1. Create feature branch from `main`
2. Add tests for new functionality
3. Ensure all tests pass
4. Update documentation if needed
5. Submit PR with clear description

---

## License

MIT License - See [LICENSE](LICENSE) for details.
