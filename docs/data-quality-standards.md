# Data Quality Standards

Plain English: **what makes an eligibility record "good enough" to trust for identity verification**,
who is responsible when it isn't, and what we measure.

---

## The six dimensions we measure

| Dimension | Question it answers | Example check |
|---|---|---|
| **Completeness** | Are required fields present? | `not_null(last_name, dob)` |
| **Validity** | Does the value conform to format? | `dob` is a valid date in 1900–today; `ssn` matches `^\d{3}-?\d{2}-?\d{4}$` |
| **Uniqueness** | Is the entity represented once? | No two rows in `silver.eligibility` share `(partner_id, partner_member_id)` |
| **Consistency** | Do related fields agree? | `state` is a valid US state for the given `zip`; `dob` not after `effective_start_date` |
| **Timeliness** | Is the data fresh enough? | Partner file landed within expected SLA (e.g., daily by 06:00 ET) |
| **Accuracy** | Does the value match reality? | Address USPS-validates; SSN passes the SSA "death master" reasonableness check |

Accuracy is the hardest. We approximate it with proxy signals: USPS validation rate, SSN format
validity, age plausibility (no DOB > 110 years ago), and downstream match rate.

---

## Quality gates by layer

| Layer | Gate | Action on fail |
|---|---|---|
| **Bronze** | Schema-on-read parses successfully | Quarantine bad records to `bronze.<partner>_quarantine`; partner alerted if quarantine rate > 1% |
| **Bronze → Silver** | Soda checks (completeness, validity, freshness) | **Block** silver build; page on-call if >5% rows fail |
| **Silver → Gold** | Soda checks (uniqueness, consistency, entity-res success rate) | **Block** gold build; do not promote to Aurora |
| **Gold → Aurora** | Reconciliation: row count diff < 0.1% from prior version | **Block** Aurora refresh; investigate before resuming |
| **Aurora → IDV API** | Continuous: latency, error rate, match precision | Page on-call; auto-rollback if precision drops > 1pp in 5min |

---

## Per-field standards

These are the hard requirements. Implementation lives in
[pipelines/soda/checks.yml](../pipelines/soda/checks.yml) and dbt tests.

### `partner_member_id`
- **Required, not null.**
- Unique within `partner_id`.
- Treat as opaque string; do not parse.

### `first_name`, `last_name`
- **Required, not null** at silver+.
- Stripped of leading/trailing whitespace.
- Title-cased with respect to "McDonald", "O'Brien", multi-part names.
- Diacritics preserved (no ASCII-fold) — important for accuracy.
- Length 1–100 chars; flag suspiciously short (`X`, `..`).

### `dob` (date of birth)
- **Required, not null.**
- Stored as ISO-8601 `DATE`.
- Range: 1900-01-01 to (today - 1 day).
- If parsed from ambiguous string (`02/03/1962`), partner contract specifies date format
  (`MM/DD/YYYY` vs `DD/MM/YYYY`) explicitly. Default rejects ambiguous.

### `ssn` (Social Security Number)
- **Optional but preferred.**
- Tokenized via Skyflow at silver layer; raw value never written to S3 silver or Snowflake.
- Validation runs *before* tokenization in the cleansing job:
  - Format: `^\d{3}-?\d{2}-?\d{4}$`
  - Not in invalid SSA ranges (`000-XX-XXXX`, `666-XX-XXXX`, `9XX-XX-XXXX`).
  - Not a known fake (`123-45-6789`, `111-11-1111`).
- We store the **last 4** in plaintext for human-readable identification (industry-standard);
  full SSN is token-only.

### `address_line_1`, `city`, `state`, `zip`
- **Required for postal-mail-eligible programs**, optional otherwise.
- USPS-validated via Lob or similar at silver layer.
- `state`: 2-letter USPS abbreviation; reject full-name forms ("California" → "CA" with warning).
- `zip`: 5-digit primary; `zip4` separate column.
- Address normalization is lossy — we **keep the original string** alongside the normalized one.

### `email`
- Optional.
- Validated via RFC 5322 + DNS MX lookup at silver layer.
- Stored lowercased for matching.
- Tokenized in PII vault; matching uses a deterministic HMAC token for join keys.

### `phone`
- Optional.
- E.164 normalized.
- Tokenized.

### `effective_start_date`, `effective_end_date`
- Date range during which the member is eligible.
- `effective_start_date` required, not null.
- `effective_end_date` null = currently active.
- `effective_start_date` ≤ `effective_end_date` when both set.
- We do **not** delete records when a member becomes ineligible — we set
  `effective_end_date`. Audit trail and back-dated reactivation rely on this.

---

## Ownership — who fixes what

Quality issues fall into three buckets, each with a different owner:

1. **Source data is wrong** (partner sent garbage). Owner: Partner Success Manager. The data
   platform's job is to **detect and quarantine fast** so the PSM has time to push back on
   the partner before it impacts members.
2. **Our parser misread good data.** Owner: Data Platform squad. Hot fix in the data contract
   or cleansing job; backfill affected partition.
3. **Our golden record logic merged or split incorrectly.** Owner: Data Platform squad + Compliance.
   Compliance involved because every wrong-merge is a near-miss breach.

Each Soda check is annotated with `owner: …` and `severity: …`. When a check fires, the
right team is paged automatically via PagerDuty service routing.

---

## Continuous quality measurement

We publish a **Quality Scorecard** per partner, weekly, surfacing:

- % rows passing all silver-layer gates
- % records that resolved to a golden record (entity-res success rate)
- Median freshness (file land time vs. expected)
- Open data quality incidents (count, age)
- Trend lines vs. prior 4 weeks

Scorecard lives in the [Dagster Insights UI](https://docs.dagster.io/concepts/dagster-insights)
and is mailed to PSMs and engineering leads every Monday.

The scorecard is not punitive. It is a **shared visibility tool** so a partner's data steward
and our PSM can have grounded conversations about "your data quality dropped 8% last week."

---

## What we do NOT do

- **We do not silently fix data.** A row with `state="Califonria"` (typo) is *quarantined and
  flagged*, not auto-corrected to `CA`. Auto-correction creates the illusion of quality and hides
  partner data drift.
- **We do not exclude rows from quality metrics because they're "expected" to fail.** If we
  start carving out exceptions, the metric loses meaning.
- **We do not hand-edit silver tables.** All fixes are made in the cleansing rules or contracts,
  committed to git, replayed.
