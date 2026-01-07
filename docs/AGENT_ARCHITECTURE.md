SPARK AGENT ARCHITECTURE & IMPLEMENTATION CONTRACT

This file is for AI agents ONLY.
It defines non-negotiable architectural, security, and process constraints for implementing Spark (LedgerBridge → Spark).

Any change that violates this file is a defect, even if the software “works”.

Conflict Resolution Rule (Binding)

If documents conflict, resolve in this exact order:

SPARK_EVALUATION_REPORT.md is authoritative for scope, decisions, semantics, acceptance criteria, and what Spark is supposed to become.

SPARK_IMPLEMENTATION_PLAN.md is authoritative for execution structure and sequencing (phases/tasks order, milestones), but must not contradict scope/decisions in the evaluation report.

This document is authoritative for engineering discipline and implementation constraints (SSOT, security, gates, determinism, testing, auditability).

If ambiguity exists:

Reconcile conservatively.

Document assumptions and trade-offs in the final implementation report.

Do not silently “choose a direction.” If you must choose, record the decision and rationale explicitly.

0. AGENT PRE-FLIGHT CHECKLIST (MANDATORY)

Read this section before writing or changing any code.

Before implementing any change, you MUST confirm:

I read SPARK_EVALUATION_REPORT.md fully and extracted the relevant phase + acceptance criteria

I read SPARK_IMPLEMENTATION_PLAN.md and mapped it to repo tasks without contradicting the evaluation report

I identified which parts of the existing LedgerBridge codebase are affected (modules, schemas, state store, UI, CLI)

I identified SSOT modules and ensured I will not duplicate shared values (Section 4)

I identified required tests (unit/integration/E2E) and wrote at least one failing test if fixing a bug

I identified which documentation must change (README FIRST; evaluation report if decisions changed)

I confirmed privacy/security invariants are preserved (Section 7)

I confirmed dedupe and linkage semantics (external_id markers, “unlinked” definition) match the evaluation report

I confirmed LLM is optional + opt-out works (global and per-document) and audit trails are recorded

I established a baseline by running gates (Section 12) before touching behavior

If any item cannot be confirmed, implementation must not proceed until it is addressed or explicitly logged as an assumption.

1. PURPOSE OF THE SOFTWARE

Spark is a financial interpretation and reconciliation system that:

Uses Paperless-ngx documents (receipts/invoices) and Firefly III transactions as dual context sources

Produces deterministic, auditable interpretation outputs:

receipt ↔ transaction matching

categorization proposals

optional line-item / split proposals (where in scope)

Maintains correctness, traceability, and dedupe guarantees:

no silent double booking

no hidden writes

no unaudited AI decisions

Spark is NOT:

a “magic autopilot bookkeeping system”

a black-box LLM pipeline

a place where “close enough” is acceptable for amounts, dates, identities, or dedupe markers

2. PLATFORM & DEPLOYMENT CONTRACT
2.1 Primary Target

Spark runs on a Linux server (Ubuntu Server on Acer Aspire V / i5 / 16GB RAM), typically containerized.

2.2 Portability Rule

Even if Linux-first, implementation MUST remain:

container-friendly (no host-specific assumptions)

reproducible (pinned deps, deterministic runs)

environment-explicit (no “works on my machine”)

2.3 External Dependency Discipline

Spark depends on:

Firefly III API

Paperless-ngx API

Optional: Ollama local API

All external dependencies must be:

health-checked

version-checked where possible

failures must be loud and classified (no silent degradation)

3. CORE DESIGN PRINCIPLES
3.1 Boring Reliability

Failure must be explicit and explainable.

“Success” must reflect actual state (especially Firefly writes and linkage markers).

3.2 Determinism First, AI Second

Deterministic rules and matching logic are the backbone.

LLM is advisory only (optional), and must never become the authority.

3.3 Auditability Everywhere

Every interpretation run must be recorded.

Every write action must be traceable: what was written, where, why.

3.4 Human-in-the-loop Honesty

Spark must clearly distinguish:

AUTO (green)

REVIEW (yellow)

MANUAL (red)

A human must be able to override any suggestion.

4. SINGLE SOURCE OF TRUTH (SSOT) (NON-NEGOTIABLE)
4.1 Core Rule

Every shared value must be defined exactly once:

config keys

marker strings / linkage prefixes

thresholds

review states

enum values

schema field names

audit table column names

No duplicated literals across modules.

4.2 Required SSOT Modules (Spark)

Spark MUST have (or keep) authoritative modules, e.g.:

core/version.py (Spark version, pipeline version)

core/constants.py (ConfigKeys, Defaults, LinkageMarkers, ReviewStates, FireflyFields)

core/limits.py (timeouts, retries, thresholds)

core/paths.py (if any filesystem paths exist; prefer Path objects)

core/modes.py (if modes exist: LLM enabled/disabled, decision sources, etc.)

If LedgerBridge already has equivalents: reuse, don’t fork. If it doesn’t: create them once and centralize.

5. PATH HANDLING CONTRACT (ABSOLUTE RULE)
5.1 Zero String Paths

Forbidden:

hard-coded paths

string concatenation for paths

f-strings for paths

Required:

pathlib.Path internally

Path → str only at I/O boundaries

5.2 Canonical Path Source

All filesystem locations must be defined in one place (SSOT paths module) if Spark uses any local directories (cache, bank export dropbox, etc.).

6. DATA MODEL & SEMANTICS (BINDING)
6.1 Firefly is SSOT for Transactions and Categories

Firefly is the ledger system of record.

Categories must be fetched from Firefly unless explicitly overridden by design.

6.2 Linkage Markers are Policy

Spark must implement a stable linkage marker strategy consistent with the evaluation report:

Spark/Legacy marker prefixes (e.g., paperless: / future spark:v1:)

“unlinked” means: not linked by Spark markers (not merely external_id IS NULL)

These marker strings must be SSOT constants.

6.3 No Double Booking Invariant

Spark MUST prevent duplicates by design:

If a matching Firefly transaction exists (by linkage marker or deterministic hash strategy), Spark must update/link, not create a new one.

Creating a new Firefly transaction is allowed only when:

user explicitly requests it (e.g., manual cash entry), OR

it is unambiguously missing and the user confirms creation

Spark must never auto-create “bank-origin” transactions when bank import is done via Firefly Data Importer (v1 scope), unless explicitly in a post-v1 path.

6.4 Amount/Date Truth

All computed totals must match:

Firefly transaction amount (if matched)

receipt amount (if receipt)

If splits exist, sum(splits) must equal total, always, and this must be validated at save time.

7. SECURITY, PRIVACY, AND DATA MINIMIZATION (MANDATORY)

Spark handles financial documents and bank transaction metadata. Treat it as sensitive.

7.1 Must-Nots

Agents MUST NOT:

log secrets, API tokens, or full raw OCR dumps by default

send sensitive content outside the machine (no remote LLM calls unless explicitly authorized)

“helpfully” broaden scope into tax advice or external reporting systems without explicit request

weaken dedupe/matching integrity “for convenience”

7.2 LLM Privacy Contract

If LLM is enabled:

Minimize prompt input (keywords, normalized fields, not raw document dumps)

Redact IBAN/account numbers, references, identifiers where feasible

Store only the minimal response + metadata needed for audit (prompt version, model id, outcome)

Provide opt-out:

global opt-out

per-document opt-out

8. LLM INTEGRATION CONTRACT (OPTIONAL, NEVER AUTHORITATIVE)
8.1 Authority Rule

LLM output is advisory only:

It may propose categories (and optionally splits if in scope)

It must be validated against allowed category set

It must not trigger Firefly writes without deterministic confirmation rules and/or user approval

8.2 Runtime Rule

LLM must not run in the request path:

Must run in a worker/background job

UI must remain responsive

LLM failures degrade to review-required, not to “best guess”

8.3 Audit Rule

Every run that touches LLM must record:

model name

prompt version

whether fallback model was used

validation success/failure

final decision source (RULES / LLM / HYBRID / USER)

9. REVIEW UI & WORKFLOW CONTRACT
9.1 Unified Review Goal

Spark must converge on a workflow where:

every Paperless document yields a Spark review object (unless confidently auto-resolved)

every unlinked Firefly transaction yields a Spark review object (unless confidently auto-categorized)

review supports:

confirm match

reject match

manual match selection

manual entry creation (e.g., cash without receipt)

optional splits (create/edit/remove line items)

9.2 Traffic Light UX Must Be Honest

Green = safe to auto-apply (but still reviewable)

Yellow = review recommended

Red = review required

No false “green” unless mitigations are applied (calibration period, conflict detection, feedback loop)

9.3 Rescheduling Is Mandatory

Any object must be reschedulable for interpretation:

“re-run interpretation” button

reason logging

keeps history (no destructive overwrite)

10. STATE STORE & MIGRATION CONTRACT
10.1 Migrations Must Be Versioned

Schema changes must be:

versioned

reversible where feasible

tested

10.2 Audit Tables are First-Class

Interpretation history is a product feature, not debug noise:

create and maintain interpretation_runs (or equivalent)

index for document_id / firefly_id

never “clean up history” without explicit policy and export path

11. DOCUMENTATION CONTRACT
11.1 README First

Any user-visible behavior change without updating README is a defect.

11.2 Spark Reports are Living Scope

If implementation deviates from SPARK_EVALUATION_REPORT.md or SPARK_IMPLEMENTATION_PLAN.md:

the agent must either:

update the relevant document(s) (with rationale), OR

explicitly document why the deviation is rejected

No silent scope drift.

12. TESTING & GATES (MANDATORY)
12.1 Required Standards

Spark code MUST comply with:

black formatting

isort import ordering

type hints (enforce via mypy or equivalent if present; otherwise add incrementally)

deterministic tests and fixtures

12.2 Test Categories (Required)

Unit tests: matching, dedupe, schema validation, redaction, LLM JSON parsing

Integration tests: Firefly client interactions (mocked HTTP), Paperless client (mocked), state store migrations

E2E tests: minimal “receipt + bank txn match → update/link → audit record” flow

12.3 Gate Order (Binding)

Before claiming completion:

Policy / governance scripts (if repo has them)

Unit tests (fail-fast)

Integration tests

Formatting checks (black/isort check-only)

E2E tests (or deterministic verification steps if E2E is impossible)

If any gate cannot be executed:

provide deterministic verification steps

mark work NOT COMPLETE

13. BUG-FIX DISCIPLINE & RELATED-ISSUE SWEEP (BINDING)

When fixing any bug:

reproduce or write failing test first (when feasible)

make smallest root-cause fix

do a related-issue sweep:

other call sites, variants, similar patterns

ensure no “same bug elsewhere”

No try-and-revert churn.

14. PROGRESS REPORTING (MANDATORY)

Agents must maintain evidence-based progress, not vibes.

During implementation:

keep a running “Implementation Log” (can be a markdown doc in reports/ or docs/)

record:

gates executed and results

migrations applied

key design decisions and why

any deviations from the evaluation report / implementation plan

15. FINAL OUTPUT REQUIREMENT (NON-NEGOTIABLE)

At the end of the implementation, the agent must produce an exceptional final report as a new document, e.g.:

SPARK_IMPLEMENTATION_REPORT.md

It must include:

scope implemented (mapped to evaluation report phases)

files changed + why

migration details

test evidence (commands + PASS)

how dedupe and “unlinked” semantics were implemented

how LLM opt-out, audit trail, rescheduling were implemented

known limitations (if any) and why they remain

16. FINAL DIRECTIVE TO AI AGENTS

If you are unsure whether a change is allowed:

assume it is NOT

stop and document the ambiguity

reconcile using the conflict resolution rule at the top

Completion authority:

the agent has no authority to waive gates

the agent has no authority to weaken dedupe integrity

the agent has no authority to silently expand scope beyond the evaluation report / implementation plan

This file is binding.