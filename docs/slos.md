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
| **What it measures** | Of all entity-resolution merges, % that are correct (per ground-truth audits) |
| **Target** | ≥ 99.5% on monthly sampled audit |
| **Error budget** | ≤ 0.5% wrong-merge rate; even one wrong-merge that exposes data is a P0 |
| **Why it matters** | Wrong merge = breach. This is the SLO we'd never trade off. |

Sample audit: 1,000 random merges per month, manually reviewed by two people; disagreements
escalate to compliance.

---

## Supporting metrics (not SLOs but watched)

| Metric | Threshold | Action |
|---|---|---|
| Bulk file land → bronze parsed | p95 < 10 min | Investigate slow parse |
| Bronze → silver Soda gate fail rate | < 1% per partner | Partner data quality conversation |
| Silver → gold dbt build duration | < 30 min | Refactor model or scale warehouse |
| Bedrock entity-res adjudicator p99 latency | < 4s | Switch to faster model or batch |
| Bedrock entity-res cost per 1M decisions | < $X | Optimize prompts, switch model tier |
| Skyflow detokenize calls per actor / day | <100 typical, alert >500 | Insider-threat signal |
| Macie unmanaged-PII findings in raw | 0 | Investigate; data contract gap |
| Aurora replication lag | < 5s | Page on >30s |

---

## Error budget policy

When the budget for any of SLO 1–4 is **>50% consumed in the period**, we:

1. Halt non-critical feature work in the affected service.
2. Run a focused reliability iteration (1-2 weeks).
3. Document root cause and remediation in a postmortem-style doc.
4. Resume feature work only when budget is replenished or a roadmap waiver is signed off.

This is unfun but it's the only way the SLO is real. Without enforcement, SLOs are theater.

---

## Quarterly review

Each quarter:

- Are the targets still right? (Maybe 99.95% is overkill for v1 of IDV.)
- Are we measuring what matters? (Did a major incident reveal a missing SLI?)
- Are alerts paging the right people? (Page fatigue → silence creep.)

Adjustments are made deliberately, not ad hoc. The principle is: **make targets harder when
you're hitting them comfortably and you have headroom; make targets easier only after a
documented postmortem-driven discussion, never to escape paging fatigue.**
