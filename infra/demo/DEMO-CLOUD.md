# Cloud Demo Runbook

**~9 minutes. Live AWS. Real API Gateway, real Lambda, real DynamoDB, real S3 event-driven pipeline, real Claude data-contract generation on every file landing.**

When a partner CSV lands in S3, the `file_processor` Lambda parses it into DynamoDB records *and* invokes the `schema_inference` Lambda, which sends the column structure to **Claude (via the Anthropic API)** and writes a draft data-contract YAML next to the data file in the bronze bucket. Beat 4 shows both outputs side by side in the console.

---

## Pre-flight (15 min before the call)

Open a fresh terminal:

```bash
cd ~/Documents/lore-case-study/lore-eligibility-platform/infra/demo
```

**Set your Anthropic API key** so the schema-inference Lambda hits real Claude (without it, the pipeline still produces a YAML, but it'll be a deterministic local-mock — the audience won't see "Claude" in the output):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # paste your key
```

Deploy fresh (idempotent — safe to re-run if anything looks off):

```bash
./deploy-cli.sh
```

Wait for `Cost meter started.` Then capture the live URL into a shell variable you'll reuse for the rest of the demo:

```bash
export API="https://$(aws apigatewayv2 get-apis --region us-east-1 \
  --query "Items[?Name=='lore-elig-demo-idv-api'].ApiId | [0]" --output text).execute-api.us-east-1.amazonaws.com"
export ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
export RAW_BUCKET="lore-elig-demo-raw-${ACCOUNT}"
echo "API=$API"
echo "RAW_BUCKET=$RAW_BUCKET"
```

Smoke test:

```bash
curl -s $API/healthz && echo
```
Expected: `ok`

**Open these AWS console tabs in your browser, signed into account `185529490129`, region `us-east-1`:**

| Tab | URL | When you'll switch to it |
|---|---|---|
| DynamoDB items | https://us-east-1.console.aws.amazon.com/dynamodbv2/home?region=us-east-1#item-explorer?table=lore-elig-demo-golden-records | Beat 3 |
| S3 raw bucket | https://us-east-1.console.aws.amazon.com/s3/buckets/lore-elig-demo-raw-185529490129?region=us-east-1 | Beat 4 (before drop) |
| S3 bronze bucket | https://us-east-1.console.aws.amazon.com/s3/buckets/lore-elig-demo-bronze-185529490129?region=us-east-1 | Beat 4 (after drop) |
| Lambda IDV API | https://us-east-1.console.aws.amazon.com/lambda/home?region=us-east-1#/functions/lore-elig-demo-idv-api | Beat 5 |
| CloudWatch metrics | https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#metricsV2?graph=~()&query=~'*7bAWS*2fLambda*2cFunctionName*7d%20FunctionName*3d*22lore-elig-demo-idv-api*22 | Beat 5 |
| API Gateway | https://us-east-1.console.aws.amazon.com/apigateway/main/apis | Optional / Beat 1 |

Also have the terminal pre-positioned with the `cd` and `export` lines above already executed. You should not need to type any setup commands during the live demo.

---

## Beat 1 — Architecture tour + prove it's live (90 sec)

**🖥 Show on screen:** terminal. Optionally keep the architecture diagram open in the background — point at each piece as you cover it.

**🎙 Say (opening):**
> *"Everything you're about to see runs on real AWS — three minutes from `git clone` to live URL. Four pieces do all the work. Quick plain-English tour before I prove it's live."*

### API Gateway — *the public front door*
- The HTTPS URL a partner mobile app calls
- Handles encryption, CORS, and routing — no servers for us to operate
- Every request lands here first and gets forwarded to the IDV Lambda

### The IDV Lambda — *the brain*
- Takes a verification request: *"is the person with this name, DOB, ZIP, and last-4-of-SSN one of our members?"*
- Builds a **fingerprint** out of ZIP + DOB + last name — the same fields a partner UI collects
- Looks for that fingerprint in DynamoDB
- Returns one of **five answers**:
  - **VERIFIED** — exactly one match, coverage active today
  - **INELIGIBLE** — match exists but coverage ended (we still found you, just not eligible)
  - **AMBIGUOUS** — multiple records match → step-up authentication needed
  - **NEEDS_REVIEW** — only a fuzzy near-match → human review or knowledge-based questions
  - **NOT_FOUND** — no record matches
- Cold start under 1 second; warm calls under 100 ms
- **No Claude call on this hot path** — every verify decision is a fast database lookup.

### DynamoDB — *the directory of who's who*
- Holds **five "golden records"** — our canonical members. Specifically: Robert Smith, Maria Garcia-Lopez, Lin Chen, Marcus Williams, Ethan O'Brien.
- **How they got there:** seeded at deploy time from `samples/golden_records_seed.json` — the same JSON file the local prototype reads. That's intentional: cloud and local stay byte-for-byte in sync so I can develop on a plane.
- **Why a "secondary index"** matters here: each record has a unique ID like `G-0001`, but **no partner ever calls verify with `G-0001`** — they only know the user's name, DOB, and ZIP. So we built a **second way in** to the same data:
  - Primary key: `golden_record_id` (a unique UUID)
  - Secondary index: the fingerprint `zip#dob#lastname-lowercase`
  - Think of a phone book sorted by phone number with a second copy sorted by last name — same data, two access patterns. Both are sub-100 ms.

### Two S3 buckets — *the partner-file pipeline*
- **`raw`** bucket = where partners drop employee files (CSVs, JSON, EDI). It has a **7-day auto-delete** so we never sit on PII.
- **`bronze`** bucket = where parsed, contract-conformant records land for analytics queries.
- **Beat 5 will drop a real test file** — `samples/partner_acme_employer.csv`, 10 fake Acme employees including Maria from our golden records — into `raw/inbox/acme-corp/`. You'll watch it flow through both buckets and into DynamoDB.

**🎙 Say:**
> *"OK — let's prove it's actually live."*

**Run:**

```bash
echo $API
curl -s $API/healthz && echo
curl -s $API/readyz | python3 -m json.tool
```

**Expected:**
- `echo`: a real `https://*.execute-api.us-east-1.amazonaws.com` URL
- `/healthz`: `ok`
- `/readyz`: JSON with `"backend": "dynamodb"` and `"table_active": true`

**🎙 Say after the output:**
> *"`/readyz` confirms the Lambda is connected to DynamoDB. This is the same liveness contract I'd wire to a load balancer in production."*

---

## Beat 2 — Identity verification, three scenarios (2 min)

**🖥 Show on screen:** terminal.

**🎙 Say first:**
> *"Three verify calls. Each one tests a different real-world scenario a partner app has to handle. I'll set up what each one is *proving* before I run it, so you can see the result land."*

**Reference card — the five seeded golden records sitting in DynamoDB right now (real field names):**

| `golden_record_id` | `last_name` | `dob` | `zip` | `ssn_last4` | `effective_end_date` |
|---|---|---|---|---|---|
| G-0001 | Smith | 1962-04-12 | 90210 | 6789 | *(null — coverage active)* |
| G-0002 | Garcia-Lopez | 1985-09-30 | 78701 | 4321 | *(null — coverage active)* |
| G-0003 | Chen | 1990-01-15 | 10001 | 1122 | *(null — coverage active)* |
| G-0004 | Williams | 1980-06-14 | 30303 | 8899 | *(null — coverage active)* |
| G-0005 | O'Brien | 1955-07-18 | 29401 | 3344 | **2024-06-30** *(past → INELIGIBLE)* |

A null `effective_end_date` means "coverage is open-ended / still active." A past date means coverage has ended — which is exactly what drives the INELIGIBLE branch you'll see in 2b.

**Important detail:** the verify endpoint matches identity on **(DOB + ZIP + last name + SSN-last-4)** — *not* first name. First name varies too much (nicknames, typos, "Bob" vs "Robert"); the other four are the high-signal anchors.

**Response shape — what every verify call returns and where each field comes from:**

| Field | Where it comes from | Used for |
|---|---|---|
| `status` | Computed by the Lambda based on lookup result + coverage check | The primary value the partner UI switches on (VERIFIED / INELIGIBLE / AMBIGUOUS / NEEDS_REVIEW / NOT_FOUND) |
| `correlation_id` | A fresh UUID4 the Lambda mints per-request, before business logic runs | Support traceability — caller reads this to a rep, rep finds the exact request in CloudWatch |
| `golden_record_id` | **Read directly from the matched DynamoDB row** (e.g. `G-0001`) | Tells the caller which canonical identity matched; useful for cross-referencing |
| `partner_id` | **Read directly from the matched DynamoDB row** (e.g. `acme-corp`) | Indicates which carrier/employer this member is associated with |
| `score` | Lambda-computed per branch: `1.0` exact match, `0.7` fuzzy, `0.5` ambiguous, `0.0` not-found | Confidence level — partner UI can show different copy at different score ranges |
| `decision_basis` | Hard-coded human-readable string per branch in the Lambda | Audit trail — what a support rep or compliance auditor reads to understand a decision |
| `detail.stage` | Which matcher branch resolved the request: `deterministic` / `fuzzy` / `not_found` | Observability — drives "% of traffic resolved at stage 1" dashboards |

So the answer to *"where do `partner_id` and `golden_record_id` come from?"* — they're literally fields on the row in DynamoDB you'll see in Beat 3. The other fields are minted or computed by the Lambda for this specific request.

---

### 2a — VERIFIED *(proves: nickname-tolerant matching)*

**Sending:**
```json
{ "first_name": "Bob", "last_name": "Smith",
  "dob": "1962-04-12", "zip": "90210", "ssn_last4": "6789" }
```

**What this proves:** a user typed their nickname (or had a typo on first name) and still verified. Identity isn't anchored on first name — it's anchored on the four high-signal fields. This is the everyday happy path: 90%+ of real verifications resolve here.

**Why we expect VERIFIED:** the four anchor fields exactly match **G-0001 (Robert Smith)**, and his coverage is active. The Lambda returns VERIFIED with score 1.0.

**Run:**
```bash
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d @../../samples/verify_nickname.json | python3 -m json.tool
```

**Look for:** `"status": "VERIFIED"`, `"golden_record_id": "G-0001"`, `"score": 1.0`, `"detail": {"stage": "deterministic"}`.

**🎙 Say after the response:**
> *"User typed 'Bob' for first name — we matched him against Robert. The four anchor fields lined up against G-0001 in DynamoDB, his coverage is active, so we return VERIFIED. Notice the response carries no PII back — just status, score, and a correlation ID a support agent can search by."*

---

### 2b — INELIGIBLE *(proves: 'we know you' is a separate check from 'you're covered')*

**Sending:**
```json
{ "first_name": "Ethan", "last_name": "O'Brien",
  "dob": "1955-07-18", "zip": "29401", "ssn_last4": "3344" }
```

**What this proves:** finding someone in the member directory is *not* the same as confirming they're currently covered. The API has to surface the difference because the partner mobile app needs different copy for the two cases:
- *"we don't know you"* → review your inputs / contact support
- *"we know you, but coverage ended"* → contact your HR / re-enroll

If the API collapsed these into one status, the partner UX gets clumsy.

**Why we expect INELIGIBLE — the actual mechanic:** the four anchor fields match **G-0005 (Ethan O'Brien)** exactly, so identity is confirmed. *But* Ethan's golden record has `"effective_end_date": "2024-06-30"`. Today is 2026-04-29 — the end date is ~22 months in the past. The Lambda runs an `is_ineligible` check: *"is the end-date earlier than today?"* It is, so the response flips from VERIFIED to **INELIGIBLE**, with the reason explicitly named.

**Run:**
```bash
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d @../../samples/verify_ineligible.json | python3 -m json.tool
```

**Look for:** `"status": "INELIGIBLE"`, `"golden_record_id": "G-0005"`, `"decision_basis"` mentioning the past end date.

**🎙 Say after the response:**
> *"We found Ethan — he's in the directory. But his coverage-end-date is summer of 2024, almost two years ago. So instead of VERIFIED, we return INELIGIBLE with the reason explicitly named. In a partner app this is what the user actually sees: 'we found your account, but eligibility ended on June 30th, 2024 — contact your HR.' That's a directed handoff, not a dead end. This distinction is what makes the API genuinely useful for a partner's support team."*

---

### 2c — NOT_FOUND *(proves: graceful no-match for non-members)*

**Sending:**
```json
{ "first_name": "Walter", "last_name": "White",
  "dob": "1959-09-07", "zip": "87104" }
```
*(Yes — Albuquerque ZIP, fictional schoolteacher. The audience may catch the joke.)*

**What this proves:** when somebody isn't a member at all, the API returns a different status from INELIGIBLE so the partner UI can show the right message — *"we couldn't find you, please double-check your inputs or contact support"* — rather than implying their coverage was once active.

**Why we expect NOT_FOUND:** no golden record has Walter's name + DOB + ZIP combination. Stage 1 (deterministic) returns zero matches. Stage 2 (fuzzy fallback on year-of-DOB + first 3 digits of ZIP + last name) also returns zero. The Lambda falls through to **NOT_FOUND**.

**Run:**
```bash
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d @../../samples/verify_not_found.json | python3 -m json.tool
```

**Look for:** `"status": "NOT_FOUND"`, `"score": 0.0`, `"golden_record_id": null`.

**🎙 Say after the response:**
> *"No-match path. Walter White isn't in our member directory — the system says so plainly. Same response shape as the previous two calls — VERIFIED, INELIGIBLE, AMBIGUOUS, NEEDS_REVIEW, NOT_FOUND all flow through one switch statement on the partner side. No special-casing per status."*

---

## Beat 3 — Show the data in DynamoDB (60 sec)

**🖥 Show on screen:** DynamoDB items tab — should already show the 5 seeded records as rows.

If the page lost session, re-open: https://us-east-1.console.aws.amazon.com/dynamodbv2/home?region=us-east-1#item-explorer?table=lore-elig-demo-golden-records

**🎙 Say (opening — tie back to Beat 2):**
> *"Those three verify calls just hit this table. This is the actual storage — five rows, one per member. Let me show you what one of them looks like."*

### Step 1 — open Robert's record (the VERIFIED one from 2a)

Click the row where `golden_record_id = G-0001`. The right panel expands.

**🎙 Point at and say:**
- *"`golden_record_id: G-0001` — primary key, internal use only."*
- *"`lookup_key: 90210#1962-04-12#smith` — that's the fingerprint we built. ZIP + DOB + last name lowercased, joined with `#` separators. **This is what the verify Lambda actually queried** to find Robert in sub-100ms."*
- *"`ssn_last4: 6789`. Notice we don't store the full SSN. Last-four only — and in production the full value gets tokenized through Skyflow before it ever hits any of our databases."*

### Step 2 — open Ethan's record (the INELIGIBLE one from 2b)

Scroll back to the row list, click `G-0005`.

**🎙 Point at and say:**
- *"`effective_end_date: 2024-06-30`. **This is the field that drove the INELIGIBLE response a minute ago.** The Lambda found Ethan by his fingerprint, then ran one more check — is this date in the past? It is — so the response flipped from VERIFIED to INELIGIBLE."*

### Step 3 — explain the secondary index

**🎙 Say:**
> *"DynamoDB has two ways into this table. The primary key is `golden_record_id` — a UUID. But no partner ever calls verify with `G-0001` — they only know the user's name, DOB, ZIP, last-4-of-SSN. So we built a **secondary index** on `lookup_key`. Same data, second access pattern. Both are sub-100ms reads."*

> *"In production this would be Aurora Postgres behind a Skyflow vault — same access pattern, different storage. DynamoDB is the demo analog because the access pattern is single-key reads against a denormalized table — exactly what DynamoDB is best at."*

---

## Beat 4 — Drop a partner file in S3, watch the full pipeline run (3 min)

> 📄 **Reference of what the YAML output will look like:** `samples/partner_acme_employer.schema.yaml`. Open this in a separate window/editor before the demo so you know which sections to point at when you click into the live one in S3 (PII tiers, cleansing rules per column, suggested partition column, overall quality risk).

**🖥 Show on screen:** S3 raw bucket tab, browsed to the bucket root. Terminal pane visible alongside.

**🎙 Say first:**
> *"Now the bigger story. Acme Corp sends us a CSV of their employees. We need to **(1)** land it, **(2)** parse and ingest the records into DynamoDB so they're verifiable through the same API, AND **(3)** generate a draft data-contract YAML — using Claude — that a data engineer can review and check into git for this partner. All event-driven, all from one file drop."*

**Pane 1 — start the file_processor log tail:**

```bash
aws logs tail /aws/lambda/lore-elig-demo-file-processor --follow --region us-east-1
```

Leave that running.

**🎙 Say (point at the S3 console):**
> *"Right now the raw bucket is empty under `inbox/acme-corp/`. I'm about to drop a real CSV — ten Acme employees — into that prefix from the terminal."*

**Pane 2 — drop the CSV:**

```bash
aws s3 cp ../../samples/partner_acme_employer.csv \
  s3://${RAW_BUCKET}/inbox/acme-corp/$(date +%s).csv
```

**🎙 Say (refresh the S3 console — show the file appear, then point at the log pane as it scrolls):**
> *"File landed. S3 fired an `ObjectCreated` event. That event triggered the file_processor Lambda. Watch the log — it's parsing the CSV, mapping columns into our canonical shape, dropping anything we don't have in the contract, stripping full SSN down to last-four for HIPAA data-minimization, and upserting ten records into DynamoDB. **Then it invokes the schema-inference Lambda** which sends the first 10 rows to Claude and gets back a structured data contract. Both outputs land in bronze."*

Wait until you see `schema contract written via mode=anthropic` in the log (~6 seconds total). Then Ctrl-C the tail.

### Step 1 — confirm the records landed in DynamoDB

```bash
aws dynamodb scan --table-name lore-elig-demo-golden-records \
  --select COUNT --region us-east-1 --output text --query 'Count'
```

**Expected:** `15` (was 5 before, +10 from the CSV).

**🎙 Say:**
> *"Five became fifteen. The ten Acme employees are now verifiable."*

### Step 2 — show the bronze bucket — both outputs side by side

**🖥 Switch to the S3 bronze bucket tab.**

Drill into `partner_id=acme-corp/dt=2026-04-29/`. You should see **two files** for the just-dropped timestamp:

- `1777482472.csv` — the parsed data, partitioned by partner + date for analytics queries
- `1777482472.csv.schema.yaml` — the draft data contract Claude generated

**🎙 Say (point at the two files):**
> *"Two outputs from one file drop. The CSV is the data — Athena can query this directly through Iceberg. The YAML is what Claude inferred from the column structure: canonical-field mappings, PII tiers, cleansing rules per column. **This is what cuts new-partner onboarding from five days of data engineering to one hour of human review.**"*

### Step 3 — open the schema YAML

Click the `.schema.yaml` file → **Open** (or the **Object actions → Query with S3 Select** if Open isn't enabled).

> 💡 The exact rules Claude returns vary slightly run-to-run (Haiku will phrase cleansing rules differently each time). The narration below points at things that are **always** in the output: the file-level fields, the PII tier classification, and the `reasoning` text per column. Open `samples/partner_acme_employer.schema.yaml` ahead of time to see a real example.

**🎙 Say (point at sections of the YAML):**

*Top of the file:*
> *"Claude detected this as `csv` format, suggested `EligStartDate` as the partition column for analytics queries, and rated overall data quality risk as MEDIUM."*

*Scroll down to the `SSN` column:*
> *"For SSN — the most sensitive column — Claude tagged it `TIER_1_DIRECT`, the highest PII tier, with cleansing rules to mask for logging, validate the 9-digit format, and strip hyphens before storage. In our production architecture we'd then layer Skyflow tokenization on top of these rules — the AI got us most of the way there."*

*Then the `DOB` column:*
> *"DOB also `TIER_1_DIRECT`. Confidence 0.99. Look at the `reasoning` field — Claude's own words: 'direct identifier when combined with name; requires vault protection.' That reasoning gets persisted with every record so an auditor can trace why a given column got the classification it did."*

*Scroll down to `PostalCode` (ZIP):*
> *"And here's the one I love. ZIP got tagged `TIER_2_QUASI` — quasi-identifier. Read the reasoning: 'combined with DOB and gender enables re-identification per HIPAA guidance.' **Claude is citing HIPAA's actual re-identification rule.** That's the kind of judgment that's hard to encode as a rule but easy for an LLM that's seen healthcare schemas at scale."*

*Wrap:*
> *"The data engineer reviews this YAML, fills in the `REPLACE_ME` fields at the top — partner_id, reviewer, date — and checks it into git as the official contract for this partner. From a five-day human task to a one-hour review of a draft Claude already wrote."*

> **🎙 Q&A backup — if asked *"so what does the YAML actually add to the pipeline?"*:**
>
> *"Three things, simply put:"*
>
> *"**One** — it turns a five-day engineering job into a one-hour review. Without the YAML, every new partner means an engineer manually reads the CSV, decides what each column means, classifies the PII risk, writes cleansing rules, ships it. With the YAML, Claude does that in ten seconds and the engineer just reviews."*
>
> *"**Two** — it makes PII handling automatic downstream. Once the contract is checked in, the rest of the pipeline reads the per-column tier tags and routes data accordingly: TIER_1 columns through Skyflow tokenization, TIER_2 with restricted analytics access, TIER_3 stored normally with audit logging. You can't accidentally store an unprotected SSN because the column was classified *before* the data flowed."*
>
> *"**Three** — it's the audit trail. Every column has Claude's reasoning attached — for ZIP it literally cited HIPAA's re-identification rule. When compliance asks 'why is ZIP a quasi-identifier?' three months from now, the answer is in version control next to the data, not in someone's head or a stale Confluence page."*
>
> *"One-liner: it's the AI's first draft of the contract that tells the rest of the pipeline how to handle this partner's data — what to tokenize, what to restrict, what to validate — with HIPAA-aware reasoning attached as the audit trail."*

### Step 4 — verify a person who only exists in that CSV

```bash
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d '{"first_name":"Jin","last_name":"Park","dob":"1978-03-22","zip":"11530","ssn_last4":"3456","partner_id":"acme-corp"}' \
  | python3 -m json.tool
```

**Expected:** `"status": "VERIFIED"` for Jin Park.

**🎙 Say:**
> *"Jin Park was a row in the CSV thirty seconds ago — not in the seeded golden records, came in entirely through the file flow. End-to-end: file lands → records ingested → schema inferred by Claude → records verifiable through the API. That's the contract every partner integration follows. Drop a file, get a working partner."*

---

## Beat 5 — Latency + cost story in CloudWatch (60 sec)

**🖥 Show on screen:** Lambda IDV API tab → click **Monitor** → **View CloudWatch metrics**.

Or jump directly to: https://us-east-1.console.aws.amazon.com/lambda/home?region=us-east-1#/functions/lore-elig-demo-idv-api?tab=monitoring

**🎙 Say:**
> *"This is the IDV Lambda's CloudWatch panel. Invocation count, duration p50/p99, error rate. Sub-100ms warm path. Zero errors across the demo. In production this is what your Datadog dashboards surface — the data is the same; the visualization is teammate preference."*

Point at duration p99 specifically. Then to error count = 0.

**🎙 Say:**
> *"Cost story while we're here: at five-thousand verifications per minute peak — what we model for Lore at scale — this Lambda costs about ninety dollars a month. The DynamoDB read traffic at single-key gets is another sixty. The whole cloud demo right now is running at about three dollars a month idle."*

---

## Beat 6 — Close (30 sec)

**🖥 Show on screen:** terminal or your architecture diagram, your call.

**🎙 Say:**
> *"Recap. Real HTTPS endpoint. Real DynamoDB. Real S3 event triggers. Real Lambda code shipping the same Pydantic models the local prototype uses. Three minutes to deploy from a clean account. The architecture diagram I showed earlier — this is exactly that slice. We swapped Snowflake for Athena plus Iceberg, Aurora for DynamoDB, Skyflow for KMS — but the data flow and the contracts are identical, and they're what this code is built around."*

---

## After the panel — DON'T FORGET

```bash
cd ~/Documents/lore-case-study/lore-eligibility-platform/infra/demo
./teardown-cli.sh
```

Wait for `All demo resources destroyed.`

Verify nothing remains (should print empty lines):

```bash
aws lambda list-functions --region us-east-1 --query 'Functions[?contains(FunctionName, `lore-elig-demo`)].FunctionName' --output text
aws s3api list-buckets --query 'Buckets[?contains(Name, `lore-elig-demo`)].Name' --output text
aws dynamodb list-tables --region us-east-1 --query 'TableNames[?contains(@, `lore-elig-demo`)]' --output text
```

---

## Recovery cards

**If Lambda returns 5xx mid-demo:**
```bash
aws logs tail /aws/lambda/lore-elig-demo-idv-api --since 2m --region us-east-1 | tail -40
```
Most likely cause: cold start on first call. Re-run the curl.

**If `$API` is empty:**
```bash
export API="https://$(aws apigatewayv2 get-apis --region us-east-1 \
  --query "Items[?Name=='lore-elig-demo-idv-api'].ApiId | [0]" --output text).execute-api.us-east-1.amazonaws.com"
```

**If you need to redeploy mid-demo (don't, but if you must):**
```bash
./deploy-cli.sh   # idempotent, ~90 sec; updates code in place
```

**If the S3 drop in Beat 4 doesn't trigger the Lambda within ~10s:**
Check the bucket notification config:
```bash
aws s3api get-bucket-notification-configuration --bucket "$RAW_BUCKET"
```
You should see a `LambdaFunctionConfigurations` entry pointing at file-processor. If it's empty, re-run `./deploy-cli.sh`.

**If anyone asks "where does Claude / the AI piece live?":**
The schema-inference Lambda (`lore-elig-demo-schema-inference`) is deployed and wired to call Claude via the Anthropic API for unknown partner shapes — first-time partners whose column names we haven't seen. We didn't demo it live because Acme's column shape is already in the file_processor's contract; the live moment-of-Claude story would need a brand-new partner format. Happy to demo it after the panel.
