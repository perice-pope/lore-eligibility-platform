---
marp: true
theme: default
paginate: true
size: 16:9
backgroundColor: "#0c2229"
color: "#e8eef0"
style: |
  section { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif; font-weight: 300; padding: 50px 60px; }
  h1, h2, h3 { color: #19c39e; font-weight: 600; letter-spacing: -0.02em; text-transform: none; }
  h1 { font-size: 2em; }
  h2 { font-size: 1.4em; margin-bottom: 0.5em; }
  strong { color: #ffffff; }
  em { color: #b8e6dc; font-style: italic; }
  table { font-size: 0.85em; border-collapse: collapse; }
  th { color: #b8e6dc; border-bottom: 1px solid #163b48; padding: 0.4em 0.8em; }
  td { padding: 0.35em 0.8em; border-bottom: 1px solid #1e323b; }
  blockquote { border-left: 3px solid #19c39e; padding: 0.3em 0.8em; color: #b8e6dc; font-style: italic; background: rgba(25,195,158,0.06); }
  code { color: #19c39e; }
  pre { background: #08171c; padding: 0.7em 1em; border-radius: 6px; font-size: 0.7em; }
  .title { text-align: center; }
  .title h1 { color: #fff; font-size: 2.6em; }
  .title .accent { color: #19c39e; text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.85em; }
  .stat { color: #19c39e; font-size: 2.4em; font-weight: 700; }
---

<!-- _class: title -->

# Partner Eligibility & Identity Verification

Case Study #3

**Perice Pope**

---

## Agenda · 45 minutes

| # | Section | Time |
|---|---|---|
| 1 | Context: Lore + chosen case study | 5 min |
| 2 | Architecture principles & target state | 7 min |
| 3 | Tech stack — AWS-first, with substitutions | 6 min |
| 4 | The two AI features (Bedrock) | 5 min |
| 5 | **Live demo** | 10 min |
| 6 | Hands-on artifacts (DDL, dbt, contracts) | 5 min |
| 7 | SLOs, migration plan, cost | 3 min |
| 8 | Wrap & questions | 4 min |

> Interrupt me anytime. I'd rather have a discussion than a monologue.

---

## What I learned about Lore

**HIPAA-regulated.** Medicare ACO context. Eligibility data is PHI from the moment it lands. Every architectural choice has to pass that filter.

**Partner-driven.** Employers, brokers, payers, ACOs send members in via eligibility feeds. Broken feed = no member to enroll = no product.

**AI-native culture.** Permission to use modern tools — Bedrock, embeddings — but only where they outperform classical approaches *and* the audit trail satisfies HIPAA.

> These three filters shaped *every* decision in the architecture.

---

## Why I picked Case Study #3

- **It's the source-of-truth problem.** New account creation depends on this. Can't match → member can't sign up. Wrong-match → data breach.

- **It's data quality *and* PII governance.** Most data engineering ducks one or the other. This case demands both.

- **It's where AI buys you something real.** Schema inference and entity resolution are two of the highest-ROI applications of LLMs in data engineering today — in a HIPAA-credible way.

---

## The five problems I'm actually solving

1. **Heterogeneous formats.** CSV, JSON, X12 EDI 834, sometimes a database CDC stream.
2. **Dirty data.** Typo'd names, three date formats, ZIPs missing leading zeros, SSNs with hyphens / spaces / placeholders.
3. **Same person across partners.** "Robert Smith" here, "Bob Smith" there. Resolve to one golden record without merging two distinct people. Wrong-merge = breach.
4. **Bulk + CDC.** Historical bulk load *and* continuous incremental updates must converge to the same end state.
5. **Hot-path SLO.** Identity verification at p99 < 150ms, 99.95% available.

Everything else falls out of solving these five.

---

## Architecture principles, in priority order

1. **Privacy is non-negotiable.** PII never leaves the secure plane in plaintext.
2. **Eligibility is the source of truth for identity.** Wrong match = breach; missed match = lost member. Both P0.
3. **Data contracts are the API.** Absorb partner chaos at the edge.
4. **Open formats over proprietary.** Iceberg, Parquet, Avro. Walk-away clause inside 90 days.
5. **Idempotent, replayable, observable.**
6. **Bias toward managed services.** We're not big enough to run our own Kafka well at 3am.
7. **AI is augmentation, not autopilot.** Human approves contracts; reviews uncertain matches.

> When two principles conflict, the higher one wins.

---

## Architecture — the happy path

```
  Partner SFTP / API / DB
            │
            ▼
  AWS Transfer Family · API GW · Debezium (CDC)        ── EventBridge ──▶ Dagster
            │                                                                ║ orchestrates
            ▼                                                                ║
  [ S3 RAW ]  ── per-partner KMS CMK                                         ║
            │                                                                ║
            ▼                                                                ║
  EMR Serverless / Glue  ◄── Skyflow Vault (PII tokenization)                ║
       │           │                                                         ║
       ▼           ▼                                                         ║
 [ BRONZE ]   [ SILVER — tokens-only Iceberg ]                               ║
                   │                                                         ║
                   ▼                                                         ║
   Entity Resolution (Bedrock embeddings + Claude adjudication)              ║
                   │                                                         ║
                   ▼                                                         ║
   [ GOLD — Snowflake, masked + RLS ]                                        ║
                   │  outbox relay (sub-minute)                              ║
                   ▼                                                         ║
   [ Aurora Postgres — hot replica ]                                         ║
                   │                                                         ║
                   ▼                                                         ║
   Identity Verification API (FastAPI on ECS Fargate) ───────────────────── ║
                   │
                   ▼
          Lore mobile app
```

Soda gates · OpenLineage lineage · Datadog observability · Terraform IaC

---

## The data flows through four homes

Each tier has one job. Mixing them up is the most common mistake teams make.

| # | Tier | What it holds | Tech |
|---|---|---|---|
| **1** | **Raw landing** | Exactly what the partner sent. Untouched. *"What did Acme send us last Tuesday?"* | S3 + KMS |
| **2** | **Bronze + Silver** | Parsed and cleansed. PII tokenized. Ready for analytics. | Iceberg on S3 |
| **3** | **Gold** | One golden record per person. Analytics warehouse reads from here. | Snowflake |
| **4** | **Hot replica** | What the sign-up app reads. **< 10 ms reads.** | Aurora Postgres |

> Why a separate hot tier? **Snowflake is built for big analytics queries, not for sub-second sign-up lookups.** Forcing the sign-up form to wait on Snowflake is the single most common mistake teams make.

---

## Two ingest paths — bulk and CDC

### Bulk
- Partners who can only send full snapshots
- SFTP (Transfer Family) → S3 → EMR Serverless → Iceberg bronze
- Scales to 50M rows/hour
- Reconciliation pain at scale

### CDC (preferred)
- Partners who give us DB access
- Debezium on MSK Connect → Kafka → silver via Spark Structured Streaming
- Sub-minute end-to-end freshness
- Row-level deletes propagate correctly

> Contract clause: *"You can start with bulk; we expect you on CDC inside 12 months."*

---

## What could go wrong (and what we do about it)

| Failure | Mitigation |
|---|---|
| Partner sends wrong file (last quarter's snapshot) | Soda freshness + row-count anomaly → quarantine; partner alerted |
| LLM schema-inference returns wrong PII tier | Always human-reviewed; Macie scans bronze for unflagged PII |
| Entity resolution merges two distinct people | Conservative threshold (0.95 auto, 0.80 review); reversible; audited |
| Skyflow outage during bulk load | Tokenization queues to durable SQS; bulk lags but doesn't fail |
| Bedrock rate-limit during spike | Provisioned throughput; degrade to deterministic-only |
| KMS key compromise (single partner) | Per-partner CMK; rotate; CloudTrail every key use |
| Insider threat — engineer queries raw PII | Skyflow access policies + two-person rule |

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Object storage | S3 + Iceberg | Open table format; engine pluralism |
| CDC | MSK + Debezium | Row-level CDC; replayable |
| Orchestration | **Dagster Cloud** | Asset-aware; native data contracts; lineage |
| Transformation | dbt on **Snowflake** | Compute/storage split; dynamic masking |
| PII vault | **Skyflow** | Format-preserving tokens; policy-as-code |
| Encryption | AWS KMS — *per-partner CMK* | Cryptographic shredding on offboarding |
| Entity res | Splink-style + Titan + Claude | Auditable; long tail handled |
| AI inference | **Amazon Bedrock** | HIPAA-friendly; no egress; model swap |
| IDV API | FastAPI on ECS + Aurora + OpenSearch | p99 < 150ms |
| Observability | **Datadog** + OpenLineage | Unified across SaaS + AWS |

---

## Snowflake over Redshift

- **Compute / storage separation.** Per-second billing; zero-copy cloning for dev/staging.
- **Dynamic masking + row access policies are first-class.** Three lines of SQL gives us per-role masking.
- **Data sharing.** Share a masked view back to a partner without copying.
- **Snowpark** for the data-science team to iterate in-warehouse.

```sql
CREATE MASKING POLICY mask_dob_to_year AS (val DATE) RETURNS DATE ->
  CASE WHEN CURRENT_ROLE() IN ('IDV_SERVICE_ROLE','COMPLIANCE_ROLE')
       THEN val
       ELSE DATE_FROM_PARTS(YEAR(val), 1, 1)
  END;

ALTER TABLE gold.eligibility_member
  MODIFY COLUMN dob SET MASKING POLICY mask_dob_to_year;
```

---

## Skyflow over self-built KMS-only

### What KMS + Macie give you
- Encryption at rest
- PII discovery scans
- Lake Formation column ACLs

*Necessary, not sufficient.*

### What Skyflow adds
- **Format-preserving tokenization** — apps use a token in place of an SSN, same shape, even in foreign keys
- **Polymorphic encryption** — search and partial reveal on encrypted data
- **Policy-as-code** — who detokenizes what, when, why; built-in audit
- **SOC 2 + HIPAA + PCI** baked in; one BAA

> Buy the vault. Build the pipeline. Don't get those backwards.

---

## Three more substitutions

### Dagster over Step Fns / MWAA
- Asset-aware orchestration
- Native data contracts; lineage out of the box
- Type-checked Python
- Hybrid agent in our VPC (HIPAA)

### Iceberg over Delta
- Engine pluralism (Spark, Trino, Snowflake, Athena, DuckDB)
- No Databricks lock-in
- Cleaner metadata model at billions of files

### Datadog over CloudWatch alone
- Unified metrics, logs, traces across SaaS + AWS + Snowflake + Skyflow
- SLO burn-rate alerts native

---

## Why Bedrock (vs. OpenAI direct)

- **Same VPC, no egress.** Critical for HIPAA — model traffic never leaves AWS.
- **One BAA** already in place at the AWS account level extends to Bedrock.
- **Model swap.** Same SDK; switch Claude → Llama → Mistral without rewrite.
- **Provisioned throughput** for predictable latency at high entity-res volume.

> If we were a non-healthcare startup, OpenAI would be fine. We're in healthcare. Bedrock is the right answer.

---

## AI feature #1 — Schema inference

**Without AI:** 3–5 engineer days · hand-coded parser · engineer-dependent PII coverage.

**With Bedrock + Claude Sonnet:**
1. Take a stratified sample (first 50 + random 50 + last 50 rows).
2. Send to Claude with a structured prompt — outputs JSON: column → canonical-field mapping, PII tier, cleansing rules, confidence per column.
3. Render as draft data-contract YAML.
4. Engineer reviews, edits, signs off, commits to git.
5. Pipeline picks up the contract automatically.

<span class="stat">5 days → <1 hour</span>

*Safe by design:* LLM never writes to production. Output is a proposal a human approves. Inference accuracy measured monthly on a labeled holdout.

---

## AI feature #2 — Entity resolution

| Stage | Mechanism | Handles |
|---|---|---|
| **1 · Deterministic** | Exact tokenized SSN, or DOB + soundex(name) + ZIP5 | ~70% — easy cases |
| **2 · Embedding retrieval** | Bedrock Titan embeddings → OpenSearch k-NN top-K | ~25% — typos, "Bob vs Robert" |
| **3 · LLM adjudication** | Claude scores each pair → JSON {decision, confidence, reasoning} | borderline + audit trail |

**Confidence ≥ 0.95** → auto-merge. **0.80–0.95** → human review queue. **< 0.80** → new golden record.

> Pure rules miss 15–30% of true matches. Pure ML fails a HIPAA audit.
> **Hybrid wins on every axis: precision, recall, auditability.**

---

<!-- _backgroundColor: "#0c2229" -->
<!-- _class: title -->

# ⏵ Live demo

10 minutes · runs locally · zero AWS access required

**Beats:**
1. Tests pass (30, sub-second)
2. Schema inference on a partner CSV → draft contract YAML
3. Entity resolution → 4 cases, 3 stages
4. IDV API → VERIFIED, INELIGIBLE, NOT_FOUND
5. Architecture in code → masking policy, Soda P0 check, cleansing SQL

---

## Who sees what — at the database

The same table, three different views — the database itself enforces it. Apps don't have to know.

| Role | What they see | Why |
|---|---|---|
| **Sign-up app** | Full DOB, ZIP, last-4 SSN | It needs them to verify identity |
| **Analyst / BI** | DOB year only · SSN columns return null | Cohort analysis works fine without precise PII |
| **Compliance** | Everything, every access logged | Auditable HIPAA trail |

> Three lines of SQL. Zero application changes. **The database is the security boundary, not the app.**

---

## Detecting "is this the same person?"

We never trust a single rule. We triangulate two signals.

| Signal | What it is | Strength |
|---|---|---|
| **Hard** | Same tokenized SSN | Rare and certain — the same person, every time |
| **Soft** | Same DOB + name-sound + ZIP-prefix | Catches typos, marriage names, "Bob vs Robert" — needs a second look |

**How decisions roll up:**
- Hard match → auto-merge
- Soft match alone → human review queue
- Neither → new record

> Pure rules miss 15–30% of real matches. Pure ML fails a HIPAA audit. **Two signals + a human-in-the-loop wins on every axis.**

---

## The data contract — a translator the team controls

Partners send messy data. The contract turns chaos into one stable shape downstream tools depend on.

```
   Acme HR system   →   [ Data contract ]   →    Our pipeline
   (CSV, MM/DD/YYYY)     One YAML in git          Stable shape
                         Versioned                ISO dates
                         Reviewed                 Tier-tagged PII
                         Signed off               Auto-quarantine rules
```

Each contract declares: **which fields are PII tier 1**, **required completeness**, **expected cadence**, **retention**, **auto-quarantine thresholds**.

> Partner changes their format? **Bump the contract version, regression-test, deploy.** No silent breakage. No mystery.

---

## The P0 guardrail — PII can never leak silently

**🚨 Severity: critical**

If a raw SSN ever appears in a column meant to hold only a token, the build fails, promotion to production stops, and on-call gets paged within seconds.

**Every promotion between layers passes through automated quality checks:**

```
Bronze (raw) → Cleansing job → ✓ Quality check → Silver (analytics-safe)
```

If any check fails:

```
Cleansing job → ✗ Quality check → Build halts · on-call paged · partner alerted
```

> This is what **"data contract enforced"** actually means in practice — not a doc, a gate.

If a raw SSN-shaped value *ever* appears in `ssn_token`, this fails the silver build, blocks promotion to gold, and pages on-call. **That's what "data contract enforced" looks like.**

---

## SLOs — what "good enough" means

| SLO | Target | Burn-rate alert |
|---|---|---|
| IDV API availability | 99.95% / 30d | Page on 14.4× burn @ 1h |
| IDV API p99 latency | < 150ms | Ticket on 30min > 250ms |
| CDC end-to-end freshness (p95) | < 90 sec | Incident on 4h breach |
| Match precision (sampled audit) | ≥ 99.5% | P0 — never trade off |

> **Error-budget policy is enforced.** When we burn 50% of any budget, non-critical feature work in that area halts until we're back inside.
> SLOs without enforcement are theater.

---

## Migration — six phases, ~9 months

| Phase | Weeks | Goal |
|---|---|---|
| **0 · Discovery** | 1–2 | Read code. Interview PSMs. Pull tickets. *Don't build yet.* |
| 1 · Foundation | 3–6 | Infra plumbing. Synthetic partner end-to-end. |
| 2 · Pilot dual-run | 7–12 | One real partner, new pipeline shadow-mode. Reconcile nightly. |
| 3 · Cut-over + expand | 13–20 | Pilot flips to new. Onboard 3–5 more. |
| 4 · Full migration | 21–32 | Waves of 5. Decommission old. |
| 5 · Hardening | 33–40 | Tune. Chaos test. SOC 2 dry-run. |
| 6 · Steady state | ongoing | 20% of every sprint to debt + reliability. |

**Two principles:** dual-run-then-cutover · simplest partner first.

---

## Cost — modeled at two scales

| Line | Launch · 1M members | Growth · 10M members |
|---|---|---|
| AWS infra (S3, MSK, EMR, Aurora, ECS, EKS, KMS, Macie) | ~$5,400/mo | ~$16,400/mo |
| Snowflake | $3,000/mo | $14,000/mo |
| Skyflow | $2,000/mo | $7,500/mo |
| Bedrock (entity res adjudication is the line) | $1,200/mo | $11,000/mo |
| Datadog · Dagster Cloud · misc | $3,900/mo | $14,100/mo |
| **Total** | **~$15,500/mo** | **~$63,000/mo** |
| **Per member per month** | **$0.0155** | **$0.0063** |

Bedrock cost levers if it diverges from forecast: switch most adjudications to **Haiku** (10× cheaper); pre-filter candidates more aggressively; cache embeddings.

---

## What I'd do differently in your real environment

1. **Talk to partners first.** Half this design is informed by guesses. Two weeks reading existing code and looking at five real partner files would change details.

2. **Build the migration plan with operations.** The interesting risk isn't building the new system. It's running both in parallel without breaking IDV for live members.

3. **Treat AI as augmentation, never a gate.** When Claude returns garbage, a human gets paged with enough context to fix it in ten minutes. The schema-inference output is *always* reviewed before promotion.

**First 30 days:** listening, reading, asking questions — not building.
**First 90 days:** the foundation phase from this plan, with the squad's input.
**First 12 months:** current state → target state with measurable improvements every quarter.

---

<!-- _backgroundColor: "#0c2229" -->
<!-- _class: title -->

# Thank you.

github.com/perice-pope/lore-eligibility-platform

**Questions?**
