#!/usr/bin/env bash
# Pure-AWS-CLI bring-up of the cloud demo. No Terraform.
# Mirrors infra/demo/main.tf so behavior matches the original deploy.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${SCRIPT_DIR}"

REGION="${AWS_REGION:-us-east-1}"
PROJECT="lore-elig-demo"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

BUCKET_RAW="${PROJECT}-raw-${ACCOUNT}"
BUCKET_BRONZE="${PROJECT}-bronze-${ACCOUNT}"
DDB_TABLE="${PROJECT}-golden-records"
ROLE_NAME="${PROJECT}-lambda-role"
BEDROCK_MODEL="us.anthropic.claude-sonnet-4-6"
EMBED_MODEL="amazon.titan-embed-text-v2:0"
LOG_RETENTION=7

LOG_IDV="/aws/lambda/${PROJECT}-idv-api"
LOG_PROC="/aws/lambda/${PROJECT}-file-processor"
LOG_SCHEMA="/aws/lambda/${PROJECT}-schema-inference"

green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
hdr()    { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }

# ============================================================================
hdr "Wave 0: build Lambda zips from source"
# ============================================================================
build_idv_api() {
  local out="${SCRIPT_DIR}/lambdas/idv_api/build"
  rm -rf "${out}" && mkdir -p "${out}"
  cp "${SCRIPT_DIR}/lambdas/idv_api/handler.py" "${out}/"
  cp -R "${REPO_ROOT}/services" "${out}/services"
  find "${out}/services" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
  python3 -m pip install --quiet \
    --platform manylinux2014_x86_64 --target "${out}" \
    --implementation cp --python-version 3.12 \
    --only-binary=:all: --upgrade \
    -r "${SCRIPT_DIR}/lambdas/idv_api/requirements.txt" >/dev/null 2>&1
  (cd "${out}" && zip -qr "${SCRIPT_DIR}/lambdas/idv_api/idv_api.zip" .)
}
build_file_processor() {
  local out="${SCRIPT_DIR}/lambdas/file_processor/build"
  rm -rf "${out}" && mkdir -p "${out}"
  cp "${SCRIPT_DIR}/lambdas/file_processor/handler.py" "${out}/"
  (cd "${out}" && zip -qr "${SCRIPT_DIR}/lambdas/file_processor/file_processor.zip" .)
}
build_schema_inference() {
  local out="${SCRIPT_DIR}/lambdas/schema_inference/build"
  rm -rf "${out}" && mkdir -p "${out}"
  cp "${SCRIPT_DIR}/lambdas/schema_inference/handler.py" "${out}/"
  cp -R "${REPO_ROOT}/services" "${out}/services"
  find "${out}/services" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
  (cd "${out}" && zip -qr "${SCRIPT_DIR}/lambdas/schema_inference/schema_inference.zip" .)
}
build_idv_api;          green "✓ idv_api zip"
build_file_processor;   green "✓ file_processor zip"
build_schema_inference; green "✓ schema_inference zip"

# Idempotent helper: run a command, ignore "already exists" errors.
ok_if_exists() {
  local out
  if ! out="$("$@" 2>&1)"; then
    if echo "$out" | grep -qiE "already exists|EntityAlreadyExists|ResourceConflictException|BucketAlreadyOwnedByYou|TableAlreadyExists"; then
      yellow "  (already exists, skipping)"
    else
      echo "$out" >&2
      return 1
    fi
  else
    echo "$out"
  fi
}

# ============================================================================
hdr "Wave A: IAM role, DDB table, S3 buckets, log groups (parallel)"
# ============================================================================

ASSUME_ROLE_DOC='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

ok_if_exists aws iam create-role \
  --role-name "${ROLE_NAME}" \
  --assume-role-policy-document "${ASSUME_ROLE_DOC}" \
  --tags Key=Project,Value=${PROJECT} Key=ManagedBy,Value=cli Key=Demo,Value=true >/dev/null
green "✓ IAM role"

ok_if_exists aws dynamodb create-table \
  --region "${REGION}" \
  --table-name "${DDB_TABLE}" \
  --billing-mode PAY_PER_REQUEST \
  --attribute-definitions \
      AttributeName=golden_record_id,AttributeType=S \
      AttributeName=lookup_key,AttributeType=S \
  --key-schema AttributeName=golden_record_id,KeyType=HASH \
  --global-secondary-indexes \
      'IndexName=lookup_key_index,KeySchema=[{AttributeName=lookup_key,KeyType=HASH}],Projection={ProjectionType=ALL}' \
  --sse-specification Enabled=true \
  --tags Key=Project,Value=${PROJECT} Key=Demo,Value=true >/dev/null
green "✓ DynamoDB table"

# us-east-1 must NOT pass --create-bucket-configuration; other regions must.
create_bucket() {
  local b="$1"
  if [ "${REGION}" = "us-east-1" ]; then
    ok_if_exists aws s3api create-bucket --bucket "${b}" --region "${REGION}" >/dev/null
  else
    ok_if_exists aws s3api create-bucket --bucket "${b}" --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi
}
create_bucket "${BUCKET_RAW}";    green "✓ S3 raw bucket"
create_bucket "${BUCKET_BRONZE}"; green "✓ S3 bronze bucket"

for lg in "${LOG_IDV}" "${LOG_PROC}" "${LOG_SCHEMA}"; do
  ok_if_exists aws logs create-log-group --region "${REGION}" --log-group-name "${lg}" >/dev/null
  aws logs put-retention-policy --region "${REGION}" --log-group-name "${lg}" --retention-in-days "${LOG_RETENTION}" >/dev/null
done
green "✓ CloudWatch log groups"

# ============================================================================
hdr "Wave B: attach IAM policies, configure S3"
# ============================================================================

aws iam attach-role-policy --role-name "${ROLE_NAME}" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

INLINE_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DynamoDBAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan",
        "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:BatchWriteItem",
        "dynamodb:DescribeTable"
      ],
      "Resource": [
        "arn:aws:dynamodb:${REGION}:${ACCOUNT}:table/${DDB_TABLE}",
        "arn:aws:dynamodb:${REGION}:${ACCOUNT}:table/${DDB_TABLE}/index/*"
      ]
    },
    {
      "Sid": "S3Access",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::${BUCKET_RAW}",
        "arn:aws:s3:::${BUCKET_RAW}/*",
        "arn:aws:s3:::${BUCKET_BRONZE}",
        "arn:aws:s3:::${BUCKET_BRONZE}/*"
      ]
    },
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:*:${ACCOUNT}:inference-profile/*",
        "arn:aws:bedrock:*:*:inference-profile/*"
      ]
    },
    {
      "Sid": "InvokeSchemaInference",
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:${REGION}:${ACCOUNT}:function:${PROJECT}-schema-inference"
    }
  ]
}
EOF
)
aws iam put-role-policy --role-name "${ROLE_NAME}" \
  --policy-name "${PROJECT}-lambda-inline" \
  --policy-document "${INLINE_POLICY}"
green "✓ IAM policies attached"

# Public access block + SSE on both buckets
for b in "${BUCKET_RAW}" "${BUCKET_BRONZE}"; do
  aws s3api put-public-access-block --bucket "${b}" \
    --public-access-block-configuration \
    BlockPublicAcls=true,BlockPublicPolicy=true,IgnorePublicAcls=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "${b}" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
done
green "✓ S3 PAB + SSE"

# Lifecycle (raw only) — 7-day expiry
aws s3api put-bucket-lifecycle-configuration --bucket "${BUCKET_RAW}" \
  --lifecycle-configuration '{
    "Rules":[{
      "ID":"expire-after-7-days",
      "Status":"Enabled",
      "Filter":{},
      "Expiration":{"Days":7}
    }]
  }'
green "✓ S3 raw lifecycle (7-day expiry)"

# ============================================================================
hdr "Wave C: wait 12s for IAM propagation, then create Lambda functions"
# ============================================================================
sleep 12

ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE_NAME}"

create_or_update_lambda() {
  local name="$1" zip="$2" timeout="$3" mem="$4" env_json="$5"
  if aws lambda get-function --function-name "${name}" --region "${REGION}" >/dev/null 2>&1; then
    yellow "  ${name} exists — updating code + config"
    aws lambda update-function-code --region "${REGION}" \
      --function-name "${name}" --zip-file "fileb://${zip}" >/dev/null
    aws lambda wait function-updated --region "${REGION}" --function-name "${name}"
    aws lambda update-function-configuration --region "${REGION}" \
      --function-name "${name}" --timeout "${timeout}" --memory-size "${mem}" \
      --environment "${env_json}" >/dev/null
  else
    aws lambda create-function --region "${REGION}" \
      --function-name "${name}" \
      --runtime python3.12 \
      --role "${ROLE_ARN}" \
      --handler handler.handler \
      --zip-file "fileb://${zip}" \
      --timeout "${timeout}" \
      --memory-size "${mem}" \
      --environment "${env_json}" \
      --tags "Project=${PROJECT},Demo=true" >/dev/null
  fi
  aws lambda wait function-active --region "${REGION}" --function-name "${name}"
  green "✓ Lambda ${name}"
}

IDV_ENV=$(printf '{"Variables":{"LORE_IDV_STORE_BACKEND":"dynamodb","LORE_IDV_DDB_TABLE":"%s","LORE_BEDROCK_MODEL":"%s","LORE_BEDROCK_EMBED_MODEL":"%s","AWS_LWA_INVOKE_MODE":"response_stream"}}' "${DDB_TABLE}" "${BEDROCK_MODEL}" "${EMBED_MODEL}")
PROC_ENV=$(printf '{"Variables":{"LORE_IDV_DDB_TABLE":"%s","BRONZE_BUCKET":"%s","SCHEMA_INFERENCE_FN":"%s-schema-inference"}}' "${DDB_TABLE}" "${BUCKET_BRONZE}" "${PROJECT}")
# Schema inference: prefer the user's local Anthropic API key if set (Bedrock daily quota
# on this account is 0/day, so direct Anthropic API is the working path for live demos).
ANTHROPIC_KEY="${ANTHROPIC_API_KEY:-}"
ANTHROPIC_MODEL_ID="${LORE_ANTHROPIC_MODEL:-claude-sonnet-4-6}"
if [ -n "${ANTHROPIC_KEY}" ]; then
  yellow "  using Anthropic API for schema_inference (model=${ANTHROPIC_MODEL_ID})"
  SCHEMA_ENV=$(BEDROCK_MODEL="${BEDROCK_MODEL}" ANTHROPIC_MODEL_ID="${ANTHROPIC_MODEL_ID}" ANTHROPIC_KEY="${ANTHROPIC_KEY}" python3 -c '
import json, os
print(json.dumps({"Variables": {
    "LORE_BEDROCK_MODEL": os.environ["BEDROCK_MODEL"],
    "LORE_SCHEMA_INFERENCE_MODE": "anthropic",
    "LORE_ANTHROPIC_MODEL": os.environ["ANTHROPIC_MODEL_ID"],
    "ANTHROPIC_API_KEY": os.environ["ANTHROPIC_KEY"],
}}))')
else
  yellow "  ANTHROPIC_API_KEY not set — schema_inference will use auto mode (will fall back to local)"
  SCHEMA_ENV=$(printf '{"Variables":{"LORE_BEDROCK_MODEL":"%s"}}' "${BEDROCK_MODEL}")
fi

create_or_update_lambda "${PROJECT}-idv-api"          "lambdas/idv_api/idv_api.zip"                   15 512 "${IDV_ENV}"
create_or_update_lambda "${PROJECT}-file-processor"   "lambdas/file_processor/file_processor.zip"     60 512 "${PROC_ENV}"
create_or_update_lambda "${PROJECT}-schema-inference" "lambdas/schema_inference/schema_inference.zip" 30 512 "${SCHEMA_ENV}"

IDV_ARN="$(aws lambda get-function --region "${REGION}" --function-name "${PROJECT}-idv-api" --query 'Configuration.FunctionArn' --output text)"
PROC_ARN="$(aws lambda get-function --region "${REGION}" --function-name "${PROJECT}-file-processor" --query 'Configuration.FunctionArn' --output text)"

# ============================================================================
hdr "Wave D: API Gateway HTTP API"
# ============================================================================

# Reuse an existing API by name if present.
EXISTING_API_ID="$(aws apigatewayv2 get-apis --region "${REGION}" \
  --query "Items[?Name=='${PROJECT}-idv-api'].ApiId | [0]" --output text)"
if [ "${EXISTING_API_ID}" != "None" ] && [ -n "${EXISTING_API_ID}" ]; then
  API_ID="${EXISTING_API_ID}"
  yellow "  API already exists — reusing ${API_ID}"
else
  API_ID="$(aws apigatewayv2 create-api --region "${REGION}" \
    --name "${PROJECT}-idv-api" \
    --protocol-type HTTP \
    --cors-configuration 'AllowMethods=GET,POST,OPTIONS,AllowHeaders=content-type,authorization,x-correlation-id,AllowOrigins=*' \
    --query ApiId --output text)"
fi
green "✓ HTTP API ${API_ID}"

# Integration (idempotent: drop existing one for this URI, recreate)
INTEG_ID="$(aws apigatewayv2 get-integrations --region "${REGION}" --api-id "${API_ID}" \
  --query 'Items[0].IntegrationId' --output text 2>/dev/null || true)"
if [ -z "${INTEG_ID}" ] || [ "${INTEG_ID}" = "None" ]; then
  INTEG_ID="$(aws apigatewayv2 create-integration --region "${REGION}" \
    --api-id "${API_ID}" \
    --integration-type AWS_PROXY \
    --integration-uri "${IDV_ARN}" \
    --payload-format-version 2.0 \
    --query IntegrationId --output text)"
fi
green "✓ Integration ${INTEG_ID}"

# $default route
DEFAULT_ROUTE_ID="$(aws apigatewayv2 get-routes --region "${REGION}" --api-id "${API_ID}" \
  --query "Items[?RouteKey=='\$default'].RouteId | [0]" --output text)"
if [ -z "${DEFAULT_ROUTE_ID}" ] || [ "${DEFAULT_ROUTE_ID}" = "None" ]; then
  aws apigatewayv2 create-route --region "${REGION}" --api-id "${API_ID}" \
    --route-key '$default' --target "integrations/${INTEG_ID}" >/dev/null
fi
green "✓ \$default route"

# $default stage with auto-deploy
EXISTING_STAGE="$(aws apigatewayv2 get-stages --region "${REGION}" --api-id "${API_ID}" \
  --query "Items[?StageName=='\$default'].StageName | [0]" --output text)"
if [ "${EXISTING_STAGE}" = "None" ] || [ -z "${EXISTING_STAGE}" ]; then
  aws apigatewayv2 create-stage --region "${REGION}" --api-id "${API_ID}" \
    --stage-name '$default' --auto-deploy >/dev/null
fi
green "✓ \$default stage"

API_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com"

# ============================================================================
hdr "Wave E: Lambda permissions + S3 → file_processor wiring"
# ============================================================================

# API Gateway → idv_api invoke permission
ok_if_exists aws lambda add-permission --region "${REGION}" \
  --function-name "${PROJECT}-idv-api" \
  --statement-id AllowAPIGwInvoke \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT}:${API_ID}/*/*" >/dev/null
green "✓ apigw → idv_api permission"

# S3 → file_processor invoke permission
ok_if_exists aws lambda add-permission --region "${REGION}" \
  --function-name "${PROJECT}-file-processor" \
  --statement-id AllowS3Invoke \
  --action lambda:InvokeFunction \
  --principal s3.amazonaws.com \
  --source-arn "arn:aws:s3:::${BUCKET_RAW}" >/dev/null
green "✓ s3 → file_processor permission"

# S3 bucket notification on raw/inbox/* → file_processor
aws s3api put-bucket-notification-configuration --bucket "${BUCKET_RAW}" \
  --notification-configuration "$(cat <<EOF
{
  "LambdaFunctionConfigurations":[{
    "LambdaFunctionArn":"${PROC_ARN}",
    "Events":["s3:ObjectCreated:*"],
    "Filter":{"Key":{"FilterRules":[{"Name":"prefix","Value":"inbox/"}]}}
  }]
}
EOF
)"
green "✓ s3 raw/inbox → file_processor notification"

# ============================================================================
hdr "Wave F: seed DynamoDB"
# ============================================================================
python3 "${SCRIPT_DIR}/seed/seed_dynamodb.py" --table "${DDB_TABLE}" --region "${REGION}"

# ============================================================================
hdr "Wave G: smoke test"
# ============================================================================
HEALTH=$(curl -sS -o /dev/null -w "%{http_code}" "${API_URL}/healthz" || echo "000")
if [ "${HEALTH}" = "200" ]; then
  green "✓ /healthz returned 200"
else
  yellow "△ /healthz returned ${HEALTH} — Lambda may still be cold-starting; retry in a few seconds"
fi

echo
printf "\033[1mLive API URL:\033[0m\n  %s\n\n" "${API_URL}"
printf "\033[1mTry it:\033[0m\n"
echo "  curl -s ${API_URL}/healthz"
echo "  curl -sX POST ${API_URL}/v1/verify -H 'content-type: application/json' \\"
echo "       -d @${REPO_ROOT}/samples/verify_nickname.json | python3 -m json.tool"
echo
green "Cost meter started. Tear it down with teardown.sh when done."
