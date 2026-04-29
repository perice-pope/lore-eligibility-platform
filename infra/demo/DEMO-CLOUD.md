# Cloud Demo Runbook — *"Walk me through onboarding a new partner"*

**~7 minutes. One narrative arc.** Acme Corp signed yesterday. Today they send their first employee file. Watch it land, get parsed, get understood by Claude, and become verifiable through the API — automatically, in roughly a minute.

---

## Pre-flight (15 min before the call)

Open a fresh terminal:

```bash
cd ~/Documents/lore-case-study/lore-eligibility-platform/infra/demo
```

Set your Anthropic API key (the schema-inference Lambda uses it for real Claude reasoning during Step 3):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # paste your key
```

Deploy fresh (idempotent — safe to re-run if anything looks off):

```bash
./deploy-cli.sh
```

Wait for `Cost meter started.` Then export the URLs you'll reuse:

```bash
export API="https://$(aws apigatewayv2 get-apis --region us-east-1 \
  --query "Items[?Name=='lore-elig-demo-idv-api'].ApiId | [0]" --output text).execute-api.us-east-1.amazonaws.com"
export ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
export RAW_BUCKET="lore-elig-demo-raw-${ACCOUNT}"
export BRONZE_BUCKET="lore-elig-demo-bronze-${ACCOUNT}"
echo "API=$API"
```

Smoke test:

```bash
curl -s $API/healthz && echo
```
Expected: `ok`

**Pre-demo state should be clean** — 5 seeded records, both S3 buckets empty:

```bash
aws dynamodb scan --table-name lore-elig-demo-golden-records --region us-east-1 --select COUNT --output text --query 'Count'   # → 5
aws s3 ls s3://${RAW_BUCKET} --recursive | wc -l    # → 0
aws s3 ls s3://${BRONZE_BUCKET} --recursive | wc -l # → 0
```

If counts aren't right, run the **Reset** snippet at the bottom of this doc.

**Open these AWS console tabs (account `185529490129`, region `us-east-1`):**

| Tab | URL | When |
|---|---|---|
| S3 raw bucket | https://us-east-1.console.aws.amazon.com/s3/buckets/lore-elig-demo-raw-185529490129?region=us-east-1 | Step 1 (before drop) |
| S3 bronze bucket | https://us-east-1.console.aws.amazon.com/s3/buckets/lore-elig-demo-bronze-185529490129?region=us-east-1 | Step 2/3 (after drop) |
| DynamoDB items | https://us-east-1.console.aws.amazon.com/dynamodbv2/home?region=us-east-1#item-explorer?table=lore-elig-demo-golden-records | Step 5 |

Also have `samples/partner_acme_employer.schema.yaml` open in your editor as a reference for what the live YAML will look like.

---

## The framing (30 sec — say this BEFORE you switch to the terminal)

**🖥 Show on screen:** the *"Demo architecture"* slide.

**🎙 Say:**
> *"Here's the question I want to answer live: when a brand-new partner joins Lore, what actually happens to get them up and running? Walk through it with me."*
>
> *"Acme Corp signed yesterday. Today they're sending us their first employee file — ten people their HR wants set up so they can sign up for Lore care. They drop one CSV in S3. Watch what unfolds: the file gets parsed and ingested into our member directory; the rest of the pipeline asks Claude to read the column structure and write a draft data contract for our data engineer to review; and within about a minute, every Acme employee in that file can verify through the same API a partner mobile app would use. End to end."*

Then switch to the terminal and the S3 raw bucket tab.

---

## Step 1 — Acme's CSV lands in S3 *(45 sec)*

**🖥 Show:** S3 raw bucket tab (empty under `inbox/acme-corp/`) + terminal.

**🎙 Say:**
> *"This is the raw landing bucket. Right now `inbox/acme-corp/` is empty — Acme hasn't sent anything yet. I'm about to drop their employee file in via the same SFTP/S3 path a real partner would use."*

**Run:**

```bash
TS=$(date +%s)
aws s3 cp ../../samples/partner_acme_employer.csv \
  s3://${RAW_BUCKET}/inbox/acme-corp/${TS}.csv
echo "TS=${TS}"
```

**Refresh the S3 console** — the file is there.

**🎙 Say:**
> *"File landed. S3 just fired an `ObjectCreated` notification. That event is wired to a Lambda. No polling, no cron — pure event-driven. Watch."*

---

## Step 2 — Pipeline kicks off automatically *(60 sec)*

**🖥 Show:** terminal (start the log tail).

**Tail the file_processor logs:**

```bash
aws logs tail /aws/lambda/lore-elig-demo-file-processor --follow --region us-east-1
```

Within 5–15 seconds you'll see lines like:
- `processing s3://...inbox/acme-corp/<ts>.csv`
- `parsed 10 rows for partner=acme-corp`
- `copied to s3://...bronze.../partner_id=acme-corp/dt=...`
- `schema contract written via mode=anthropic model=claude-haiku-4-5 -> ...schema.yaml`

Once that last line appears, **Ctrl-C the tail.**

**🎙 Say while logs scroll:**
> *"The file_processor Lambda is doing three things in sequence: parsing the CSV and upserting ten records into DynamoDB so the IDV API can verify them; copying the file to bronze, partitioned by partner and date for analytics; and invoking a second Lambda that hands the same column structure to Claude via the Anthropic API. Claude returns a draft data contract — column-by-column mapping, PII tier classifications, cleansing rules — which gets written back to bronze as a YAML sidecar next to the data. We'll look at that next."*

**Confirm count went up:**

```bash
aws dynamodb scan --table-name lore-elig-demo-golden-records --region us-east-1 --select COUNT --output text --query 'Count'
```

**Expected:** `15` (was 5; +10 from Acme's file).

---

## Step 3 — Claude's draft data contract *(90 sec)*

**🖥 Switch to the S3 bronze bucket tab.**

Drill into `partner_id=acme-corp/dt=<today>/`. You'll see **two files** for the just-dropped timestamp:

- `<ts>.csv` — the parsed partner data
- `<ts>.csv.schema.yaml` — Claude's draft contract

> 💡 *Wording in the YAML varies slightly run-to-run (Haiku rephrases cleansing rules each call). The narration below points at things that are always there.* Reference: `samples/partner_acme_employer.schema.yaml`.

**🎙 Say (point at the two files):**
> *"Two outputs from one file drop. The CSV is the data — Athena queries this directly through Iceberg. The YAML is what Claude inferred. Let's open it."*

Click `<ts>.csv.schema.yaml` → **Open** (or **Object actions → Query with S3 Select**).

**🎙 Walk through the YAML:**

*Top of the file:*
> *"Claude detected this as `csv` format, suggested `EligStartDate` as the partition column for analytics queries, and rated overall data quality risk as MEDIUM."*

*Scroll to the `SSN` column:*
> *"For SSN — the most sensitive column — Claude tagged it `TIER_1_DIRECT`, the highest PII tier, with cleansing rules to mask, validate the 9-digit format, and strip hyphens. In our production architecture we'd then layer Skyflow tokenization on top of these rules — the AI got us most of the way there."*

*Then the `DOB` column:*
> *"DOB also `TIER_1_DIRECT`. Confidence 0.99. Look at the `reasoning` field — Claude's own words: 'direct identifier when combined with name; requires vault protection.' That reasoning gets persisted with every record so an auditor can trace why a column got the classification it did."*

*Scroll to `PostalCode` (ZIP):*
> *"And here's the one I love. ZIP got `TIER_2_QUASI`. Read the reasoning: 'combined with DOB and gender enables re-identification per HIPAA guidance.' **Claude is citing HIPAA's actual re-identification rule.** That's the kind of judgment that's hard to encode but easy for an LLM that's seen healthcare schemas at scale."*

*Wrap:*
> *"The data engineer reviews this, fills the `REPLACE_ME` fields at the top — partner_id, reviewer, date — and merges it as the canonical contract for Acme. From a five-day human task to a one-hour review of a draft Claude already wrote."*

> **🎙 Q&A backup — if asked *"so what does the YAML actually add to the pipeline?"*:**
>
> *"Three things, simply put:"*
>
> *"**One** — turns a five-day engineering job into a one-hour review."*
>
> *"**Two** — once the contract is checked in, the rest of the pipeline reads the per-column tier tags and routes data accordingly: TIER_1 columns through Skyflow tokenization, TIER_2 with restricted analytics access, TIER_3 stored normally with audit logging. You can't accidentally store an unprotected SSN — the column was classified before the data flowed."*
>
> *"**Three** — it's the audit trail. Claude's reasoning lives in version control next to the data. When compliance asks 'why is ZIP a quasi-identifier?' three months from now, the answer is right there."*

---

## Step 4 — A brand-new Acme employee verifies through the API *(60 sec)*

**🖥 Switch back to terminal.**

**🎙 Say:**
> *"Last step. Acme just landed in our system thirty seconds ago. Let me verify one of their employees — Jin Park — through the same IDV API a partner mobile app would call. Jin wasn't in the seeded golden records; he came in entirely through that file flow."*

**Run:**

```bash
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d '{"first_name":"Jin","last_name":"Park","dob":"1978-03-22","zip":"11530","ssn_last4":"3456","partner_id":"acme-corp"}' \
  | python3 -m json.tool
```

**Expected:** `"status": "VERIFIED"`, `"golden_record_id": "G-EEB5"`, `"score": 1.0`, `"detail": {"stage": "deterministic"}`.

**🎙 Say:**
> *"VERIFIED. End-to-end: file landed → records ingested → schema understood by Claude → Jin verifiable. Same path every partner, every file, forever. **About a minute total elapsed time** for what's traditionally a multi-day engineering ticket."*

---

## Step 5 — *(Optional)* Show the actual record in DynamoDB *(45 sec)*

**🖥 Switch to the DynamoDB items tab** and refresh.

You'll see 15 rows now (5 seeded + 10 ingested). All ingested records have IDs like `G-EEB5`, `G-58FE` — same format as the seeded `G-0001`..`G-0005`.

Click the row `G-EEB5` (Jin Park).

**🎙 Point at and say:**
- *"`golden_record_id: G-EEB5` — Jin's canonical ID. Deterministic — built from `partner_id` + `partner_member_id`, so re-running the file produces the same ID every time."*
- *"`lookup_key: 11530#1978-03-22#park` — that's the fingerprint the verify Lambda just queried on a Global Secondary Index. ZIP + DOB + last name lowercased, joined with `#`. Sub-100ms reads."*
- *"`ssn_last4: 3456`. Notice we don't store the full SSN. Last-four only — the full value gets tokenized through Skyflow in the production architecture before it ever hits a database."*

---

## Wrap *(30 sec)*

**🎙 Say:**
> *"Quick recap. Acme went from zero to fully integrated in one minute. Their employees are now verifiable through the same API; the pipeline wrote a draft data contract our engineer reviews in an hour instead of a week; that contract becomes self-driving for every future Acme file. The architecture is the same one a payer or ACO would use — Acme is just a CSV; a different partner could be JSON or EDI 834 and the flow is identical because the contract layer is what the pipeline actually reads."*

---

## Optional add-ons (only if you have time or get specific questions)

### Three sign-up scenarios for the verify API

If asked *"what other verification cases does it handle?"*:

```bash
# VERIFIED via nickname (Bob → Robert Smith / G-0001)
curl -sX POST $API/v1/verify -H 'content-type: application/json' \
  -d @../../samples/verify_nickname.json | python3 -m json.tool

# INELIGIBLE — Ethan / G-0005, his coverage ended 2024-06-30
curl -sX POST $API/v1/verify -H 'content-type: application/json' \
  -d @../../samples/verify_ineligible.json | python3 -m json.tool

# NOT_FOUND — Walter White, fictional
curl -sX POST $API/v1/verify -H 'content-type: application/json' \
  -d @../../samples/verify_not_found.json | python3 -m json.tool
```

The verify endpoint anchors on `(DOB + ZIP + last name + SSN-last-4)` — first name is informational only. Five possible statuses: VERIFIED, INELIGIBLE, AMBIGUOUS, NEEDS_REVIEW, NOT_FOUND.

### CloudWatch latency / cost

If asked *"what about observability and cost?"*:

Lambda IDV API → **Monitor** → CloudWatch metrics. Sub-100ms warm path, zero errors. Cost model at 5K verifications/min: ~$90/mo for the Lambda, ~$60/mo for DynamoDB; idle demo runs ~$3/mo.

---

## Q&A backup answers

**"What happens after the contract is reviewed and merged?"**
> *"Acme is 'self-driving' from then on. Every future file drop hits the same pipeline; the file_processor reads `contracts/acme-corp/v1.yaml` instead of hard-coded mappings; TIER_1 columns automatically tokenize through Skyflow; rows that don't match the contract go to a dead-letter queue. Validated records flow two places: DynamoDB for the verify hot-path, Iceberg in bronze for analytics. Nightly entity-resolution runs the 3-stage matcher — deterministic → embedding similarity → Claude adjudication on borderline pairs — to dedupe identities across all partners."*

**"Where does each field in the verify response come from?"**
> *"`status`, `correlation_id`, `score`, `decision_basis`, `detail.stage` — all minted or computed by the Lambda for that specific request. `golden_record_id` and `partner_id` come straight from the row in DynamoDB. The correlation ID is what a support agent searches by in CloudWatch."*

**"Why do verify and ingest fields not match exactly?"**
> *"They do at the canonical layer. The CSV has `EmployeeID`, `FirstName`, `LastName`, `DOB`, `SSN`, etc. The file_processor maps those into the canonical schema (`partner_member_id`, `first_name`, `last_name`, `dob`, `ssn_last4`) using the data contract — that's literally what the YAML defines. The verify API queries on the canonical schema."*

---

## Recovery cards

**If Lambda returns 5xx mid-demo:**
```bash
aws logs tail /aws/lambda/lore-elig-demo-idv-api --since 2m --region us-east-1 | tail -40
```
Most likely cause: cold start on first call. Re-run the curl.

**If `$API`, `$RAW_BUCKET`, or `$BRONZE_BUCKET` is empty:**
Re-run the export block from Pre-flight.

**If the S3 drop in Step 1 doesn't trigger the Lambda within ~10s:**
```bash
aws s3api get-bucket-notification-configuration --bucket "$RAW_BUCKET"
```
You should see a `LambdaFunctionConfigurations` entry pointing at file-processor. If empty, re-run `./deploy-cli.sh`.

**If the schema YAML in Step 3 says `(local_mock)` instead of `(anthropic)`:**
The Lambda's `ANTHROPIC_API_KEY` env var didn't land. Fix:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
./deploy-cli.sh   # picks up the key, updates Lambda env
```

**If you need to redeploy mid-demo (don't, but if you must):**
```bash
./deploy-cli.sh   # idempotent, ~90 sec; updates code in place
```

---

## Reset to clean pre-demo state

If artifacts piled up from rehearsals and you need DDB back to 5 + buckets empty:

```bash
aws s3 rm s3://${RAW_BUCKET} --recursive
aws s3 rm s3://${BRONZE_BUCKET} --recursive
python3 - <<'PY'
import boto3
table = boto3.resource("dynamodb", region_name="us-east-1").Table("lore-elig-demo-golden-records")
items = []
resp = table.scan(ProjectionExpression="golden_record_id")
items.extend(resp["Items"])
while "LastEvaluatedKey" in resp:
    resp = table.scan(ProjectionExpression="golden_record_id", ExclusiveStartKey=resp["LastEvaluatedKey"])
    items.extend(resp["Items"])
with table.batch_writer() as batch:
    for it in items:
        batch.delete_item(Key={"golden_record_id": it["golden_record_id"]})
print(f"deleted {len(items)} items")
PY
python3 seed/seed_dynamodb.py --table lore-elig-demo-golden-records --region us-east-1
```

---

## After the panel — DON'T FORGET

```bash
cd ~/Documents/lore-case-study/lore-eligibility-platform/infra/demo
./teardown-cli.sh
```

Wait for `All demo resources destroyed.` Then verify nothing remains:

```bash
aws lambda list-functions --region us-east-1 --query 'Functions[?contains(FunctionName, `lore-elig-demo`)].FunctionName' --output text
aws s3api list-buckets --query 'Buckets[?contains(Name, `lore-elig-demo`)].Name' --output text
aws dynamodb list-tables --region us-east-1 --query 'TableNames[?contains(@, `lore-elig-demo`)]' --output text
```

All three should print empty. Cost meter stops.
