# Panel Presentation — 45-Minute Speaker Notes

> CTO-level technical detail. Verbatim is OK; bullets are reminders, not a script.
> Italics are *delivery hints* (when to pause, what to emphasize, what slide to be on).

**Total time budget: 45 minutes**

| Section | Time | Slides |
|---|---|---|
| 1. Opening + agenda | 2 min | 1–2 |
| 2. Context: Lore + chosen case study | 5 min | 3–5 |
| 3. Architecture principles & target state | 7 min | 6–10 |
| 4. Tech stack — AWS-first, with substitutions | 6 min | 11–14 |
| 5. The two AI features | 5 min | 15–17 |
| 6. **Live demo** | 10 min | 18 |
| 7. Hands-on artifacts (DDL, dbt, contracts, governance) | 5 min | 19–22 |
| 8. SLOs, migration, cost | 3 min | 23–25 |
| 9. Wrap + what I'd do next + Q&A | 2 min | 26–27 |

---

## 1. Opening (Slides 1–2) — 2 min

*Stand up, shoulders back, eye contact. Smile. Don't apologize for anything.*

> "Thanks for having me. I'm going to walk you through Case Study 3 — partner eligibility ingestion and identity verification — and the working prototype I built for it.
>
> The repo is on my GitHub; I'll share the link at the end. About 30 unit tests pass green, the identity-verification API is runnable on this laptop with no AWS access required, and there's a Bedrock-powered AI demo I'll show you.
>
> Three things to know before we start. **One** — I'm presenting at *staff-engineer scale.* That means I'm designing for 100M eligibility records across hundreds of partners, not for a v1. **Two** — every choice I made has a non-AWS alternative I considered; I'll call those out as we go. **Three** — I made assumptions because I didn't have access to your real environment. I'll flag them. Tell me if I got any wrong; that's part of why I want this conversation."

*Click to agenda slide. Read the agenda quickly. Don't dwell.*

> "I'm leaving 5 minutes at the end for questions, but feel free to interrupt — I'd rather have a discussion than a monologue."

---

## 2. Context: Lore + chosen case study (Slides 3–5) — 5 min

### Slide 3 — What I learned about Lore (don't share specifics)

> "I read the 'Why We're Here' page. I won't quote it. What I took away — and what shaped every decision in this design — is three things.
>
> **One:** Lore is HIPAA-regulated. Medicare ACO. The partner-eligibility data we're talking about is PHI under HIPAA the moment it touches our infrastructure. Every architectural choice has to pass that filter.
>
> **Two:** Lore's business model is partner-driven. Employers, brokers, payers, ACOs send members in via eligibility feeds. If those feeds are broken, fragile, or slow, the whole product is broken — there's no member to enroll.
>
> **Three:** Lore's culture leans into AI. So I gave myself permission to use modern tools — Bedrock, embeddings — but only where they meaningfully outperform classical approaches *and* where the audit trail satisfies HIPAA. I'll show you both AI features."

### Slide 4 — Why Case Study 3

> "I picked Case Study 3 because it sits at the intersection of three things I find interesting and Lore presumably finds existentially important.
>
> **It's the source-of-truth problem.** New user account creation depends on this. If we can't match a sign-up to an eligibility record, the member can't use Lore. If we wrong-match, that's a data breach.
>
> **It's a data-quality and PII-governance problem at the same time.** Most data engineering ducks one or the other. This case demands both.
>
> **It's where AI buys you something real.** Schema inference and entity resolution are two of the highest-ROI applications of LLMs in data engineering today. I get to demonstrate that in a HIPAA-credible way."

### Slide 5 — The five problems I'm actually solving

*This is your "show I understand the brief" slide. Be confident.*

> "Underneath the high-level brief, here's what I'm actually solving.
>
> **One:** every partner sends data differently — CSV, JSON, X12 EDI 834, sometimes a database CDC stream. Field names are different, formats are inconsistent.
>
> **Two:** the data is dirty. Names with typos, dates in three different formats, ZIPs missing leading zeros, SSNs with hyphens, with spaces, with placeholder values like all-zeros.
>
> **Three:** the same person shows up in two partners' feeds, once as 'Robert Smith' once as 'Bob Smith'. We have to resolve them to one golden record without merging two distinct people. Wrong merge equals breach.
>
> **Four:** we have to do this both as a one-time historical bulk load *and* as a continuous near-real-time CDC stream. Both have to land in the same end state.
>
> **Five:** we have to serve identity-verification queries at sub-150-millisecond p99 latency, 99.95% available, while the offline pipeline is doing all of the above in the background.
>
> Everything else falls out of solving these five."

---

## 3. Architecture principles & target state (Slides 6–10) — 7 min

### Slide 6 — Principles in priority order

> "Architecture principles are the lens. When two principles conflict, the higher one wins. Here's mine, in priority order.
>
> **Privacy is non-negotiable.** PII never leaves the secure plane in plaintext. Period.
>
> **The eligibility record is the source of truth for identity.** A wrong match is a breach; a missed match is a member who can't use the product. Both are P0.
>
> **Data contracts are the API.** Partners change formats. We absorb chaos at the edge so downstream consumers see one stable schema.
>
> **Open formats over proprietary.** Iceberg, Parquet, Avro. We must be able to walk away from any vendor inside 90 days.
>
> **Idempotent, replayable, observable.** Every step can be re-run with the same inputs and produce the same outputs.
>
> **Bias toward managed services.** Lore is not big enough to operate Kafka well at 3am. Use MSK. Pay the premium. Sleep at night.
>
> **AI is augmentation, not autopilot.** A human approves every new partner contract and reviews every entity-resolution decision below the high-confidence threshold."

*Pause. Let it land.*

> "These principles disagree with what some other teams would build. Let's talk about why this set."

### Slide 7 — The architecture diagram

*Click to the big architecture diagram. Don't try to explain everything. Trace the happy path with your finger.*

> "Three layers. Five storage tiers. Two AI services on the side. Let me trace the happy path.
>
> A partner SFTPs a file. **AWS Transfer Family** is a managed SFTP server — no EC2 to babysit. The file lands in **S3**, encrypted with a **per-partner KMS key**. I'll come back to why per-partner.
>
> **EventBridge** fires. **Dagster** picks it up. If this is a new partner format we've never seen, **the AI schema-inference service** kicks in — that's AI feature one.
>
> A Spark job — **EMR Serverless** for the big files, AWS Glue otherwise — parses the file and writes to **Iceberg bronze**. Bronze is replay-grade fidelity to the source. We can rebuild silver and gold from bronze any time.
>
> The bronze-to-silver transformation tokenizes Tier-1 PII through **Skyflow**. After silver, raw SSNs and emails do not exist anywhere in our analytical plane. They live in the vault.
>
> Silver gets entity-resolved — that's AI feature two — and writes the resolution decision to a separate audit table.
>
> Silver becomes gold via **dbt on Snowflake**. Gold is the analytical golden record. From there, an **outbox table** continuously feeds **Aurora Postgres**, which is the hot replica that the **identity verification API on ECS Fargate** reads against.
>
> The IDV API's job is one thing: in under 150 milliseconds, tell the mobile app whether this sign-up is verified, ineligible, ambiguous, or not found.
>
> **Soda** gates every promotion between layers. **Datadog** observes. **OpenLineage** tracks data lineage end-to-end."

### Slide 8 — The three storage tiers

> "Most teams stop at one tier and conflate analytics and OLTP. That breaks at scale.
>
> **Bronze** — Iceberg on S3. Cheap. Replayable. 7-year retention with Object Lock for HIPAA. Athena and Spark and Trino all read it. Useful when an auditor asks: 'what exactly did the partner send us on April 28?'
>
> **Gold** — Snowflake. Analytical workloads, BI queries, dbt builds. Per-second billing. Dynamic data masking and row-access policies. Separation of compute and storage means we can spin a warehouse for the dbt build, kill it, and not pay between runs.
>
> **Aurora** — the hot replica. Postgres. Two read replicas. RDS Proxy in front for connection pooling. The IDV API only reads from here, never from Snowflake. *That's the design move that lets us hit p99 under 150ms while still using Snowflake for analytics.* Snowflake is not an OLTP database; treating it as one is the most common mistake teams make."

### Slide 9 — Two ingest paths

> "Bulk for partners who can only send full snapshots. CDC for partners who give us database access. Both end up in the same silver and gold; the path is just different upstream.
>
> **Bulk** is straightforward — parse a file, write Iceberg, run dbt. It scales to 50M rows per hour with EMR Serverless on a $200-a-month spot fleet.
>
> **CDC** is the dream state. **Debezium connectors** running on **MSK Connect** stream every row-level change from the partner DB into a Kafka topic. We get true row-level deletes. Sub-minute end-to-end freshness.
>
> The contract with new partners says: 'You can start with bulk, but we expect you on CDC inside 12 months.' That clause is on me — it's the right hill to die on, because bulk full-snapshot reconciliation pain is brutal at scale and CDC eliminates it."

### Slide 10 — Failure modes

*This is the slide that earns staff-engineer credibility.*

> "I won't read the whole thing. The point is: I've thought about every box on the diagram failing. The mitigations are on the right. Three I want to call out.
>
> **Skyflow goes down.** What does the IDV API do? It does *not* fail closed. Failing closed locks legitimate members out. It falls back to a non-SSN match path, lower precision, and pages the on-call. Telemetry to incident response. We choose graceful degradation over hard fail because the *member experience* is the higher-order concern.
>
> **Bedrock rate-limits us during a spike.** Adjudication queues to async. New ambiguous matches go to human review. No breach risk. Brief degraded UX.
>
> **Wrong merge during entity resolution.** This is the only one where I'm conservative to a fault. Auto-merge requires confidence ≥ 0.95. 0.80 to 0.95 goes to human review. Below 0.80 creates a new golden record. Every merge is reversible — we keep the source rows. Compliance is in the loop monthly on a sampled audit."

---

## 4. Tech stack — AWS-first, with substitutions (Slides 11–14) — 6 min

### Slide 11 — The stack at a glance

*Walk through the table briefly; don't dwell.*

### Slide 12 — Snowflake over Redshift

> "Three reasons.
>
> **One:** separation of compute and storage. We can size a warehouse for the dbt build, scale a separate warehouse for BI, and a third for the data-science team — all hitting the same data with no copies. RA3 narrows the gap, but Snowflake is still ahead on per-second billing and zero-copy cloning, which we'll use for dev/staging.
>
> **Two:** dynamic data masking and row access policies are first-class. I'm going to show you the masking policy SQL in a few slides — it's three lines, it's per-role, and it works without app-layer changes. Redshift requires more glue for the same thing.
>
> **Three:** data sharing. We'll want to share a curated, masked view back with partners for their reporting. Snowflake's data-sharing feature does this without copying data.
>
> Redshift is fine. Snowflake is better here."

### Slide 13 — Skyflow over self-built KMS-only

> "AWS gives us KMS for encryption, Macie for PII discovery, Lake Formation for column-level access. That's necessary but not sufficient for PHI at scale.
>
> Skyflow is a managed PII vault. **Tokenization with format preservation** — applications use a token in place of an SSN with the same shape, including in foreign keys. AWS doesn't have a native equivalent.
>
> **Polymorphic encryption** lets us search and partial-reveal on encrypted data without decrypting.
>
> **Policy-as-code** for who can detokenize what, when, why. Two-person rule for human detokenize calls.
>
> Could we build it? Yes. Should we? No. A staff engineer's job is to know when to *buy the vault and build the pipeline*, not the other way around. Skyflow has the SOC 2 + HIPAA + PCI certifications. We'd take 18 months to match that."

### Slide 14 — Three more substitutions

> "**Dagster over Step Functions, MWAA, or Airflow.** Asset-aware orchestration. Pipelines are defined in terms of *what* the data is, not *what ran when*. Native data contracts. Lineage out of the box. Type-checked Python. Hybrid agent runs in our VPC; Dagster Cloud only sees metadata, which is HIPAA-friendly.
>
> **Iceberg over Delta.** Engine pluralism. Iceberg reads from Spark, Trino, Snowflake, Athena, DuckDB. Delta is fine but pulls us closer to Databricks. I want to keep that option open without committing.
>
> **Datadog over CloudWatch alone.** Unified metrics, logs, traces. CloudWatch is fine for AWS-native; once you have services on Kubernetes, services on Snowflake, and a vendor like Skyflow, Datadog is where you actually correlate."

---

## 5. The two AI features (Slides 15–17) — 5 min

### Slide 15 — Why Bedrock

> "Before I show what these features do, why Bedrock and not OpenAI directly.
>
> **Same VPC, no egress.** Critical for HIPAA. Model traffic never leaves AWS. The BAA we already have at the AWS account level extends to Bedrock model access — one-line vendor risk review.
>
> **Model swap.** Same SDK; we can switch Claude for Llama or Mistral without rewriting anything. That hedges against vendor pricing and capability changes.
>
> **Provisioned throughput** when we need predictable latency for high-volume entity resolution.
>
> If we were a startup not in healthcare, OpenAI would be fine. We're in healthcare. Bedrock is the right answer."

### Slide 16 — Schema inference (AI feature 1)

*Click to the schema-inference flow diagram.*

> "The problem: a new partner sends us a file in a format we've never seen. Without AI, an engineer spends three to five days writing a parser, classifying which columns are PII, picking a date format, deciding if SSN gets tokenized.
>
> With AI: we send a 50-row sample to Claude on Bedrock with a structured prompt. Claude returns JSON with the column-to-canonical-field mapping, a PII tier per column, suggested cleansing rules, and a confidence score per mapping. We render that as a draft data-contract YAML.
>
> An engineer reviews it — usually under an hour — edits anything Claude got wrong, commits it to git. The pipeline picks up the contract.
>
> **Time-to-onboard a partner drops from a week to under an hour of human review.**
>
> Why this is safe: the LLM never writes to production. Its output is a *proposal* a human approves. We measure inference accuracy on a labeled holdout monthly and gate model upgrades behind a regression test."

### Slide 17 — Entity resolution (AI feature 2)

*Click to the three-stage diagram.*

> "Hard problem: two partners send a person; are they the same person?
>
> **Stage one — deterministic blocking.** Same tokenized SSN — instant resolve. Or same DOB plus name soundex plus ZIP — high-confidence candidate. This handles maybe 70% of cases.
>
> **Stage two — embedding retrieval.** For the rest, build a feature string like 'NAME: John Smith | DOB: 1962-04-12 | ADDR: 401 N Main 90210', embed via Bedrock Titan, query OpenSearch's k-NN index for the top 10 nearest neighbors. This is the magic for typos and 'Bob versus Robert.'
>
> **Stage three — LLM adjudication.** For each plausible candidate pair, ask Claude: 'Are these the same person? Consider name variations, transposed digits, address moves. Respond JSON: decision, confidence, reasoning.' Above 0.95, auto-merge. Between 0.80 and 0.95, human review queue. Below, new golden record.
>
> Why hybrid beats pure ML or pure rules. Pure rules miss 15 to 30 percent of true matches because of typos and missing SSN. Pure ML is a black box, drifts silently, fails a HIPAA audit. Hybrid: deterministic handles the easy 70%, ML handles the medium 25%, human-in-the-loop handles the last 5%. Every decision has a reasoning trail."

---

## 6. LIVE DEMO (Slide 18) — 10 min

*Switch from slides to your terminal. Have the terminal pre-zoomed to a comfortable size. Have the API server already running in a second terminal pane.*

**Read [DEMO.md](DEMO.md) for exact commands and what to say.** The high-level beats:

1. **Run all tests** — `pytest tests/` — show 30 passing in under a second.
2. **Schema inference on a partner CSV** — `python -m services.schema_inference.cli samples/partner_acme_employer.csv` — show the YAML output, point at the PII tiers and confidence scores. *"This is the draft contract that goes to a human reviewer."*
3. **Entity resolution demo** — `python -m services.entity_resolution.demo` — walk through the four cases (Bob Smith → matches, Maria Garcia → review, Lin Chen → matches, Dolores → no match).
4. **IDV API** — show four `curl` requests:
   - `verify_nickname.json` → VERIFIED
   - `verify_ineligible.json` → INELIGIBLE (member found, coverage ended)
   - `verify_not_found.json` → NOT_FOUND
   - Show the metrics endpoint.
5. **Show the architecture in code** — open `schemas/ddl/02_silver.sql` and `pipelines/soda/checks.yml` for 30 seconds each.

> *Closing line for the demo:*
> "Everything you just saw runs on this laptop with no AWS access. The Bedrock calls have a deterministic local-mock fallback. In production, the same code paths hit real Bedrock — same interface, same output schema."

---

## 7. Hands-on artifacts (Slides 19–22) — 5 min

### Slide 19 — DDL: bronze, silver, gold, Aurora

> "Four DDL files in `schemas/ddl/`. Two design moves I want to highlight.
>
> **Bronze stores `raw_payload` as JSON, not as a fixed schema.** That's deliberate. Schema-on-read at bronze. We can change the canonical schema without rewriting bronze. Replay always works.
>
> **Gold has a row-access policy and dynamic masking.** Three lines of SQL — masking policy, row-access policy, alter table. An engineer logged in to Snowflake can query the gold table all day, but they only see the partners they're authorized for, with DOB masked to year. The IDV service role gets the full data."

### Slide 20 — The cleansing query

*Pull up `schemas/ddl/05_cleansing_examples.sql`.*

> "Four common inconsistencies, four queries.
>
> **Duplicate PII detection** — hard signal on tokenized SSN, soft signal on DOB plus soundex plus ZIP3. Triangulation, not single-rule.
>
> **Format errors** — SSN with non-digits, suspicious patterns like 0000 or 9999, DOB in the future, ZIP placeholder of 00000.
>
> **Cross-partner name refresh** — picks the most-current name across partners (think: post-marriage surname change in HR system but not in the older payer feed).
>
> **Attrition detection** — finds members who *should* be active per the gold table but haven't been seen in any partner feed for seven days. We don't auto-deactivate; we surface for partner confirmation."

### Slide 21 — Data contract YAML

*Pull up `schemas/data_contracts/partner_acme_employer_v1.yml`.*

> "Versioned. In git. Reviewed and signed off. The AI service produced the first draft; a human reviewed and committed.
>
> Note the `quality_thresholds` section — completeness rules, max quarantine percentage, max freshness hours. These drive the Soda gates. Note the `retention` section — partner-specific retention, deletion-on-offboard SLA. That's the data-contract API surface; if a partner changes their format, the contract version bumps and we run regression."

### Slide 22 — Soda checks + dbt model

*Pull up `pipelines/soda/checks.yml` and `transformations/dbt/models/silver/silver_eligibility_member.sql`.*

> "Soda checks are declarative. They run as Dagster asset checks. A failure blocks the silver-to-gold promotion. The check that matters most is the one that says *'no SSN-shaped value should appear in the ssn_token column'* — that's a P0 PII leak detector with `severity: critical`.
>
> The dbt model is the cleansing in one place. CTEs in linear flow. Each rule auditable. Quality failures captured as an array column on every row, used for the quality score and the quarantine flag."

---

## 8. SLOs, migration, cost (Slides 23–25) — 3 min

### Slide 23 — SLOs

> "Four SLOs. **IDV API availability 99.95%** — that's 22 minutes of downtime budget per month. **IDV API p99 latency under 150ms.** **CDC end-to-end freshness p95 under 90 seconds.** **Match precision 99.5%** on monthly sampled audit.
>
> Error-budget policy is enforced. When we burn 50% of any budget, non-critical feature work in that area halts until we're back inside. SLOs without enforcement are theater."

### Slide 24 — Migration plan

> "Six phases over roughly nine months. Two principles drive it: dual-run-then-cutover, and start with the simplest partner first.
>
> Phase zero: discovery. Read existing code. Interview partner success managers. Pull thirty days of support tickets. Don't build a thing yet.
>
> Phase two: pilot partner. New pipeline runs alongside old. Reconciliation report runs nightly. Old serves IDV traffic; new is shadow-mode. Acceptance threshold: 99.5% record agreement, 99.5% IDV decision agreement, fourteen consecutive days.
>
> Phase three: cut over the pilot. Onboard three to five more partners through the AI-assisted contract flow.
>
> Phase four: full migration in waves of five. Decommission the old pipeline.
>
> Phase six is steady state. Continuous improvement. 20% of every sprint to debt and reliability. Always."

### Slide 25 — Cost

> "At launch, one million members, ten partners, this runs about $15K a month, all in. At growth, ten million members, fifty partners, $63K a month — that's about a half a cent per member per month at scale.
>
> Bedrock is the line that scales with usage. At growth, projected $11K a month for entity-resolution adjudication. We have three cost levers — switch most adjudications to Haiku, pre-filter candidates more aggressively, cache embeddings — that I'd pull at month six if cost diverges from forecast."

---

## 9. Wrap + next steps (Slides 26–27) — 2 min

### Slide 26 — What I'd do differently in your real environment

> "Three things.
>
> **One:** I'd talk to partners before committing to architecture. Half this design is informed by guesses about what your real partner files look like. Two weeks reading existing pipeline code, interviewing the squad, and looking at five real partner files would change details.
>
> **Two:** I'd build the migration plan with operations. The interesting risk isn't building the new system — it's running both in parallel without breaking IDV for live members.
>
> **Three:** I'd treat the AI components as augmentation, never gates. A staff engineer's job is to make sure that when Claude returns garbage, a human gets paged with enough context to fix it in ten minutes. The schema-inference output is *always* reviewed before promotion. Always."

### Slide 27 — Closing

> "If I take this role, the first 30 days are listening, reading, and asking questions — not building. The first 90 days are building the foundation phase from this plan with the squad's input. The first 12 months are getting Lore from current state to this target state, with measurable improvements every quarter — partner onboarding time, member match rate, cost per member.
>
> The repo is at github.com/perice-pope/lore-eligibility-platform. It's private. I'll grant access to whoever you tell me to.
>
> I'd love to take questions."

---

## Appendix — Likely questions & answers

**Q: Why didn't you use AWS Entity Resolution?**
> "I considered it. Two reasons I went custom. AWS Entity Resolution is newer, less battle-tested at our scale; the cost model surprises some teams. And I wanted explicit LLM reasoning persisted with each match decision for HIPAA audit — that's not native in AWS Entity Resolution. If their service matures and adds reasoning persistence, I'd reconsider."

**Q: Why per-partner KMS keys instead of one platform key?**
> "Cryptographic shredding on partner offboarding. Delete the key, all that partner's data at rest is unreadable, even from backups. With shared keys, the cleanup story is messy and audit-hostile."

**Q: What's the failure mode for the Bedrock entity-resolution adjudicator?**
> "Three layers. One — Bedrock returns garbage JSON. Code has fallback parsing and emits to a DLQ if it fails. Two — Bedrock rate-limits. Adjudication queues to async; new accounts wait for human review. Brief degraded UX. Three — Bedrock returns wrong answer. The 0.95 confidence threshold for auto-merge is conservative; below that, human-in-the-loop catches it."

**Q: How do you handle a partner that sends a totally new format with breaking changes?**
> "Schema fingerprint check at bronze landing. If the fingerprint doesn't match the registered contract, the file ingests to *quarantine*, not bronze. Partner success manager is alerted with a diff. We never silently drop a file."

**Q: Snowflake is expensive. What if Lore can't justify it?**
> "Fair. Tradeoff: cost vs. operational velocity. If cost is the constraint, I'd start with Trino on EMR over Iceberg and skip Snowflake entirely — you lose dynamic masking conveniences, gain lower bill. Possible to revisit at month 12."

**Q: Why FastAPI over a heavier framework?**
> "Async-native, type-safe with Pydantic, auto-generates OpenAPI docs, deploys as a single container. Latency profile is excellent — we're not bound by the framework, we're bound by Aurora roundtrips. For an internal API with one job, FastAPI is the right level."

**Q: What about real-time streaming with Flink instead of Spark Structured Streaming?**
> "Both work. Flink wins on event-time correctness with watermarks; Spark Structured Streaming wins on team familiarity (most data engineers already know Spark) and unified batch/stream codebase. For our current shape — silver-layer enrichment with low-millisecond latency budget — Spark SS is sufficient. If we hit a use case that demands sub-second event-time processing, I'd revisit."

**Q: How does this interact with Lore's existing systems?**
> "Honest answer: I don't know what they are. The IDV API is the integration surface — it speaks REST in, EventBridge events out. Anything else is a downstream consumer that subscribes to the right event topic. I'd integrate by introducing the event surface and migrating consumers one at a time."
