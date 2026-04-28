# Cloud Demo Runbook — what to type during the panel

**10 minutes. Live AWS. Real Bedrock calls.**

## Before the panel (do these the day before)

1. **`./deploy.sh`** — one time, confirm everything works end-to-end. Capture the API URL.
2. **`./teardown.sh`** — destroy it. You'll redeploy fresh on demo day.
3. **Verify Bedrock quotas** — `aws bedrock list-inference-profiles --region us-east-1 | grep claude-sonnet-4-6` returns the profile.
4. Save the panel-day commands below to a Note app for fast paste-during-call.

## Demo-day pre-flight (15 min before the call)

```bash
cd infra/demo
./deploy.sh
```

Wait for the green "Deploy complete" message. Save the URL it prints.

```bash
export API="$(terraform output -raw idv_api_url)"
echo $API
```

Open these in browser tabs (the script prints the URLs):
- **DynamoDB console** showing the golden_records table — switch to it during beat 3
- **CloudWatch log groups** for the IDV API — switch to it during beat 5

## The demo itself (10 min)

### Beat 1 — Show the live HTTPS URL (60 sec)

> "Everything you're about to see runs on real AWS infrastructure I deployed about 15 minutes ago.
> Lambda + API Gateway for the IDV API. DynamoDB for the golden record store. S3 buckets ready
> to receive partner files. Real Bedrock model access for the AI features."

```bash
echo $API
curl -s $API/healthz
# → ok
curl -s $API/readyz | python3 -m json.tool
# → shows backend=dynamodb, real table connected
```

### Beat 2 — Identity verification, three scenarios (2 min)

```bash
# VERIFIED — Bob is a nickname for Robert; the system normalizes and matches G-0001
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d @../../samples/verify_nickname.json | python3 -m json.tool
```

> "This member typed 'Bob' for first name. The deterministic match on DOB + ZIP + SSN-last-4
> succeeded. Sub-150ms p99, served from DynamoDB. The correlation ID is what support uses if
> this person calls in later. Notice the response doesn't include any PII."

```bash
# INELIGIBLE — coverage end date is in the past
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d @../../samples/verify_ineligible.json | python3 -m json.tool
```

> "Found in our system. Different status from NOT_FOUND. The mobile app would show 'we found
> your account but eligibility ended; contact your HR.'"

```bash
# NOT_FOUND
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d @../../samples/verify_not_found.json | python3 -m json.tool
```

### Beat 3 — Show the data in DynamoDB (60 sec)

**Switch to DynamoDB console tab.**

> "Behind that API: a DynamoDB table. Five golden records I seeded from the same JSON file
> the local prototype uses. Notice the lookup_key column — that's the GSI we query for
> exact-match identity verification. zip + DOB + last name lowercased."

Click into a record. Show the schema.

### Beat 4 — Real Bedrock schema inference (2 min)

```bash
SAMPLE=$(python3 -c "
import csv, json, sys
with open('../../samples/partner_acme_employer.csv') as f:
    rows = list(csv.DictReader(f))[:10]
print(json.dumps({'filename':'partner_acme_employer.csv', 'sample': rows, 'mode':'bedrock'}))
")
echo "$SAMPLE" > /tmp/inference_payload.json

aws lambda invoke \
  --function-name lore-elig-demo-schema-inference \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/inference_payload.json \
  /tmp/inference_response.json

cat /tmp/inference_response.json | python3 -c "import sys, json; print(json.load(sys.stdin)['body'])" | python3 -m json.tool | head -80
```

> "I'm sending 10 rows from a partner CSV — never seen by Claude before — to a Lambda that
> calls Bedrock. The response includes a column-by-column mapping with PII tier and confidence,
> plus a draft data-contract YAML. **In production this is what cuts new partner onboarding from
> five days to one hour of human review.**"

If Bedrock throttles, the response shows `"mode": "local_mock"` and the panel still gets a
valid output — same schema, just from the deterministic heuristic. Acknowledge it:

> "Bedrock has tight per-day quotas on new accounts. The system falls back to a deterministic
> classifier with the same output schema. In a production Lore environment we'd have provisioned
> throughput contracts so this never matters."

### Beat 5 — Drop a partner file in S3, watch it flow (2 min)

```bash
# In one terminal pane — start tailing the file-processor Lambda logs
aws logs tail /aws/lambda/lore-elig-demo-file-processor --follow --region us-east-1 &
TAIL_PID=$!
```

```bash
# In another pane — drop the partner CSV
aws s3 cp ../../samples/partner_acme_employer.csv \
  s3://lore-elig-demo-raw-$(aws sts get-caller-identity --query Account --output text)/inbox/acme-corp/$(date +%s).csv
```

> "Dropped a CSV in `s3://...raw.../inbox/acme-corp/`. EventBridge triggers the file-processor
> Lambda. Within seconds, you'll see it parse the CSV, extract canonical fields, drop unknown
> columns (data minimization), strip raw SSN to last-4 only, and upsert 10 records to DynamoDB."

Wait ~5 sec, you'll see the log lines.

```bash
# Stop the log tail
kill $TAIL_PID 2>/dev/null
```

```bash
# Verify the new records appeared
aws dynamodb scan --table-name lore-elig-demo-golden-records \
  --select COUNT --region us-east-1
```

> "Item count went up. The 10 Acme employees are now verifiable through the IDV API."

```bash
# Curl with one of the newly-loaded employees
curl -sX POST $API/v1/verify \
  -H 'content-type: application/json' \
  -d '{"first_name":"Maria","last_name":"Garcia-Lopez","dob":"1985-09-30","zip":"78701","ssn_last4":"4321"}' \
  | python3 -m json.tool
```

### Beat 6 — Show the latency in CloudWatch (60 sec)

**Switch to CloudWatch tab.** Pull up the IDV Lambda's metrics — show p99 latency under 100ms
and zero errors. Mention this is what your Datadog dashboards would surface in production.

### Beat 7 — Close (30 sec)

> "Recap: real HTTPS URL, real DynamoDB, real Bedrock, real S3-triggered processing, all on
> AWS, all deployed from Terraform in three minutes. The architecture diagram I showed earlier
> is what this slice maps to: the demo trades Snowflake for Athena + Iceberg, Aurora for
> DynamoDB, and Skyflow for KMS-encrypted token storage — but the data flow and the contracts
> are identical."

## After the panel — DON'T FORGET

```bash
./teardown.sh
```

Wait for the green "All demo resources destroyed" message.
