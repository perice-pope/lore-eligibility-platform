# Live Demo Runbook

**Total demo time: 10 minutes.**

This is the script for the live-demo segment of the panel presentation. Every command
has been verified to work. There is **no AWS access required** — Bedrock calls fall
back to deterministic local mocks.

---

## ⏱ Pre-flight (do this 5 minutes before the call starts)

Open **two terminal windows** side by side:

- **Terminal A** — for running commands during the demo
- **Terminal B** — for the IDV API server (will run in foreground)

Set your terminal font to ~16pt. Resize windows so the audience can read.

In **both terminals**, run:

```bash
cd ~/Documents/lore-case-study/lore-eligibility-platform
source .venv/bin/activate
```

### One-time setup (only if .venv doesn't exist)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q fastapi 'pydantic>=2' 'uvicorn[standard]' httpx pytest pyyaml
```

### Pre-warm the IDV server in Terminal B

```bash
PYTHONPATH=. uvicorn services.identity_verification_api.main:app --port 8000 --log-level info
```

You should see:
```
INFO  idv.api starting up — version=0.1.0
INFO  idv.api golden record store initialized: {'backend': 'memory', 'records': 5}
INFO  Uvicorn running on http://127.0.0.1:8000
```

**Leave this running.** All `curl` commands hit `localhost:8000`.

### Verify everything works (run this once before the audience joins)

```bash
PYTHONPATH=. python -m pytest tests/ -q
# expect: 30 passed
```

```bash
curl -s http://localhost:8000/healthz
# expect: ok
```

You're now demo-ready.

---

## 🎬 The demo — what to type, what to say

### Beat 1 — "Tests pass" (60 seconds)

**Switch to Terminal A.**

**Type:**
```bash
PYTHONPATH=. python -m pytest tests/ -v
```

**While it runs (under 1 second), say:**
> "First, sanity check — thirty unit tests, sub-second runtime, all passing. They cover schema inference heuristics, entity resolution, the PII vault's policy engine, the CDC handler's tokenization-at-edge, and the IDV API end-to-end. Hermetic — no AWS access required."

**Point at the pytest summary.** Don't dwell.

---

### Beat 2 — AI feature 1: Schema inference (90 seconds)

**Type:**
```bash
cat samples/partner_acme_employer.csv | head -3
```

**Say:**
> "This is what a partner sends. CSV. Mixed-case column names. SSN with hyphens. DOB in MM/DD/YYYY. Without a parser written, this is opaque to our pipeline."

**Type:**
```bash
PYTHONPATH=. python -m services.schema_inference.cli samples/partner_acme_employer.csv --mode local
```

**While it scrolls, say:**
> "I'm running the schema-inference service in local-mock mode for the demo, which is a deterministic heuristic classifier that mimics Claude's output schema. In production, this hits Bedrock — same code path, same JSON contract.
>
> The output is a draft data contract YAML. Every column gets a canonical-field mapping, a PII tier, suggested cleansing rules, and a confidence score."

**Scroll up and point at one or two columns.**
> "SSN — Tier 1 direct. Suggested rules: validate format, tokenize via Skyflow, store last 4 only. Confidence 0.92.
>
> EligStartDate — Tier 3 sensitive. Coerce to ISO 8601.
>
> The ouput goes into git as `schemas/data_contracts/<partner>_v1.yml`. A human reviews, edits anything wrong, signs off, commits. **Time-to-onboard a new partner: under an hour of human review instead of five days of engineering work.**"

---

### Beat 3 — AI feature 2: Entity resolution (2.5 minutes)

**Type:**
```bash
PYTHONPATH=. python -m services.entity_resolution.demo
```

**Output appears. Walk through each case.**

**Case 1: "Bob Smith"**
> "Incoming sign-up: 'Bob Smith,' DOB 1962-04-12, ZIP 90210, SSN-last-4 6789. Existing golden record: 'Robert Smith,' same DOB, same ZIP, same SSN-last-4.
>
> Decision: AUTO_MATCH. Stage: deterministic. Reason: DOB plus ZIP plus SSN-last-4 is a strong combined signal. We never even reached the embedding stage. **The fastest possible path won.**"

**Case 2: "Maria Garcia"**
> "Incoming: Maria Garcia. Golden record: Maria Garcia-Lopez. Different surname — could be marriage. Different ZIP within the same metro — could be a move.
>
> Decision: NO_MATCH. Score 0.735. Stage: embedding.
>
> *This is interesting.* In production with Bedrock Titan, the embedding similarity would be much higher than 0.735 — Titan understands name variants and semantic similarity in ways the local hash-based fallback can't. I left this case in the demo deliberately to show that the pipeline fails *safely*: it doesn't merge unconfidently. Maria gets a new golden record. If she signs up later with consistent info, future matching catches it."

**Case 3: "Lin Chen"**
> "Exact match. Deterministic stage. Score 0.97. Done."

**Case 4: "Dolores Abernathy"**
> "New person. No blocking-key overlap with any existing golden record. NO_MATCH. New record created.
>
> The four cases together demonstrate the three resolver stages — deterministic, embedding, no-candidate — and the right behavior in each."

---

### Beat 4 — IDV API live (3 minutes)

**Type:**
```bash
cat samples/verify_nickname.json
```

**Say:**
> "Member tries to sign up. They typed 'Bob' for first name — their legal name is Robert. They type DOB, ZIP, last-4 of SSN."

**Type:**
```bash
curl -s -X POST http://localhost:8000/v1/verify \
  -H "Content-Type: application/json" \
  -d @samples/verify_nickname.json | python -m json.tool
```

**Output appears:**
```json
{
  "status": "VERIFIED",
  "correlation_id": "...",
  "golden_record_id": "G-0001",
  "partner_id": "acme-corp",
  "score": 1.0,
  "decision_basis": "Exact deterministic match on DOB, last name, ZIP, SSN-last-4."
}
```

> "Status VERIFIED. Score 1.0. The correlation ID is what the support team uses if this member calls in later. Notice the API doesn't return PII — just the golden record ID, the partner, the decision basis. Even our own logs don't see PHI in plaintext."

**Type:**
```bash
curl -s -X POST http://localhost:8000/v1/verify \
  -H "Content-Type: application/json" \
  -d @samples/verify_ineligible.json | python -m json.tool
```

**Output:**
```json
{
  "status": "INELIGIBLE",
  "golden_record_id": "G-0005",
  "decision_basis": "Member found, but coverage end date is in the past."
}
```

> "INELIGIBLE — different status from NOT_FOUND. The member exists in our system; their employer's coverage ended last year. The mobile app shows a different message: 'We found your account but your eligibility has ended. Contact your HR.' Better UX than a generic failure."

**Type:**
```bash
curl -s -X POST http://localhost:8000/v1/verify \
  -H "Content-Type: application/json" \
  -d @samples/verify_not_found.json | python -m json.tool
```

**Output:**
```json
{
  "status": "NOT_FOUND",
  "decision_basis": "No eligibility record matches these inputs..."
}
```

> "NOT_FOUND. The member isn't in any partner feed. Could be a typo, could be wrong partner, could be a fraudulent sign-up. The mobile app routes to support."

**Type:**
```bash
curl -s http://localhost:8000/metrics | head -20
```

> "Prometheus-format metrics. Datadog scrapes this every 30 seconds. We track requests by status, latency histograms, dependency health. SLO burn-rate alerts fire from Datadog directly."

---

### Beat 5 — Show the architecture in code (90 seconds)

**Open the project in your editor (VS Code or whatever) on the projector. Open these files in tabs:**

1. **`schemas/ddl/02_silver.sql`** — scroll to the masking + row-access policies in the gold DDL.
> "Three lines of SQL gives us per-role data masking. The IDV service sees full DOB. An analyst sees DOB masked to year-only. Compliance sees everything with audit logging. Zero application-layer changes."

2. **`pipelines/soda/checks.yml`** — scroll to the `ssn_token_format_check`.
> "This is the P0 PII-leak detector. Severity: critical. If a raw SSN-shaped value ever appears in the ssn_token column, this fails the silver build, blocks promotion to gold, and pages on-call. *That* is what a 'data contract enforced' actually looks like."

3. **`schemas/ddl/05_cleansing_examples.sql`** — scroll past the duplicate-PII detection query.
> "This is the kind of cleansing logic the brief explicitly asked for — duplicate PII detection using hard signals (tokenized SSN match) plus soft signals (DOB + name soundex + ZIP3). Triangulation, not single-rule."

---

### Beat 6 — Close (30 seconds)

**Switch back to the slide deck.**

> "Recap: thirty tests pass, two AI services running, four IDV scenarios demonstrated end-to-end, all without AWS access. In production, every code path you saw routes to Bedrock for the AI calls and Aurora for the golden record reads. The interfaces are identical.
>
> Next slide: SLOs and migration plan."

---

## 🆘 Troubleshooting (if something goes wrong on stage)

### "Address already in use" on uvicorn

```bash
# find the existing server
lsof -i :8000
# kill it
kill -9 <pid>
# or use a different port and update curls
```

### Tests fail unexpectedly

```bash
# nuke caches
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null
PYTHONPATH=. python -m pytest tests/ -v
```

### Curl returns "connection refused"

The IDV server died. In Terminal B:
```bash
PYTHONPATH=. uvicorn services.identity_verification_api.main:app --port 8000
```

### The API returns INELIGIBLE for the nickname case

Make sure your seed file has G-0001 with `effective_end_date: null`. Check
`samples/golden_records_seed.json`.

### If everything is on fire

> "The demo gods aren't smiling on me today. Let me show you the test output instead — it covers the same paths."
>
> ```bash
> PYTHONPATH=. python -m pytest tests/ -v
> ```
> Walk through the test names. They tell the story: nickname matching, ineligible detection, ambiguous candidates, etc.

---

## 📋 Cheat sheet — commands in order, no narrative

```bash
# Pre-flight (Terminal B, leave running):
PYTHONPATH=. uvicorn services.identity_verification_api.main:app --port 8000

# Demo (Terminal A, in order):
PYTHONPATH=. python -m pytest tests/ -v
PYTHONPATH=. python -m services.schema_inference.cli samples/partner_acme_employer.csv --mode local
PYTHONPATH=. python -m services.entity_resolution.demo
curl -s -X POST http://localhost:8000/v1/verify -H "Content-Type: application/json" -d @samples/verify_nickname.json | python -m json.tool
curl -s -X POST http://localhost:8000/v1/verify -H "Content-Type: application/json" -d @samples/verify_ineligible.json | python -m json.tool
curl -s -X POST http://localhost:8000/v1/verify -H "Content-Type: application/json" -d @samples/verify_not_found.json | python -m json.tool
curl -s http://localhost:8000/metrics | head -20
```
