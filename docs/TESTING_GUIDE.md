# Testing the Pipeline

This guide explains how to test the Paperless → Firefly III pipeline.

## Quick Start

### 1. Tag Documents in Paperless

For a document to be processed by the pipeline, it needs to have the **filter tag** applied. By default, this is:

```
finance/inbox
```

**To tag a document in Paperless:**
1. Open Paperless web interface
2. Find a financial document (receipt, invoice, bank statement, etc.)
3. Click "Edit" or open the document
4. In the **Tags** section, add the tag: `finance/inbox`
5. Save the document

### 2. Run the Pipeline

You have several options to process documents:

#### Option A: Full Automated Pipeline (Recommended for testing)

```bash
docker compose run --rm paperless-firefly pipeline finance/inbox --limit 5
```

This will:
- Extract data from up to 5 documents tagged with `finance/inbox`
- Auto-import high-confidence extractions
- Queue low-confidence extractions for review

#### Option B: Extract Only (Manual Review)

```bash
# Extract and queue for review
docker compose run --rm paperless-firefly extract --tag finance/inbox --limit 5

# Then open the web interface to review
# http://YOUR_RASPBERRY_PI_IP:8080
```

#### Option C: Use the Web Interface

The web server (already running at port 8080) shows all pending reviews. Run extraction separately:

```bash
docker compose exec paperless-firefly paperless-firefly extract --tag finance/inbox --limit 5
```

Then refresh the web interface to see new items.

---

## Document Requirements

### What Makes a Good Test Document?

The pipeline extracts finance data using OCR text analysis. Best results come from:

**✅ Good Document Types:**
- Receipts with clear amounts and dates
- Invoices with vendor names
- Bank statements
- Credit card statements
- Utility bills

**✅ Required Information:**
- **Amount**: A clear monetary value (e.g., "Total: $45.99")
- **Date**: A transaction or document date
- **Currency**: USD, EUR, etc. (or implied from context)

**❌ Avoid for Testing:**
- Multi-page statements with many transactions (start simple!)
- Handwritten receipts (OCR quality dependent)
- Documents without clear amounts

### Example: Testing with a Receipt

1. **Upload a receipt to Paperless** (or use an existing one)
2. **Make sure Paperless has processed it** (OCR completed)
3. **Add the tag**: `finance/inbox`
4. **Run extraction**:
   ```bash
   docker compose exec paperless-firefly paperless-firefly extract --tag finance/inbox --limit 1
   ```
5. **Check the web interface**: http://YOUR_PI_IP:8080

---

## Understanding the Process

### Pipeline Flow

```
┌─────────────────┐
│  Tag Document   │  ← You do this in Paperless
│  finance/inbox  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Run Extract    │  ← Command or scheduled job
│  Command        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  OCR Analysis   │  ← Automatic extraction
│  & Confidence   │     Looks for: amount, date, vendor
└────────┬────────┘
         │
         ├─── Confidence ≥ 85% ──→ [Auto Import to Firefly III]
         │
         ├─── Confidence 60-85% ─→ [Review Queue (Web Interface)]
         │
         └─── Confidence < 60% ──→ [Review Queue (Flagged for careful review)]
```

### Confidence Thresholds

Set in your `.env` file:

```bash
CONFIDENCE_AUTO_THRESHOLD=0.85    # Auto-import if ≥ 85%
CONFIDENCE_REVIEW_THRESHOLD=0.60  # Flag if < 60%
```

**Adjust these based on your needs:**
- Higher thresholds = more manual review, fewer errors
- Lower thresholds = more automation, slight error risk

---

## Checking Results

### View Extraction Status

```bash
docker compose exec paperless-firefly paperless-firefly status
```

This shows:
- Total documents processed
- Extractions pending review
- Imported transactions
- Error count

### View in Firefly III

1. Open Firefly III web interface
2. Go to **Transactions** → **Withdrawals** (or appropriate type)
3. Look for transactions with external ID starting with `paperless:`
4. Click on a transaction to see the link back to the Paperless document

### Check the Database

The state is stored in `/app/data/state.db` inside the container. To inspect:

```bash
docker compose exec paperless-firefly sqlite3 /app/data/state.db
# Then run SQL queries:
SELECT * FROM extractions;
SELECT * FROM imports;
.quit
```

---

## Common Testing Scenarios

### Scenario 1: Test High-Confidence Auto-Import

**Goal**: Document should auto-import without review

1. Use a **clear, simple receipt** (e.g., supermarket receipt)
2. Ensure OCR quality is good in Paperless
3. Tag with `finance/inbox`
4. Run: `docker compose exec paperless-firefly paperless-firefly pipeline finance/inbox --limit 1`
5. **Expected**: Transaction appears in Firefly III immediately

### Scenario 2: Test Manual Review

**Goal**: Review and edit before import

1. Use a **complex document** (e.g., invoice with multiple items)
2. Tag with `finance/inbox`
3. Run: `docker compose exec paperless-firefly paperless-firefly extract --tag finance/inbox --limit 1`
4. Open web interface: http://YOUR_PI_IP:8080
5. **Expected**: See document in review queue, edit fields, then approve

### Scenario 3: Test Deduplication

**Goal**: Verify re-running doesn't create duplicates

1. Process a document successfully
2. Run the same extract command again
3. **Expected**: Pipeline detects it was already processed (check logs)

---

## Troubleshooting

### No Documents Found

```bash
# Check if documents have the tag
docker compose exec paperless-firefly paperless-firefly scan --tag finance/inbox
```

If no documents shown, verify in Paperless that:
- Documents exist and are processed
- Tag `finance/inbox` is applied correctly
- API token has read permissions

### Extraction Fails

Check logs:
```bash
docker logs paperless-firefly
```

Common issues:
- Paperless API not reachable (network issue)
- Document has no OCR text
- Document format not supported

### Web Interface Shows Nothing

Make sure you ran extraction first:
```bash
docker compose exec paperless-firefly paperless-firefly extract --tag finance/inbox --limit 5
```

The web interface only shows what's in the review queue.

---

## Scheduled Processing

For automatic processing, set up a cron job on your Raspberry Pi:

```bash
# Edit crontab
crontab -e

# Add this line (runs every 6 hours)
0 */6 * * * cd ~/Code/Paperless_FireflyIII_parser/LedgerBridge && docker compose run --rm paperless-firefly pipeline finance/inbox >> ~/paperless-firefly.log 2>&1
```

Or use Docker's built-in scheduler with the worker profile (see README).

---

## Next Steps

Once testing is successful:
1. **Tag more documents** with `finance/inbox`
2. **Adjust confidence thresholds** based on accuracy
3. **Set up automated scheduling** for hands-off operation
4. **Monitor the web interface** periodically for items needing review

Need help? Check the logs with `docker logs -f paperless-firefly`
