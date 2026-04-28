# Cost Estimate

Plain English: **what this costs to run, and where the money goes.** Modeled at two scales —
launch (1M members) and growth (10M members). Numbers are rough, conservative, US East 1.

---

## Assumptions

- 10 partners at launch, 50 at growth.
- Avg eligibility record size: 1 KB.
- Daily delta: 0.5% of members (eligibility changes).
- IDV API: 5K verifications/day at launch, 100K/day at growth.
- Bedrock entity-res adjudications: 10% of inbound records require LLM call.
- All prices ballpark April 2026 list prices.

---

## Monthly cost table

| Service | Launch (1M members) | Growth (10M members) | Notes |
|---|---|---|---|
| **AWS S3** (raw + bronze) | $80 | $700 | Iceberg compresses 4–5×; lifecycle to IA after 90d |
| **AWS Transfer Family** (SFTP) | $200 | $400 | Mostly per-endpoint fixed cost |
| **AWS MSK** (Kafka 3 brokers, m5.large) | $750 | $2,200 | Larger brokers + storage at growth |
| **MSK Connect** (Debezium) | $300 | $900 | Per-connector |
| **AWS EMR Serverless** | $200 | $1,500 | Bulk + nightly transformation jobs |
| **AWS Glue** (catalog + occasional crawlers) | $100 | $300 | |
| **Aurora Postgres** (r6g.xlarge primary + 2 replicas) | $1,800 | $4,500 | Scales with IDV traffic |
| **AWS KMS** | $50 | $150 | One CMK/partner; some operations |
| **AWS Macie** | $200 | $1,000 | Per-GB scan cost |
| **CloudTrail + GuardDuty + Config** | $400 | $1,200 | Org-wide |
| **OpenSearch** (entity-res vector index) | $600 | $2,400 | r6g.large.search nodes |
| **EKS** (Dagster agent, IDV API) | $500 | $1,500 | Cluster + nodes |
| **AWS Bedrock** (Claude Sonnet + Titan embed) | $1,200 | $11,000 | See breakdown below |
| **Snowflake** | $3,000 | $14,000 | Curated layer + dbt builds |
| **Skyflow** | $2,000 | $7,500 | Per-record-stored + API calls |
| **Datadog** | $2,500 | $9,000 | Hosts + custom metrics + log volume |
| **Dagster Cloud** | $1,000 | $3,500 | Per-seat + per-pipeline-step |
| **Misc** (Lob USPS validation, PagerDuty, etc.) | $600 | $2,000 | |
| **TOTAL (rough)** | **~$15,500/mo** | **~$63,000/mo** | |
| **Per-member-per-month** | **$0.0155** | **$0.0063** | Drops with scale |

For context, Lore's reported >10% healthcare savings on ~$X PMPM means this platform pays for
itself many times over per member if it's the gating factor for member onboarding.

---

## Bedrock cost breakdown (the line that scales)

At growth scale (10M members), assume:

| Use case | Volume / mo | Model | Cost |
|---|---|---|---|
| Schema inference (new partner files) | ~5 calls/mo | Claude 3.5 Sonnet | <$10 |
| Entity res — embedding generation | 10M records × 50% novel = 5M embeddings | Titan Text v2 ($0.02/1M tokens, ~50 tokens/record) | ~$5 |
| Entity res — LLM adjudication | 5M × 10% = 500K calls × 600 input + 200 output tokens | Claude 3.5 Sonnet ($3/$15 per 1M) | ~$2,500 |
| Anomaly explanations & DQ alerts | ~50K calls/mo | Claude 3.5 Haiku | ~$50 |
| **Total** | | | ~$2,600/mo |

The number above ($11K) bakes in 4× headroom and Provisioned Throughput contracts for SLA on
adjudication latency.

**Cost levers:**
- Most adjudications could downshift to Haiku (10× cheaper) — A/B test by month 6.
- Pre-filter candidates to fewer per-record before adjudication (currently top-10 → top-3).
- Cache embeddings by `(name_norm, dob, zip)` hash — many records repeat across partners.

---

## What we're spending on the AI feature vs. building it ourselves

Building entity resolution without LLM/embeddings would cost:
- 1–2 engineers full-time × 6 months = ~$200K loaded
- Ongoing tuning + model maintenance = ~$50K/yr
- Match precision/recall ceiling lower than hybrid

Bedrock running cost at growth: ~$130K/yr.

Wash on cost; **win on time-to-market, auditability (LLM reasoning is human-readable), and the
ability to onboard partners faster.** This is the entire value prop of the AI feature in business
terms.
