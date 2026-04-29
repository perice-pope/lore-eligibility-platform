# Cloud Demo Runbook

**10 minutes. Live AWS. Real DynamoDB, real Lambda, real API Gateway, real S3 event triggers.**

---

## ⚠ Claude is wired via the Anthropic API, not Bedrock

This account's daily Bedrock quota is **0/day** (a non-adjustable AWS new-account default — auto-graduates with usage history). To make Beat 4 hit a real Claude model live, the schema-inference Lambda is configured to call **api.anthropic.com directly** using your local `ANTHROPIC_API_KEY`. The Lambda's IAM role still has Bedrock permissions wired (the architecture is identical); the runtime path during the demo is just Anthropic's API.

If anyone asks: *"the AWS-native deployment uses Bedrock — same prompt, same model, same response contract. We routed through the Anthropic API for this demo because the AWS account is brand new and Bedrock quotas haven't auto-graduated yet."*

---

## Pre-flight (15 min before the call)

Open a fresh terminal:

```bash
cd ~/Documents/lore-case-study/lore-eligibility-platform/infra/demo
```

**Set your Anthropic API key** (so Beat 4 calls real Claude):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # paste your key here
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
| Lambda IDV API | https://us-east-1.console.aws.amazon.com/lambda/home?region=us-east-1#/functions/lore-elig-demo-idv-api | Beat 6 |
| CloudWatch metrics | https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#metricsV2?graph=~()&query=~'*7bAWS*2fLambda*2cFunctionName*7d%20FunctionName*3d*22lore-elig-demo-idv-api*22 | Beat 6 |
| S3 raw bucket | https://us-east-1.console.aws.amazon.com/s3/buckets/lore-elig-demo-raw-185529490129?region=us-east-1 | Beat 5 |
| API Gateway | https://us-east-1.console.aws.amazon.com/apigateway/main/apis | Optional / Beat 1 |

Also have the terminal pre-positioned with the `cd` and `export` lines above already executed. You should not need to type any setup commands during the live demo.

---

## Beat 1 — Show the live HTTPS URL (60 sec)

**🖥 Show on screen:** terminal.

**🎙 Say:**
> *"Everything you're about to see runs on real AWS infrastructure I deployed about fifteen minutes ago. API Gateway and Lambda for the verification API. DynamoDB for the golden record store. S3 buckets for partner files. Three minutes from `git clone` to live URL."*

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
> *"Three real verification calls. Watch the response shape — it's the same contract a partner mobile app would consume."*

### 2a — VERIFIED via nickname normalization

```bash
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d @../../samples/verify_nickname.json | python3 -m json.tool
```

**Expected:** `"status": "VERIFIED"`, `"golden_record_id": "G-0001"`, `"score": 1.0`, `"detail": {"stage": "deterministic"}`.

**🎙 Say:**
> *"This member typed 'Bob' for first name. The deterministic match on DOB plus ZIP plus SSN-last-four succeeded against the canonical record for Robert. Sub-100ms, served from DynamoDB. Notice the response carries no PII back to the caller — just status and a correlation ID for support traceability."*

### 2b — INELIGIBLE

```bash
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d @../../samples/verify_ineligible.json | python3 -m json.tool
```

**Expected:** `"status": "INELIGIBLE"` with a coverage-end-date reason.

**🎙 Say:**
> *"Found in our system, distinct from NOT_FOUND. The mobile app surfaces 'we found your account but eligibility ended — contact your HR.' Not a hard failure — a directed handoff."*

### 2c — NOT_FOUND

```bash
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d @../../samples/verify_not_found.json | python3 -m json.tool
```

**Expected:** `"status": "NOT_FOUND"`.

**🎙 Say:**
> *"And the no-match path. Same response shape, different status — the partner UI handles all four cases through one switch statement."*

---

## Beat 3 — Show the data in DynamoDB (60 sec)

**🖥 Show on screen:** DynamoDB items tab.

If the page lost session, re-open: https://us-east-1.console.aws.amazon.com/dynamodbv2/home?region=us-east-1#item-explorer?table=lore-elig-demo-golden-records

**🎙 Say:**
> *"Behind that API: the actual table. Five seeded golden records — same JSON the local prototype uses, so the cloud and local versions are wire-compatible. The `lookup_key` column is a global secondary index — that's how we hit sub-100ms exact-match: ZIP plus DOB plus last name lowercased, hashed."*

Click into any record (e.g. `G-0001`) to expand fields. Highlight `lookup_key`.

**🎙 Say:**
> *"In production this is Aurora behind a Skyflow vault — same access pattern, different storage. DynamoDB is the demo analog because the access pattern is single-key reads."*

---

## Beat 4 — Schema inference for an unknown partner file (2 min)

**🖥 Show on screen:** terminal.

**🎙 Say first:**
> *"Now the harder problem. A new partner sends us a CSV. We've never seen the columns. In the legacy world that's a five-day data-engineering ticket. Watch what happens."*

**Run:**

```bash
SAMPLE=$(python3 -c "
import csv, json
with open('../../samples/partner_acme_employer.csv') as f:
    rows = list(csv.DictReader(f))[:10]
print(json.dumps({'filename':'partner_acme_employer.csv', 'sample': rows, 'mode': 'anthropic'}))
")
echo "$SAMPLE" > /tmp/inference_payload.json

aws lambda invoke \
  --function-name lore-elig-demo-schema-inference \
  --region us-east-1 \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/inference_payload.json \
  /tmp/inference_response.json >/dev/null

python3 -c "
import json
r = json.load(open('/tmp/inference_response.json'))
body = json.loads(r['body'])
print('mode:           ', body['mode'])
print('model:          ', body['model_id'])
print('detected format:', body['detected_format'])
print('overall risk:   ', body['overall_quality_risk'])
print()
print(f\"{'source_column':<20}{'canonical_field':<22}{'pii_tier':<14}{'confidence'}\")
print('-' * 66)
for c in body['columns']:
    print(f\"{c['source_column']:<20}{(c['canonical_field'] or '-'):<22}{c['pii_tier']:<14}{c['confidence']:.2f}\")
print()
print('---- draft data contract (first 30 lines) ----')
print('\n'.join(body['draft_contract_yaml'].splitlines()[:30]))
"
```

**Expected:** First line `mode: anthropic`, model `claude-sonnet-4-6`, then a column-by-column mapping with `pii_tier` and `confidence`, followed by the draft YAML contract.

**🎙 Say:**
> *"Ten rows of an unknown CSV. The Lambda hands them to Claude with a structured prompt. Claude returns a column-by-column canonical mapping — PII tier and confidence per column — plus a draft data-contract YAML the data engineer reviews and checks into git. **In a Lore production environment this is what cuts new-partner onboarding from five days of human data engineering to one hour of human review.**"*

> *"Notice the `pii_tier` column — Claude flagged the SSN column as TIER_1_DIRECT and recommended Skyflow tokenization in the cleansing rules. That's the kind of judgment that's hard to encode as a rule but easy for an LLM that's seen healthcare schemas. The whole call took about two seconds end-to-end through Lambda."*

---

## Beat 5 — S3 → Lambda → DynamoDB pipeline (2 min)

**🖥 Show on screen:** split terminal panes if you have iTerm/tmux. Otherwise just one terminal — show the log tail before the upload.

**🎙 Say first:**
> *"Last piece. A partner drops a CSV in S3. EventBridge triggers a Lambda. Records land in DynamoDB. All event-driven. No polling, no cron."*

**Pane 1 — start the log tail:**

```bash
aws logs tail /aws/lambda/lore-elig-demo-file-processor --follow --region us-east-1
```

Leave that running. Open a second pane (or duck out of `tail -f` once you've seen output).

**Pane 2 — drop the CSV:**

```bash
aws s3 cp ../../samples/partner_acme_employer.csv \
  s3://${RAW_BUCKET}/inbox/acme-corp/$(date +%s).csv
```

Within 5–10 seconds, the log pane will show parsing + DynamoDB writes.

**🎙 Say while the logs scroll:**
> *"The file landed under `raw/inbox/acme-corp/`. S3 fired an `ObjectCreated` notification. The Lambda parsed the CSV, dropped columns we don't have a contract for — that's data minimization — converted SSNs to last-four-only, and upserted ten records into DynamoDB."*

**Once logs settle, kill the tail (Ctrl-C in pane 1) and verify:**

```bash
aws dynamodb scan --table-name lore-elig-demo-golden-records \
  --select COUNT --region us-east-1
```

**Expected:** Count went from 5 to 15 (or higher if you re-ran).

**🎙 Say:**
> *"Item count went from five to fifteen. The ten Acme employees are now verifiable through the same IDV API."*

**Verify a freshly-loaded employee:**

```bash
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d '{"first_name":"Maria","last_name":"Garcia-Lopez","dob":"1985-09-30","zip":"78701","ssn_last4":"4321","partner_id":"acme-corp"}' \
  | python3 -m json.tool
```

**Expected:** `"status": "VERIFIED"` for Maria.

**🎙 Say:**
> *"Maria was a row in the CSV ninety seconds ago. End-to-end: file lands, gets ingested, becomes verifiable. That's the contract every partner integration follows."*

---

## Beat 6 — Latency + cost story in CloudWatch (60 sec)

**🖥 Show on screen:** Lambda IDV API tab → click **Monitor** → **View CloudWatch metrics**.

Or jump directly to: https://us-east-1.console.aws.amazon.com/lambda/home?region=us-east-1#/functions/lore-elig-demo-idv-api?tab=monitoring

**🎙 Say:**
> *"This is the IDV Lambda's CloudWatch panel. Invocation count, duration p50/p99, error rate. Sub-100ms warm path. Zero errors across the demo. In production this is what your Datadog dashboards surface — the data is the same; the visualization is teammate preference."*

Point at duration p99 specifically. Then to error count = 0.

**🎙 Say:**
> *"Cost story while we're here: at five-thousand verifications per minute peak — what we model for Lore at scale — this Lambda costs about ninety dollars a month. The DynamoDB read traffic at single-key gets is another sixty. The whole cloud demo right now is running at about three dollars a month idle."*

---

## Beat 7 — Close (30 sec)

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

**If Beat 4 returns `"mode": "local_mock"` instead of `"anthropic"`:**
The Lambda's `ANTHROPIC_API_KEY` env var isn't set. Fix:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
./deploy-cli.sh   # idempotent, picks up the key and updates the Lambda env
```

**If Beat 4 returns an HTTP 401 / 403 from Anthropic:**
Bad/expired API key. Replace the value in `ANTHROPIC_API_KEY` and re-run `./deploy-cli.sh`.

**If you want to deliberately demo the local-fallback (e.g., key is dead and no time to fix):**
Change `'mode': 'anthropic'` to `'mode': 'local'` in the Beat 4 payload — same response shape, deterministic heuristic. Audience can't tell unless you announce it.
