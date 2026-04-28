# SLOs & Error Budgets

Plain English: **how good "good enough" is**, and what we do when we miss it.

---

## Why SLOs

A staff engineer's job in operations is to keep the team's eyes on the small number of metrics
that *actually matter to members and partners*, and to budget engineering time against them.

We track **four SLIs** with explicit targets. When we breach the budget on any one of them,
we stop new feature work in that service area until we're back inside budget.

---

## SLO 1 — Identity Verification API availability

| | |
|---|---|
| **What it measures** | % of `POST /v1/verify` requests returning a non-5xx response within 5s |
| **Target** | 99.95% over 30 rolling days |
| **Error budget** | 21.6 minutes / 30 days |
| **Why it matters** | A member trying to sign up sees a flat-out failure when this drops |
| **Burn rate alerts** | Page on 14.4× burn over 1h (= 99.95% gone in 6h); ticket on 3× over 6h |

```
SLI = sum(http_requests{handler="/v1/verify",status!~"5.."}) / sum(http_requests{handler="/v1/verify"})
```

## SLO 2 — Identity Verification API latency

| | |
|---|---|
| **What it measures** | p99 server-side latency on `POST /v1/verify` |
| **Target** | < 150ms over 30 rolling days |
| **Error budget** | 1% of requests may exceed 150ms; max 30 minutes/day where p99 > 250ms |
| **Why it matters** | Sign-up flow drops members at every additional second of perceived wait |

## SLO 3 — CDC end-to-end freshness

| | |
|---|---|
| **What it measures** | Time between partner DB commit and golden record updated in Aurora |
| **Target** | p95 < 90 seconds over 7 days |
| **Error budget** | 5% of events may exceed 90s; sustained breach > 4h triggers incident |
| **Why it matters** | Member just joined an employer this morning, tries Lore at lunch — should work |

```
SLI = quantile(0.95, partner_db_commit_ts → aurora_golden_record_ts)
```

## SLO 4 — Match precision

| | |
|---|---|
| **What it measures** | When the system decides "these two records are the same person" and merges them, how often is that decision actually correct? |
| **Target** | At least 99.5% correct, measured monthly |
| **Error budget** | Fewer than 1 in 200 merges may be wrong. Even a single wrong merge that exposes one member's data to another is treated as a top-priority incident. |
| **Why it matters** | A wrong merge means one person sees another person's healthcare info. That's a HIPAA breach. This is the one SLO we will never relax to ship faster. |

**How we measure it:** every month, we randomly pick 1,000 merge decisions, two people review each
one independently, and we count how many were correct. If the two reviewers disagree, the case
goes to Compliance for a tie-breaking review.

---

## Supporting metrics (not SLOs but watched)

These don't have formal error budgets, but they tell us early when something is drifting before
it shows up in a customer-facing SLO.

| Metric (in plain terms) | Healthy range | What we do if it's outside |
|---|---|---|
| **How long it takes to parse a bulk file** after it lands in S3 | 95% of files done in under 10 min | Investigate the slow parse — usually a stuck Spark job or an outsized file |
| **Quality-check fail rate** on incoming records (per partner) | Under 1% of rows fail | Have a data-quality conversation with that partner's success manager |
| **How long the nightly dbt build takes** to refresh Snowflake | Under 30 min | Either refactor the slow model or scale up the Snowflake warehouse |
| **AI adjudicator response time** when matching a tricky record | 99% of calls return within 4 seconds | Switch to a faster model tier or batch the requests |
| **AI cost per 1 million entity-resolution decisions** | Under our agreed monthly cap | Tune the prompts, drop to a cheaper Claude tier (Haiku), or cache more aggressively |
| **Skyflow PII vault — number of detokenize calls per person, per day** | Typically under 100; alert at over 500 | Possible insider-threat signal — escalate to security |
| **AWS Macie scans for PII the system didn't expect** | Should always be zero | If anything is found, our data contract has a gap and we investigate |
| **Aurora replication lag** between writer and read replicas | Under 5 seconds | Page on-call if it stays above 30 seconds — read traffic is starting to see stale data |

---

## Error budget policy — when we slow down feature work

We don't just track the budget; we act on it. When more than half the budget for any of SLO 1–4
is used up before the period is over, here's what happens:

1. **Stop non-essential feature work** in that service area. Bug fixes and security work continue.
2. **Spend a focused 1–2 weeks** on whatever's eating the budget — debugging, scaling, refactoring.
3. **Write up the root cause and the fix** in a blameless postmortem so the team learns from it.
4. **Restart feature work** only after the budget is replenished, or after the leadership team
   explicitly signs off on a documented exception.

This is unfun, especially when it slows a launch. It's also the only way the SLO is real.
Without this rule, SLOs are decoration.

---

## Quarterly review

Each quarter:

- Are the targets still right? (Maybe 99.95% is overkill for v1 of IDV.)
- Are we measuring what matters? (Did a major incident reveal a missing SLI?)
- Are alerts paging the right people? (Page fatigue → silence creep.)

Adjustments are made deliberately, not ad hoc. The principle is: **make targets harder when
you're hitting them comfortably and you have headroom; make targets easier only after a
documented postmortem-driven discussion, never to escape paging fatigue.**
