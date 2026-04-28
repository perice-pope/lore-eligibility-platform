# Architecture & Rationale

This document explains the *why* behind every component. The intended reader is a fellow staff
engineer, but every section opens with a plain-English summary so a PM or partner stakeholder
can follow.

---

## 1. Architecture principles

These principles are the lens I use to evaluate every design decision. They are listed in priority
order — when two principles conflict, the higher one wins.

1. **Patient privacy is non-negotiable.** Lore is HIPAA-regulated. PII never leaves the secure
   plane in plaintext. Tokens travel; raw values stay vaulted. Every access is logged.
2. **The eligibility record is the source of truth for identity.** A wrong match is a breach;
   a missed match is a member who can't use the product. Both are P0 incidents.
3. **Data contracts are the API.** Partners change formats, fields disappear, new fields appear.
   We absorb chaos at the edge so downstream consumers see one stable schema.
4. **Open formats over proprietary.** Iceberg, Parquet, Avro, OpenLineage. We must be able to
   walk away from any vendor inside 90 days.
5. **Idempotent, replayable, observable.** Every step can be re-run with the same inputs and
   produce the same outputs. Every step emits a metric and a structured log.
6. **Bias toward managed services.** Lore is not big enough to run our own Kafka cluster well.
   Use MSK, Aurora, Snowflake — pay the premium, sleep at night.
7. **AI is augmentation, not autopilot.** A human approves every new partner contract and reviews
   every entity-resolution decision below the high-confidence threshold.

---

## 2. The current state (assumed)

I do not have access to Lore's actual environment, so I'm describing the *typical* state of an
early-stage health-tech company that's growing into the partner-eligibility problem:

- Partner files arrive in inboxes and Slack channels; an analyst reformats them in spreadsheets.
- Some partners have a Python script in a Docker container running on EC2; others go through a
  vendor like Stedi or Change Healthcare for EDI.
- A monolithic Postgres in RDS holds the eligibility table; new partners require migrations and
  bespoke ingest scripts.
- PII is encrypted at rest with default RDS encryption, but otherwise unrestricted within the app
  database.
- Identity verification is a SQL `WHERE last_name = ? AND dob = ?` query. Match rate is unmeasured.
- No data contracts. No automated PII classification. No CDC; partners overwrite full files nightly.

If the actual state is different, the architecture below adapts — but the *direction* of travel
holds.

---

## 3. The future-state architecture

### 3.1 Logical pipeline

```
[Partners] → [Landing Zone] → [Staging / Bronze] → [Cleansed / Silver] → [Curated / Gold]
                                                                                ↓
                                                                  [Golden Record (Aurora)]
                                                                                ↓
                                                                  [Identity Verification API]
                                                                                ↓
                                                                       [Member sign-up]
```

- **Landing zone** = exactly-as-received files in S3, immutable, KMS-encrypted, retention 7 years
  (HIPAA). One bucket per partner with separate KMS keys for blast-radius isolation.
- **Bronze** = the same data parsed into Iceberg tables, schema-on-read, no transformations beyond
  decoding the file format. Goal: replayability.
- **Silver** = cleansed and standardized. Names normalized, addresses USPS-validated, DOBs in
  ISO format, SSNs tokenized via Skyflow, deterministic + ML entity resolution applied. PII
  is here only as Skyflow tokens.
- **Gold** = the golden eligibility record per person, with provenance back to source rows. This
  is what dbt builds and Snowflake serves to BI.
- **Aurora golden record store** = a small, hot, online-OLTP copy of the gold table optimized for
  identity verification API reads. Refreshed continuously from Snowflake (sub-minute) via an
  outbox pattern.

### 3.2 Why three storage layers?

| Layer | Purpose | Format | Cost | Latency for read |
|---|---|---|---|---|
| Bronze (S3 + Iceberg) | Replay, recovery, audit | Iceberg/Parquet | $0.023/GB-month | 100s of ms (Athena) |
| Gold (Snowflake) | Analytics, dbt models, BI | Snowflake | $$ per query | 1-10s |
| Golden record (Aurora) | IDV API hot path | InnoDB / Postgres | $$ per hour | <10ms |

Putting Aurora in front of Snowflake is the move that lets us hit p99 < 150ms on the IDV API while
still using Snowflake for analytics. Snowflake is not a transactional database; treating it as one
is the #1 mistake teams make.

### 3.3 The two ingest paths — bulk and CDC

**Bulk** (initial historical load and partners who can only send full snapshots):

```
Partner → SFTP (AWS Transfer Family) → S3 raw bucket → EventBridge → Dagster sensor →
   ↓ schema inference (Bedrock) on first-ever file from this partner
   → EMR Serverless / AWS Glue Spark job (decode CSV/JSON/EDI 834) → Iceberg bronze table →
   → Soda quality checks → silver dbt models → entity resolution → gold dbt → Aurora outbox
```

EMR Serverless over Glue when individual files are >10GB; Glue otherwise. Both are warm-start
managed Spark — no cluster to babysit.

**CDC** (partners who give us database access — the dream state):

```
Partner DB → Debezium connector on MSK Connect → MSK Kafka topic (one per source table) →
   → Kafka Streams enrichment (PII tokenization at the edge) →
   → S3 sink connector → Iceberg bronze (continuous) →
   → Flink job (or Spark Structured Streaming) → silver → gold
```

CDC is preferred because it gives us:

- **Sub-minute freshness** on attrition and demographic changes
- **Row-level deletes** propagated correctly (a member who leaves an employer must lose access)
- **No reconciliation pain** vs. partners who send "full snapshots" with last week's data missing

If a partner can only do bulk, we accept it but actively work to upgrade them to CDC. The
contract should include an SLA for moving to CDC inside 12 months.

### 3.4 Schema inference (AI service #1)

This is the differentiator. When a new partner sends a file in a format we've never seen, we:

1. Take a stratified sample (first 50 + random 50 + last 50 rows).
2. Send it to Claude Sonnet on Bedrock with a structured prompt (see
   [services/schema_inference/prompts.py](../services/schema_inference/prompts.py)).
3. Claude returns a JSON object with: column → semantic field mapping, PII classification, suggested
   cleansing rules, and a confidence score per mapping.
4. We render this as a draft data contract YAML.
5. A data engineer reviews and approves, optionally modifying.
6. The approved contract is committed to git (`schemas/data_contracts/`) and the pipeline picks it up.

**Why this beats hand-coding parsers:**

| | Hand-coded parser | Bedrock-assisted contract |
|---|---|---|
| Time-to-onboard | 3-5 engineer days | 30-60 min review |
| Drift detection | Breaks silently | Re-run inference flags drift |
| PII coverage | Engineer-dependent | Systematic |
| Auditability | Code in git | Contract YAML in git, signed by reviewer |

**Why this is safe:** the LLM never writes to production. Its output is a *proposal* that a
human approves. We measure inference accuracy on a labeled holdout set monthly and gate model
upgrades behind a regression test.

### 3.5 Entity resolution (AI service #2)

The hard problem: two partners send a person; are they the same person?

**Stage 1 — deterministic blocking:**
- Exact match on (tokenized SSN) → instant resolve.
- Exact match on (DOB + last_name_soundex + first_name_soundex + zip3) → high-confidence candidate.

**Stage 2 — embedding retrieval:**
- For records that don't deterministically match, build a feature string like
  `"NAME: John Smith | DOB: 1962-04-12 | ADDR: 401 N Main St 90210"`
- Embed via **Bedrock Titan Text v2** (1024-dim).
- Query OpenSearch k-NN index for top 10 nearest neighbors.

**Stage 3 — LLM adjudication:**
- For each (incoming, candidate) pair with cosine sim > 0.85, ask Claude:
  *"Are these the same person? Consider name variations (Bob/Robert), nicknames, transposed
  digits in DOB, address moves. Respond JSON: {decision, confidence, reasoning}."*
- If decision == MATCH and confidence ≥ 0.95 → merge into existing golden record.
- If 0.80–0.95 → queue for human review (this is the magic — the LLM's *reasoning* is the
  human's onboarding ramp).
- Below 0.80 → create new golden record.

**Why hybrid beats pure ML or pure rules:**
- Pure deterministic: misses 15-30% of true matches due to typos, name changes, missing SSN.
- Pure ML: black-box, hard to audit, drifts silently, fails HIPAA scrutiny.
- Hybrid: deterministic handles the easy 70%, ML handles the medium 25%, human-in-the-loop
  handles the last 5%, and every decision has a reasoning trail.

### 3.6 PII vault — Skyflow over Macie + KMS alone

AWS gives us Macie (discovery), KMS (encryption), and Lake Formation (column-level access). For
PHI/PII at this scale, that's necessary but not sufficient. Skyflow adds:

- **Tokenization with format preservation** — applications can use a token in place of an SSN
  with the same shape (`123-45-6789`), including in foreign keys, without ever holding the real
  value. AWS doesn't have a native equivalent.
- **Polymorphic encryption** — search and partial reveal on encrypted data without decrypting.
- **Policy-as-code** for who can detokenize what, when, and for what purpose. Audit logs are
  built in.
- **Compliance certifications** — SOC 2 + HIPAA + PCI baked in; one BAA covers it.

Could we build it? Yes. Should we? Not at our stage. **Buy the vault, build the pipeline.**

### 3.7 Identity verification API design

```
                              ┌──────────────────┐
member sign-up form  ─POST→   │  IDV FastAPI     │   ←── reads ──── Aurora (golden record)
                              │  (ECS Fargate)   │   ←── reads ──── OpenSearch (vector index)
                              │                  │   ←── reads ──── Skyflow (token detokenize)
                              └──────┬───────────┘
                                     │
                                     ▼
                            allow / deny / step-up
```

**Read path is hot:** Aurora primary + 2 read replicas, connection-pooled via RDS Proxy. p99 under
150ms is achievable because we keep the working set in Aurora's buffer pool.

**Write path is async:** The IDV service does not write back to the eligibility tables. If sign-up
succeeds, it publishes a `MemberAccountCreated` event to EventBridge; downstream consumers
(account service, comms, etc.) react. This keeps the IDV path simple and fast.

**Failure modes:**

| Failure | Behavior |
|---|---|
| Aurora primary down | Read replica promoted automatically (90s); meanwhile reads degraded but functional. |
| Skyflow down | Verification falls back to non-SSN match path (slower, lower precision); we explicitly do *not* fail closed because that locks legitimate members out. Telemetry to incident response. |
| OpenSearch down | We skip the fuzzy fallback; deterministic-only matching. Match rate degrades ~5%. |
| Bedrock rate-limited | Adjudication queues to async DLQ; new accounts wait for human review. Brief degraded UX, no breach risk. |

**Availability target 99.95%** allows ~22 minutes/month downtime. Achievable with multi-AZ Aurora
+ ECS service spanning 3 AZs + active health-checked ALB.

---

## 4. Functional & non-functional requirements

### 4.1 Functional

- **F1.** Ingest CSV, JSON, X12 EDI 834, and database CDC streams from partners.
- **F2.** Apply per-partner data contract; reject files that don't conform with actionable error.
- **F3.** Cleanse and standardize PII fields per [data-quality-standards.md](data-quality-standards.md).
- **F4.** Tokenize all PII at silver layer; raw PII never enters Snowflake.
- **F5.** Resolve to a golden record using deterministic + ML matching.
- **F6.** Serve identity verification queries with sub-150ms p99 latency.
- **F7.** Propagate attrition (member leaves partner) within 90 seconds of partner CDC event.
- **F8.** Emit OpenLineage events for every transformation step.
- **F9.** Support full historical replay from any bronze date.

### 4.2 Non-functional

- **NF1. Compliance:** HIPAA, SOC 2 Type II, state privacy laws (CCPA/CPRA, NY SHIELD, Texas).
- **NF2. Encryption:** TLS 1.3 in transit; AES-256 at rest; KMS CMKs per partner.
- **NF3. Auditability:** Every PII access logged with user, purpose, timestamp; 7-year retention.
- **NF4. Latency:** IDV API p99 < 150ms; CDC end-to-end p95 < 90s; bulk ETL p95 < 15min.
- **NF5. Throughput:** ≥ 50M rows/hour bulk ingest; 10K events/sec sustained CDC.
- **NF6. Availability:** IDV API 99.95% monthly; data pipelines 99.9%.
- **NF7. Recoverability:** RPO 5 min for golden records; RTO 30 min for IDV API.
- **NF8. Observability:** Datadog dashboards per partner; SLO burn-rate alerts; OpenLineage graph.
- **NF9. Cost:** ≤ $X/month at 10M members; see [cost-estimate.md](cost-estimate.md).
- **NF10. Maintainability:** Two-pizza squad can own end-to-end; on-call rotation < 4 nightly pages/month.

---

## 5. Component-by-component justifications

### Why Snowflake over Redshift

- **Separation of compute and storage.** We can size warehouse for the IDV-feeding refresh job
  independently of the BI workload. Redshift's RA3 narrows the gap, but Snowflake's per-second
  billing and zero-copy cloning win for our dev/prod pattern.
- **Dynamic data masking and row access policies** are first-class. Redshift requires more glue.
- **Data sharing** lets us share a curated read-only view with a partner without copying data —
  useful when partners want eligibility back-feeds or reporting.
- **Snowpark** for the data-science team to iterate on entity-resolution features without leaving
  the warehouse.

### Why Iceberg over Hudi/Delta

- **Iceberg's metadata model** scales to billions of files cleanly. Hudi has known scale issues
  on metadata; Delta is fine but ties us tighter to Databricks tooling.
- **Engine pluralism** — Iceberg reads from Spark, Trino, Snowflake, Athena, DuckDB. Single
  source of truth, multiple engines, no copies.
- **Time travel and branching** are clean for replay and "what if" analyses.

### Why Dagster over Airflow / Step Functions / MWAA

- **Asset-aware** orchestration: pipelines are defined in terms of *what* the data is, not *what
  ran when*. This maps to data-product thinking — see Case Study 2.
- **Native data contracts and partitioning** primitives reduce custom code.
- **Lineage out of the box** via OpenLineage integration.
- **Type-checked Python** (vs. Airflow's stringly-typed DAGs).
- **Hybrid agent** model means our agent runs in our VPC; Dagster Cloud only sees metadata.
  HIPAA-friendly.

I like Airflow; I've shipped on it. For a greenfield staff-eng-led data platform in 2026,
Dagster is the better default.

### Why Debezium + MSK over AWS DMS

- **Granular CDC events with schema-aware payloads.** DMS gives row changes; Debezium gives the
  before/after image with strict typing via Schema Registry.
- **Replay** from the Kafka log is trivial; DMS replay is awkward.
- **Connector ecosystem** — partner-specific source connectors exist for nearly every DB.
- DMS is fine for one-time migrations; not for a long-running CDC backbone.

### Why Skyflow over building it on AWS

See §3.6 above. Buy the vault, build the pipeline.

### Why Bedrock over OpenAI / Anthropic API direct

- **Same VPC, no egress.** Critical for HIPAA — model traffic never leaves AWS.
- **One BAA already in place** at the AWS account level extends to Bedrock model access.
- **Model swap** — same SDK, swap Claude for Llama or Mistral if we need to rebalance cost vs.
  capability.
- **Provisioned throughput** when we need predictable latency for high-volume entity resolution.

---

## 6. The "what could go wrong" appendix

Things I lose sleep over and how I'd mitigate:

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Partner sends wrong file (e.g., last quarter's snapshot) | High | High | Soda freshness + row-count anomaly checks; quarantine on fail; partner alerted. |
| LLM schema-inference returns wrong PII classification | Medium | High | Always human-reviewed; Macie scan of bronze flags PII not declared in contract. |
| Entity resolution merges two distinct people | Medium | Severe (breach) | Conservative confidence threshold (0.95 auto, 0.80 review); audit every merge; reversible (we keep source records). |
| Skyflow outage during bulk load | Low | Medium | Tokenization queues to durable SQS; bulk load can lag but won't fail. |
| Bedrock rate limit during spike | Medium | Low | Provisioned throughput contracts for entity-res adjudication; degraded mode falls back to deterministic-only. |
| KMS key compromise (single partner) | Very low | Severe (per-partner) | Per-partner CMK; rotate; re-encrypt; CloudTrail every key use. |
| Insider threat — engineer queries raw PII | Low | Severe | Skyflow access policies require justification; all detokenize calls logged; quarterly access review. |
| Partner sends malicious payload (zip bomb, SQL injection in CSV) | Low | Medium | File-format validation in Lambda before EMR; size caps; sandboxed parsing. |
| Snowflake cost overrun | Medium | Medium | Resource monitors with hard credit caps; query tagging by team; weekly cost review. |
