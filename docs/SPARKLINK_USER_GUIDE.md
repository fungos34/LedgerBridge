# SparkLink User Guide

**The Complete Reference for Document-to-Transaction Automation**

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Getting Started](#2-getting-started)
3. [Navigation Overview](#3-navigation-overview)
4. [Feature Guide](#4-feature-guide)
   - [4.1 Dashboard (Home)](#41-dashboard-home)
   - [4.2 Review & Import](#42-review--import)
   - [4.3 Processing History](#43-processing-history)
   - [4.4 Reconciliation](#44-reconciliation)
   - [4.5 Sync Assistant](#45-sync-assistant)
   - [4.6 AI Assistance](#46-ai-assistance)
   - [4.7 Settings](#47-settings)
5. [Common Workflows](#5-common-workflows)
6. [Step-by-Step Guides](#6-step-by-step-guides)
7. [Troubleshooting](#7-troubleshooting)
8. [Quick Reference](#8-quick-reference)

---

## 1. Introduction

### What is SparkLink?

SparkLink is a bridge between **Paperless-ngx** (document management) and **Firefly III** (personal finance) that automates the process of turning receipts, invoices, and financial documents into accounting transactions.

### When to Use SparkLink

Use SparkLink when you want to:
- **Automate receipt processing** - Extract transaction data from scanned receipts and invoices
- **Link documents to bank transactions** - Match receipts to existing bank imports in Firefly
- **Maintain audit trails** - Keep documents connected to their corresponding transactions
- **Reduce manual data entry** - Let AI help categorize and fill in transaction details
- **Prevent duplicate transactions** - Smart deduplication ensures no double-bookings

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Extraction** | The process of reading financial data (amount, date, vendor) from a document |
| **Linkage** | A connection between a Paperless document and a Firefly transaction |
| **Reconciliation** | Matching documents to existing bank transactions |
| **SSOT** | Single Source of Truth - Paperless owns documents, Firefly owns transactions |
| **External ID** | A unique identifier that links documents to transactions |

### What to Expect

1. **Documents flow in** from Paperless-ngx (tagged with your finance tag)
2. **Data is extracted** automatically using OCR and structured formats (ZUGFeRD, UBL)
3. **You review** the extracted data (or let high-confidence items auto-import)
4. **Transactions are created** or linked in Firefly III
5. **Everything stays connected** via linkage markers

---

## 2. Getting Started

### Prerequisites

Before using SparkLink, ensure you have:
- ✅ Paperless-ngx running and accessible
- ✅ Firefly III running and accessible
- ✅ API tokens for both services
- ✅ A tag in Paperless for financial documents (default: `finance/inbox`)

### First-Time Setup

1. **Access SparkLink** at `http://your-server:8080`
2. **Log in** with your credentials (or register if first user)
3. **Go to Settings** (⚙️ in user dropdown)
4. **Configure connections**:
   - Enter your Paperless URL and API token
   - Enter your Firefly URL and API token
   - Set your default asset account (e.g., "Checking Account")
5. **Test connections** using the status indicators

### Quick Start Workflow

```
1. Upload receipt to Paperless → Tag with "finance/inbox"
2. Open SparkLink → Go to "Review & Import"
3. Click "Extract & Sync" → Document appears in queue
4. Review the extraction → Accept or Edit
5. Transaction created in Firefly → Done!
```

---

## 3. Navigation Overview

### Main Navigation Bar

| Icon | Section | Purpose |
|------|---------|---------|
| 🏠 | **Home** | Dashboard with stats and quick actions |
| 📝 | **Review & Import** | Main workflow for reviewing and importing documents |
| 📊 | **History** | View archive, documents, AI queue, and audit trail |

### User Dropdown Menu

| Icon | Item | Description |
|------|------|-------------|
| ⚙️ | Settings | Configure API connections and preferences |
| 🔑 | Change Password | Update your account password |
| 🔗 | Bank Reconciliation | Match documents to existing bank transactions |
| 🔄 | Firefly Sync Assistant | Share Firefly entities between users |
| 📄 | Paperless-ngx ↗ | External link to Paperless (if configured) |
| 💰 | Firefly III ↗ | External link to Firefly III (if configured) |
| 📡 | Syncthing ↗ | External link to Syncthing (if configured) |
| 📥 | Firefly Data Importer ↗ | External link to Data Importer (if configured) |
| 🛠️ | Admin Panel | Django admin (staff only) |
| 🚪 | Logout | Sign out of SparkLink |

### Symbol Legend (Consistent Throughout)

| Symbol | Meaning |
|--------|---------|
| 🏠 | Home/Dashboard |
| ✏️/📝 | Review/Edit |
| 📊 | History/Statistics |
| ⚙️ | Settings/Configuration |
| 🔑 | Password/Security |
| 🔗 | Linkage/Reconciliation |
| 🔄 | Sync/Refresh |
| 📄 | Paperless Document |
| 💰 | Firefly III |
| 📦 | Archive |
| 🤖 | AI/Automation |
| 📜 | Audit Trail |
| 💬 | Chat Assistant |
| 📡 | Syncthing |
| 📥 | Firefly Data Importer |
| 🚪 | Logout/Exit |

---

## 4. Feature Guide

### 4.1 Dashboard (Home)

**Location:** 🏠 Home (main nav)

The dashboard provides an overview of your SparkLink activity:

- **Status Cards** - Connection status to Paperless and Firefly
- **Quick Stats** - Documents pending, processed today, total transactions
- **Recent Activity** - Latest extractions and imports
- **Quick Actions** - Shortcuts to common tasks

### 4.2 Review & Import

**Location:** ✏️ Review & Import (main nav)

This is the primary workflow for processing documents:

#### Queue Views

| Section | Description |
|---------|-------------|
| **Paperless Documents** | Documents fetched from Paperless awaiting processing |
| **Firefly Transactions** | Bank transactions that may need document links |
| **Import Queue** | Approved items waiting to be sent to Firefly |

#### Document Cards

Each document shows:
- **Title** - From Paperless
- **Amount** - Extracted from document
- **Date** - Transaction/invoice date
- **Confidence** - How sure the system is about the extraction
- **AI Status** - 🤖 icon shows AI suggestion availability

#### Actions

| Action | When to Use |
|--------|-------------|
| **Review** | Open detailed view to verify/edit extraction |
| **Quick Accept** | Accept high-confidence extraction as-is |
| **AI Confirm** | Accept AI suggestions and create transaction |
| **Link** | Connect document to an existing Firefly transaction |

#### Review Detail Page

When you click "Review", you see:
- **Document Preview** - The original PDF/image
- **Extraction Form** - Editable fields with AI suggestions
- **Link Suggestions** - Potential matches to existing transactions
- **Actions** - Accept, Reject, Skip, or Edit and Save

### 4.3 Processing History

**Location:** 📊 History (main nav)

The History page has four tabs:

#### 📦 Archive Tab
- Shows processed extractions (imported, rejected, failed)
- Actions: Reset (send back to review), View details
- Filter by status

#### 📄 Documents Tab
- Browse all Paperless documents
- List/Unlist documents for extraction
- Search and filter options

#### 🤖 AI Queue Tab
- View AI job status (pending, processing, completed, failed)
- Actions: Cancel, Retry, View results
- Statistics on AI processing

#### 📜 Audit Trail Tab
- Full history of all interpretation decisions
- Shows: Who, What, When, How (rules vs LLM)
- Filter by document, transaction, or decision source

### 4.4 Reconciliation

**Location:** 🔗 Reconciliation (user dropdown → Tools)

Use reconciliation when you have bank transactions in Firefly and want to link receipts to them:

#### Dashboard View
- **Paperless Pending** - Documents waiting for matches
- **Firefly Unmatched** - Transactions without receipts
- **Proposals** - System-suggested matches to review

#### Workflow
1. **Sync Firefly** - Pull recent transactions
2. **Sync Paperless** - Pull documents
3. **Run Auto-Match** - Let the system find matches
4. **Review Proposals** - Accept or reject suggested links
5. **Manual Link** - Create links manually if needed

#### Match Criteria
The system matches based on:
- Amount (exact or within 5%)
- Date (exact or within 3 days)
- Description similarity
- Vendor patterns

### 4.5 Sync Assistant

**Location:** 🔄 Sync Assistant (user dropdown → Tools)

Share Firefly entities (categories, tags, accounts) between SparkLink users:

#### Entity Types

| Group | Entities |
|-------|----------|
| **Core** | Categories, Tags |
| **Accounts** | Asset, Expense, Revenue accounts |
| **Planning** | Budgets, Bills |
| **Automation** | Rules, Rule Groups, Recurrences |

#### Workflow
1. **Fetch from Firefly** - Import entities to your pool
2. **Share** - Grant access to other users
3. **Import** - Recipients can import shared entities to their Firefly

### 4.6 AI Assistance

SparkLink includes optional AI-powered assistance via Ollama:

#### Features
- **Category suggestions** - AI recommends categories based on vendor/description
- **Field suggestions** - AI fills in missing fields
- **Split suggestions** - AI suggests how to split multi-item invoices

#### How It Works
1. Document is extracted
2. AI job is scheduled (runs in background)
3. Suggestions appear with 🤖 badge on review form
4. Accept suggestions individually or all at once

#### Controls
- **Global opt-out** - Disable AI in Settings
- **Per-document opt-out** - Disable for specific documents
- **Schedule** - Set active hours for AI processing
- **Calibration** - First 100 suggestions require manual approval

### 4.7 Settings

**Location:** ⚙️ Settings (user dropdown)

#### Connection Settings
- **Paperless URL** - Base URL for Paperless API
- **Paperless Token** - Your API token
- **Firefly URL** - Base URL for Firefly API
- **Firefly Token** - Your personal access token
- **Default Account** - Asset account for new transactions

#### AI Settings
- **Enable AI** - Toggle AI assistance
- **Model** - Select Ollama model
- **Schedule** - Set processing hours
- **Opt-out** - Disable AI globally

#### External Links
- **Syncthing URL** - Link to Syncthing UI
- **Firefly Data Importer URL** - Link to Data Importer

---

## 5. Common Workflows

### Workflow 1: New Receipt → New Transaction

**Use when:** You have a receipt and need to create a new transaction

```
1. Upload receipt to Paperless with "finance/inbox" tag
2. Go to Review & Import
3. Click "Extract & Sync"
4. Find your document in the list
5. Click "Review"
6. Verify/edit the extracted data
7. Click "Accept"
8. Document goes to Import Queue
9. Import runs automatically (or click "Run Import")
10. Transaction created in Firefly with document link
```

### Workflow 2: Receipt → Existing Bank Transaction

**Use when:** You already imported bank transactions and have matching receipts

```
1. Upload receipt to Paperless
2. Go to Reconciliation
3. Click "Sync Firefly" and "Sync Paperless"
4. Click "Run Auto-Match"
5. Review the match proposals
6. Accept correct matches, reject incorrect ones
7. Documents are linked to existing transactions
```

### Workflow 3: Bank Statement → Find Receipts

**Use when:** You imported a bank statement and want to find/attach receipts

```
1. Import bank statement via Firefly Data Importer
2. Go to Reconciliation
3. Click "Sync Firefly"
4. Look at "Unlinked Transactions" section
5. Click on a transaction
6. System shows potential document matches
7. Select and link the correct document
```

### Workflow 4: Bulk Processing

**Use when:** You have many documents to process at once

```
1. Upload multiple receipts to Paperless
2. Go to History → Documents tab
3. Select documents and click "List" to tag them
4. Go to Review & Import
5. Click "Extract & Sync"
6. Use "Quick Accept" for high-confidence items
7. Review uncertain items individually
```

---

## 6. Step-by-Step Guides

### Guide: Setting Up API Connections

1. **Get Paperless Token:**
   - Log into Paperless-ngx
   - Go to Settings → Users → Your User → Edit
   - Find or generate API Token
   - Copy the token

2. **Get Firefly Token:**
   - Log into Firefly III
   - Go to Options → Profile → OAuth
   - Click "Create Personal Access Token"
   - Name it "SparkLink"
   - Copy the token (only shown once!)

3. **Configure SparkLink:**
   - Open SparkLink Settings (⚙️)
   - Enter Paperless URL (e.g., `http://192.168.1.100:8000`)
   - Paste Paperless Token
   - Enter Firefly URL (e.g., `http://192.168.1.100:8080`)
   - Paste Firefly Token
   - Click "Save"

### Guide: Processing Your First Document

1. **Prepare Document:**
   - Scan or photograph your receipt
   - Upload to Paperless-ngx
   - Add the tag "finance/inbox"

2. **Extract Data:**
   - Open SparkLink → Review & Import
   - Click "Extract & Sync"
   - Wait for extraction to complete

3. **Review Extraction:**
   - Find your document in the list
   - Note the confidence score (colored badge)
   - Click "Review" to open detail view

4. **Verify and Accept:**
   - Check the extracted amount, date, vendor
   - Correct any errors
   - Select the correct category and accounts
   - Click "Accept" to approve

5. **Import to Firefly:**
   - Approved documents queue for import
   - Click "Run Import" or wait for auto-import
   - Check Firefly to see your new transaction

### Guide: Using AI Suggestions

1. **Enable AI (if not already):**
   - Go to Settings → AI Assistant section
   - Toggle "Enable AI Suggestions" on
   - Select your preferred model

2. **Process Document:**
   - Extract document as usual
   - AI job is scheduled automatically
   - Wait for 🤖 ✓ badge to appear

3. **Review AI Suggestions:**
   - Open the document review
   - Fields with AI suggestions show 🤖 badge
   - Click "Accept" next to suggestion to use it
   - Or click "Accept All AI" to use all suggestions

4. **Calibration Period:**
   - First 100 suggestions require manual review
   - After calibration, high-confidence AI can auto-apply

### Guide: Reconciling with Bank Statements

1. **Import Bank Statement:**
   - Use Firefly Data Importer to import your bank statement
   - Transactions appear in Firefly without receipts

2. **Sync in SparkLink:**
   - Go to Reconciliation
   - Click "Sync Firefly" to pull transactions
   - Click "Sync Paperless" to pull documents

3. **Run Matching:**
   - Click "Run Auto-Match"
   - System finds potential matches by amount/date

4. **Review Proposals:**
   - Go to Proposals section
   - Each proposal shows document ↔ transaction pair
   - Click to expand and see details
   - Click "Accept" if correct, "Reject" if not

5. **Handle Orphans:**
   - **Orphan Document:** No matching transaction → Create new
   - **Orphan Transaction:** No receipt → Confirm as is

### Guide: Sharing Firefly Entities

1. **Fetch Your Entities:**
   - Go to Sync Assistant
   - Select entity type (e.g., Categories)
   - Click "Fetch from Firefly"
   - Your entities appear in your pool

2. **Share with Another User:**
   - Click "Share" on the entity
   - Select the user to share with
   - Confirm the share

3. **Import Shared Entities (as recipient):**
   - Go to Sync Assistant
   - Switch to "Shared with Me" section
   - Select entities to import
   - Click "Import to Firefly"
   - Entities created in your Firefly instance

---

## 7. Troubleshooting

### Connection Issues

| Problem | Solution |
|---------|----------|
| "Connection refused" | Check URL is correct, service is running |
| "401 Unauthorized" | Token is invalid or expired, regenerate it |
| "500 Internal Server Error" | Check service logs, may need restart |
| "Timeout" | Network issue or service overloaded |

### Extraction Issues

| Problem | Solution |
|---------|----------|
| No amount extracted | Document may need better OCR, or format not supported |
| Wrong amount | Edit manually, amount heuristics may need tuning |
| Low confidence | Document quality issue, review carefully |
| Document not appearing | Check Paperless tag matches your filter tag |

### Import Issues

| Problem | Solution |
|---------|----------|
| "Source account not found" | Check account name in Settings matches Firefly exactly |
| "Duplicate detected" | Document was already imported, check by external_id |
| Import stuck | Check import queue, may need manual retry |

### AI Issues

| Problem | Solution |
|---------|----------|
| No AI suggestions | Check Ollama is running, model is available |
| AI taking too long | Check Ollama resources, try smaller model |
| Wrong suggestions | Reject and provide feedback, AI learns over time |
| AI disabled | Check global opt-out in Settings |

---

## 8. Quick Reference

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Submit current form |
| `Esc` | Cancel/close modal |
| `Tab` | Move to next field |

### Status Colors

| Color | Meaning |
|-------|---------|
| 🟢 Green | Success/Imported/Linked |
| 🟡 Yellow | Pending/Warning/Review needed |
| 🔴 Red | Error/Failed/Rejected |
| 🔵 Blue | Processing/Info |
| ⚫ Gray | Unknown/Not started |

### Confidence Levels

| Level | Threshold | Action |
|-------|-----------|--------|
| High | ≥85% | Can auto-import |
| Medium | 60-85% | Needs review |
| Low | <60% | Manual verification required |

### External ID Format

```
paperless:{doc_id}:{hash}:{amount}:{date}
```

Example: `paperless:1234:abcdef12:35.70:2024-11-18`

### API Endpoints Reference

| Endpoint | Purpose |
|----------|---------|
| `/` | Dashboard |
| `/unified-review/` | Review queue |
| `/processing-history/` | History tabs |
| `/reconciliation/` | Reconciliation dashboard |
| `/sync-assistant/` | Entity sync |
| `/settings/` | User settings |
| `/admin/` | Django admin |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PAPERLESS_URL` | - | Paperless API URL |
| `PAPERLESS_TOKEN` | - | Paperless API token |
| `FIREFLY_URL` | - | Firefly API URL |
| `FIREFLY_TOKEN` | - | Firefly API token |
| `CONFIDENCE_AUTO_THRESHOLD` | 0.85 | Auto-import threshold |
| `CONFIDENCE_REVIEW_THRESHOLD` | 0.60 | Review threshold |
| `LLM_ENABLED` | false | Enable AI features |
| `OLLAMA_URL` | localhost:11434 | Ollama server |

---

## For AI Assistants

This section provides context for AI agents helping users with SparkLink.

### Identity

You are the SparkLink Assistant, an AI helper integrated into the SparkLink application. Your role is to:
- Help users understand and use SparkLink features
- Guide users through workflows step-by-step
- Provide financial organization advice
- Troubleshoot common issues
- Answer questions about document-to-transaction automation

### Response Guidelines

1. **Language:** Always respond in the same language the user used
2. **Tone:** Be helpful, clear, and concise
3. **Context:** Use the current page context to provide relevant help
4. **Actions:** When suggesting actions, be specific about where to click
5. **Warnings:** Alert users to potential issues (duplicates, data loss)

### Common User Questions

**"How do I start?"**
→ Guide them to Settings to configure API connections, then to Review & Import

**"My document isn't appearing"**
→ Check Paperless tag, click Extract & Sync, wait for processing

**"What's the difference between Accept and Link?"**
→ Accept creates NEW transaction, Link connects to EXISTING transaction

**"Why is confidence low?"**
→ Document quality, OCR issues, or unusual format - review carefully

**"How do I undo an import?"**
→ Edit/delete in Firefly directly, or use Archive → Reset in SparkLink

### Page Context Reference

| Page Path | User Intent |
|-----------|-------------|
| `/` | Overview, getting started |
| `/unified-review/` | Processing documents |
| `/unified-review/paperless/X/` | Reviewing specific document |
| `/processing-history/?tab=archive` | Finding processed items |
| `/processing-history/?tab=ai_queue` | Checking AI status |
| `/reconciliation/` | Linking to bank transactions |
| `/sync-assistant/` | Sharing entities |
| `/settings/` | Configuration help |

---

*SparkLink User Guide - Version 1.0*
*Last Updated: January 2026*

