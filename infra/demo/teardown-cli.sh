#!/usr/bin/env bash
# Pure-AWS-CLI teardown of the cloud demo. No Terraform.
# Idempotent — safe to re-run.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

REGION="${AWS_REGION:-us-east-1}"
PROJECT="lore-elig-demo"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

BUCKET_RAW="${PROJECT}-raw-${ACCOUNT}"
BUCKET_BRONZE="${PROJECT}-bronze-${ACCOUNT}"
DDB_TABLE="${PROJECT}-golden-records"
ROLE_NAME="${PROJECT}-lambda-role"

green() { printf "\033[32m%s\033[0m\n" "$*"; }
hdr()   { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }

quiet() { "$@" >/dev/null 2>&1 || true; }

hdr "Lambdas + API Gateway"
quiet aws lambda delete-function --region "${REGION}" --function-name "${PROJECT}-idv-api"
quiet aws lambda delete-function --region "${REGION}" --function-name "${PROJECT}-file-processor"
quiet aws lambda delete-function --region "${REGION}" --function-name "${PROJECT}-schema-inference"
green "✓ Lambdas"

API_ID="$(aws apigatewayv2 get-apis --region "${REGION}" \
  --query "Items[?Name=='${PROJECT}-idv-api'].ApiId | [0]" --output text 2>/dev/null)"
if [ -n "${API_ID}" ] && [ "${API_ID}" != "None" ]; then
  quiet aws apigatewayv2 delete-api --region "${REGION}" --api-id "${API_ID}"
fi
green "✓ API Gateway"

hdr "DynamoDB"
quiet aws dynamodb delete-table --region "${REGION}" --table-name "${DDB_TABLE}"
green "✓ DynamoDB"

hdr "S3 buckets (force-empty)"
for b in "${BUCKET_RAW}" "${BUCKET_BRONZE}"; do
  quiet aws s3 rm "s3://${b}" --recursive
  quiet aws s3api delete-bucket --bucket "${b}" --region "${REGION}"
done
green "✓ S3"

hdr "CloudWatch log groups"
for lg in "/aws/lambda/${PROJECT}-idv-api" "/aws/lambda/${PROJECT}-file-processor" "/aws/lambda/${PROJECT}-schema-inference"; do
  quiet aws logs delete-log-group --region "${REGION}" --log-group-name "${lg}"
done
green "✓ Log groups"

hdr "IAM role"
quiet aws iam delete-role-policy --role-name "${ROLE_NAME}" --policy-name "${PROJECT}-lambda-inline"
quiet aws iam detach-role-policy --role-name "${ROLE_NAME}" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
quiet aws iam delete-role --role-name "${ROLE_NAME}"
green "✓ IAM role"

echo
green "All demo resources destroyed."
