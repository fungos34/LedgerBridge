# SparkLink Workflow Guide

This document describes the complete workflow for managing financial documents with SparkLink (formerly Paperless-Firefly Pipeline).

## Overview

SparkLink bridges Paperless-ngx (document management) and Firefly III (personal finance) with two primary workflows:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SparkLink Workflows                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  WORKFLOW 1: Document → Transaction (Create New)                            │
│  ───────────────────────────────────────────────                            │
│  Paperless Doc → Extract → Review → Import → NEW Firefly Transaction        │
│                                                                             │
│  WORKFLOW 2: Document ↔ Transaction (Match Existing)                        │
│  ───────────────────────────────────────────────────                        │
│  Paperless Doc ←→ Reconciliation ←→ EXISTING Firefly Transaction           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Web Interface Pages

| Page | URL | Purpose |
|------|-----|---------|
| **Landing Page** | `/` | Dashboard with stats and navigation |
| **Review Queue** | `/review/` | Review extracted documents before creating transactions |
| **Reconciliation** | `/reconciliation/` | Match documents to existing bank transactions |
| **Proposals** | `/reconciliation/list/` | Review auto-generated match proposals |
| **Archive** | `/archive/` | View processed documents (imported/rejected) |
| **Import Queue** | `/import-queue/` | Monitor pending imports to Firefly |
| **Audit Trail** | `/audit-trail/` | View interpretation history |
| **Settings** | `/settings/` | User preferences |
| **Admin** | `/admin/` | Django admin panel |

---

## Workflow 1: Extract & Import (New Transactions)

Use this workflow when you have receipts/invoices and want to **create new transactions** in Firefly III.

### Step 1: Extract Documents

From the Review Queue (`/review/`), click **"Extract & Sync"** to:
1. Fetch documents from Paperless matching your configured tags
2. Run OCR extraction to detect amounts, dates, vendors
3. Calculate confidence scores for each extraction

### Step 2: Review Extractions

For each extracted document:
- **High confidence (≥85%)**: Can be auto-imported
- **Medium confidence (60-85%)**: Requires human review
- **Low confidence (<60%)**: Needs careful verification

Actions available:
- **Accept**: Approve the extraction for import
- **Reject**: Skip this document (won't create transaction)
- **Edit**: Modify extracted values before accepting
- **Skip**: Defer review for later

### Step 3: Import to Firefly

After review, documents are imported as **new transactions** in Firefly III with:
- Extracted amount, date, description
- Category mapping (if configured)
- Linkage markers connecting back to Paperless document

---

## Workflow 2: Reconciliation (Match Existing)

Use this workflow when you already have **bank transactions in Firefly** (via import/sync) and want to **link receipts** to them.

### Step 1: Sync Data Sources

From the Reconciliation Dashboard (`/reconciliation/`):
- **Sync Firefly**: Pulls recent transactions (last 90 days by default)
- **Sync Paperless**: Pulls documents matching your filter tags

### Step 2: Manual or Auto Matching

**Option A: Manual Matching**
1. Select a Paperless document from the left panel
2. Select a Firefly transaction from the right panel
3. Click "Link Selected" to create the connection

**Option B: Auto Matching**
1. Click "Run Auto-Match" to generate proposals
2. System matches by: amount, date proximity, vendor similarity
3. Review proposals in `/reconciliation/list/`

### Step 3: Review Match Proposals

For each proposal:
- **Accept**: Create linkage between document and transaction
- **Reject**: Mark as not a match

### Step 4: Handle Orphans

If no match exists:
- **Orphan Document**: Receipt with no bank transaction → Create new transaction
- **Orphan Transaction**: Bank transaction with no receipt → Mark as confirmed

---

## Linkage Markers

When documents are linked to transactions, SparkLink writes:

| Field | Format | Purpose |
|-------|--------|---------|
| `external_id` | `paperless:{doc_id}:{hash}:{amount}:{date}` | Unique identifier |
| `internal_reference` | `PAPERLESS:{doc_id}` | Quick lookup |
| `notes` | `Paperless doc_id={doc_id}` | Human-readable link |

These markers:
- Prevent duplicate imports
- Enable bidirectional navigation
- Provide audit trail

---

## Decision Flow Chart

```
                    ┌─────────────────────┐
                    │   New Document in   │
                    │     Paperless       │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Extract Data     │
                    │  (OCR + Metadata)   │
                    └──────────┬──────────┘
                               │
                               ▼
              ┌────────────────────────────────┐
              │   Do you have existing bank    │
              │   transactions in Firefly?     │
              └───────────────┬────────────────┘
                              │
           ┌──────────────────┴──────────────────┐
           │                                     │
           ▼ YES                                 ▼ NO
┌─────────────────────┐               ┌─────────────────────┐
│   RECONCILIATION    │               │   REVIEW & IMPORT   │
│   Match to existing │               │   Create new tx     │
│   bank transaction  │               │   from document     │
└──────────┬──────────┘               └──────────┬──────────┘
           │                                     │
           ▼                                     ▼
┌─────────────────────┐               ┌─────────────────────┐
│  /reconciliation/   │               │     /review/        │
│  Select & Link      │               │  Accept/Reject/Edit │
└──────────┬──────────┘               └──────────┬──────────┘
           │                                     │
           └──────────────────┬──────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │   Document linked   │
                    │   to Firefly tx     │
                    └─────────────────────┘
```

---

## API Endpoints

### Sync Operations
- `POST /reconciliation/sync-firefly/` - Fetch Firefly transactions
- `POST /reconciliation/sync-paperless/` - Fetch Paperless documents
- `POST /extract/` - Run extraction pipeline

### CRUD Operations
- `POST /reconciliation/manual-link/` - Create manual link
- `POST /reconciliation/link-document/` - Link document to transaction
- `POST /reconciliation/confirm-orphan/` - Confirm no match exists
- `POST /reconciliation/run-auto-match/` - Generate match proposals

### Status APIs
- `GET /api/stats/` - Pipeline statistics
- `GET /api/extract/status/` - Extraction job status
- `GET /api/reconciliation/sync-status/` - Sync job status

---

## Best Practices

### 1. Bank-First Matching
If you import bank statements to Firefly, always try reconciliation first before creating new transactions from receipts.

### 2. Tag Your Documents
Use consistent tags in Paperless (e.g., `finance/inbox`) to filter documents for processing.

### 3. Review Low-Confidence Items
Documents with <60% confidence often have OCR issues - verify amounts manually.

### 4. Use External IDs
The `external_id` in Firefly is your audit trail - never delete or modify it manually.

### 5. Periodic Reconciliation
Run reconciliation weekly/monthly to ensure all receipts are linked to transactions.

---

## Troubleshooting

### "No pending migrations" spam in logs
Fixed in v1.1 - StateStore now uses singleton pattern.

### Transaction linkage fails
Check that Firefly III allows external_id updates on existing transactions.

### Documents not appearing
Verify your `PAPERLESS_FILTER_TAG` matches your Paperless tags.

### 500 errors on reconciliation
Ensure the StateStore database is accessible and migrations have run.
