# Cloud Demo — `infra/demo/`

A scoped subset of the architecture deployable to a free-tier AWS account, designed
specifically for the panel demo. Pure AWS — no Snowflake, no Skyflow, no Datadog.

## What this deploys

| Resource | Purpose | Production analog |
|---|---|---|
| **API Gateway HTTP API** + **Lambda (FastAPI/Mangum)** | The IDV API on a live `https://...` URL | ECS Fargate behind ALB |
| **DynamoDB table** + lookup_key GSI | Golden record store | Aurora PostgreSQL |
| **S3 raw bucket** + S3-trigger Lambda | "Drop a partner CSV → records appear in DDB" pipeline | EMR Serverless on Iceberg |
| **S3 bronze bucket** | Iceberg-ready destination for parsed files | Same — Iceberg on S3 |
| **Schema-inference Lambda** | Real Bedrock Claude calls during the demo | Same |
| **IAM execution role** | Least-privilege: DDB R/W, S3 R/W, Bedrock invoke | Same |
| **CloudWatch log groups** | 7-day retention | Datadog in production |

Bedrock model is **`us.anthropic.claude-sonnet-4-6`** (cross-region inference profile).
Embedding model is **`amazon.titan-embed-text-v2:0`**.

## Cost

| Scenario | Cost |
|---|---|
| One 1-hour panel demo | **under $1** (mostly Bedrock token usage) |
| Idle 24/7 if you forget to tear down | **~$3–8/month** (Lambda cold starts, CloudWatch logs, DDB free tier) |

The teardown script verifies nothing is left behind.

## Prerequisites

1. **AWS CLI v2** installed and configured (`aws sts get-caller-identity` works)
2. **Terraform ≥ 1.7** installed
3. **Bedrock model access** — see the main [README.md](../../README.md). The `us.anthropic.claude-sonnet-4-6` inference profile must work; daily quota for Claude Sonnet 4.6 must be ≥ 0 (defaults are usually fine for one demo).
4. **Region**: `us-east-1` (broadest Bedrock availability). Override with `AWS_REGION=...` if needed.

## Deploy

```bash
cd infra/demo
./deploy.sh
```

Takes ~3 minutes. Prints the live URL plus console links to bring up during the demo.

## Tear down (after the panel)

```bash
./teardown.sh
```

Verifies no resources remain. **Always run this after the demo.** Lambda + CloudWatch costs accrue while idle.

## Files

```
infra/demo/
├── README.md             ← this file
├── DEMO-CLOUD.md         ← what to type during the panel demo
├── versions.tf
├── variables.tf
├── main.tf               ← the whole architecture in one file
├── outputs.tf
├── deploy.sh             ← build + apply + seed + verify
├── teardown.sh           ← empty + destroy + verify clean
├── lambdas/
│   ├── idv_api/          ← FastAPI app via Mangum, DynamoDB-backed
│   ├── file_processor/   ← S3 trigger → DDB upserts + bronze copy
│   └── schema_inference/ ← real Bedrock Claude call demo
└── seed/
    └── seed_dynamodb.py  ← loads samples/golden_records_seed.json
```

## Troubleshooting

**`AccessDeniedException` invoking the model from inside Lambda** — check the Service Quotas
console for Sonnet 4.6 cross-region tokens-per-minute. Default for new accounts is 0; you
have to explicitly request the bump to the AWS default.

**`ThrottlingException`** — quota recovered too slowly. Wait a minute and retry, or request
a Service Quota increase.

**`/healthz` returns 502** — Lambda cold start. First request after `deploy.sh` can take 3–5
seconds to compile the FastAPI app. Subsequent requests are sub-second.

**S3 bucket already exists** — bucket names are global. The script names buckets with your
account ID, so collision is rare; if it happens, change `var.project` in `variables.tf`.

**`terraform destroy` fails on non-empty S3 bucket** — the teardown script empties buckets
first; if you ran `terraform destroy` directly, run `aws s3 rm s3://<bucket> --recursive`
then retry.
