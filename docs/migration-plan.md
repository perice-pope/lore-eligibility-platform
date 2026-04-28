# Migration & Delivery Plan

Plain English: **how we go from "current state" to this architecture without breaking
identity verification for existing members.**

---

## Principle: dual-run, reconcile, cut over

We do not replace a working system in one step. We run the old and the new in parallel,
compare their outputs continuously, and cut over only when the new system has matched or beaten
the old for a sustained period.

---

## Phase 0 — Discovery & alignment (weeks 1–2)

**Goal:** ground every later decision in the actual current state, not assumptions.

- Read every line of every existing ingest script. Document them.
- Interview each partner success manager about pain points.
- Pull 30 days of "member couldn't sign up" support tickets — categorize root cause.
- Get sample files from all current partners; redact and store in a test corpus.
- Inventory all consumers of the current eligibility table — who reads it, what queries, expected SLAs.
- Compliance + Security review of the proposed architecture; get sign-off on the PII model.

**Deliverable:** a one-page current-state diagram + risk register + signed architecture review.

**Exit criteria:** the team agrees on what we're building, what's out of scope, and who's accountable.

---

## Phase 1 — Foundation (weeks 3–6)

**Goal:** the infra plumbing exists; one synthetic partner can flow end-to-end.

- Stand up AWS accounts (prod, staging, dev) with org SCPs and Control Tower.
- Terraform: VPC, KMS keys, S3 raw buckets, Macie, CloudTrail org-wide, GuardDuty.
- Snowflake account + Skyflow vault provisioned.
- Dagster Cloud connected with hybrid agent on EKS.
- One **synthetic partner** flows: SFTP → bronze → silver → gold → Aurora → IDV API → "verified".
- Soda + dbt skeletons in place.
- Datadog dashboards skeleton.
- BAAs signed with all subprocessors.

**Risk:** scope creep. Don't try to onboard a real partner this phase.

**Deliverable:** end-to-end demo for leadership using synthetic data. Architecture review #2.

---

## Phase 2 — Pilot partner, parallel run (weeks 7–12)

**Goal:** one real partner is fully ingested through the new pipeline alongside the old. Outputs
are reconciled daily.

- Pick the **simplest** partner first — single-format CSV, modest volume, friendly DPO.
- Build their data contract via the AI schema-inference flow; engineer reviews and approves.
- Run new pipeline alongside old. Both populate their respective golden record stores.
- Build a **reconciliation report** that runs nightly:
  - Records in old not in new (and why)
  - Records in new not in old (and why)
  - Records in both with field-level diffs
  - Match-decision agreement rate
- IDV API still serves from the old store. New store is **shadow** — every request also queries
  it, results compared, diffs logged, but old result returned.
- Acceptance: 99.5% record agreement, 99.5% IDV decision agreement for 14 consecutive days.

**Risk:** the new pipeline disagrees and we don't know who's right. Mitigation: every disagreement
gets a manual review case until we've burned down to <50 disagreements per day, then sample.

---

## Phase 3 — Cut-over for pilot, expand to 3-5 partners (weeks 13–20)

**Goal:** new system is the source of truth for IDV for the pilot partner; the next wave of
partners is onboarded.

- Flip IDV for pilot partner: new store is primary, old is shadow (inverted).
- Watch SLOs daily for 2 weeks; rollback toggle ready.
- Onboard 3–5 more partners through the AI-assisted contract flow.
- Add CDC for any partner with database access.
- Build first cut of partner data quality scorecards.
- First quarterly compliance audit dry-run.

**Risk:** an unexpected edge case in production. Mitigation: feature flag per partner so we can
flip individual partners back to old without affecting others.

---

## Phase 4 — Full migration (weeks 21–32)

**Goal:** all partners migrated; old pipeline decommissioned.

- Onboard remaining partners in waves of 5, prioritized by complexity and willingness.
- For each wave: dual-run for 2 weeks, cut over, monitor for 2 weeks, decommission.
- Migrate historical data: backfill from old pipeline's archives into bronze, replay through silver
  and gold, reconcile with current state.
- Stand up the **AI assist tools** for ongoing use:
  - Schema-inference for new partners
  - Embedding-based entity resolution in production
  - LLM-assisted data quality alerts (anomaly explanations)
- Decommission old pipeline infrastructure. Archive code and docs to a "predecessor" repo.

**Deliverable:** sunset notice for old system; final reconciliation showing data parity.

---

## Phase 5 — Hardening & optimization (weeks 33–40)

**Goal:** we're not just "live", we're sustainably operable.

- Tune dbt models: bring p95 build under 30 min.
- Tune Aurora: connection pooling, cache warming, query plans.
- Tune Bedrock cost: prompt-engineering, candidate filtering before adjudication, model tier selection.
- Build automated chaos tests: kill an AZ, kill Skyflow, induce CDC lag — confirm degradation modes match docs.
- Build the on-call runbook from the incidents we've actually had, not the ones we predicted.
- Quarterly compliance audit (SOC 2 + HIPAA) — first real audit on the new system.

---

## Phase 6 — Continuous improvement (ongoing)

This isn't really a phase, it's the steady state. Some things that stay on the radar:

- **Cost** — Bedrock and Snowflake compute are the top two line items; revisit quarterly.
- **Match quality** — sample audits monthly; retrain entity-res ML if performance drifts.
- **Partner experience** — quarterly survey to PSMs; what's annoying about onboarding?
- **Compliance** — regulatory changes (state privacy laws move fast).
- **Tech debt** — at least 20% of each sprint goes to debt and reliability, every sprint. No exceptions.

---

## Risk register (living)

| Risk | Mitigation | Owner |
|---|---|---|
| A live IDV regression breaks sign-ups during cutover | Feature flag, blue/green per-partner, fast rollback | Eng lead |
| AI schema inference makes a wrong PII classification | Always human-reviewed; Macie scans bronze | Compliance + Eng |
| Skyflow vendor lock-in worsens over time | Abstract behind our own tokenization interface; quarterly portability check | Eng lead |
| Snowflake cost overruns | Resource monitors with hard credit caps; query tagging; weekly review | Data lead |
| Partner formats drift silently | Soda freshness + schema drift checks; auto-quarantine | Data lead |
| Engineer leaves with deep tribal knowledge | Pair programming, written runbooks, quarterly knowledge-share rotation | Eng manager |

---

## Decision log (illustrative — examples from this design)

| # | Decision | Date | Reasoning |
|---|---|---|---|
| 001 | Snowflake over Redshift for curated layer | Wk 1 | Compute/storage separation, dynamic masking, data sharing |
| 002 | Skyflow over self-built vault | Wk 1 | Buy not build; SOC 2 + HIPAA out of the box |
| 003 | Iceberg over Delta | Wk 2 | Engine pluralism, no Databricks lock-in |
| 004 | Dagster over Airflow | Wk 2 | Asset-aware orchestration; native data contracts |
| 005 | Bedrock over OpenAI for AI features | Wk 2 | HIPAA, no egress, model swap flexibility |
| 006 | Hybrid (deterministic + ML) entity res over pure ML | Wk 3 | Auditability for HIPAA; ML for the long tail |
| 007 | Per-partner CMK | Wk 3 | Cryptographic shredding on offboarding |

The decision log is its own tracked artifact. Reversing a decision requires the same rigor as
making one.
