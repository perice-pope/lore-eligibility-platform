#!/usr/bin/env bash
# Tear down the cloud demo. Empties S3 buckets and DDB, then `terraform destroy`,
# and verifies nothing is left behind so you don't get a surprise bill.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

REGION="${AWS_REGION:-us-east-1}"
PROJECT="lore-elig-demo"

red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
hdr()    { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }

if [ ! -f "terraform.tfstate" ] && [ ! -f ".terraform.tfstate" ]; then
  yellow "No terraform state found in $(pwd) — nothing to tear down (or already destroyed)."
  exit 0
fi

ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo unknown)"

# ─── Empty S3 buckets first (terraform destroy can't delete non-empty buckets) ─
hdr "Emptying S3 buckets"
for bucket in "${PROJECT}-raw-${ACCOUNT}" "${PROJECT}-bronze-${ACCOUNT}"; do
  if aws s3api head-bucket --bucket "${bucket}" 2>/dev/null; then
    yellow "  emptying s3://${bucket}"
    aws s3 rm "s3://${bucket}" --recursive --quiet || true
    # Delete versioned objects too if versioning was ever enabled
    aws s3api delete-objects --bucket "${bucket}" \
      --delete "$(aws s3api list-object-versions --bucket "${bucket}" \
        --query '{Objects: Versions[].{Key: Key, VersionId: VersionId}}' 2>/dev/null \
        || echo '{"Objects":[]}')" >/dev/null 2>&1 || true
  fi
done

# ─── Terraform destroy ─────────────────────────────────────────────────────────
hdr "Running terraform destroy"
terraform destroy -auto-approve -var "aws_region=${REGION}"

# ─── Verify clean ─────────────────────────────────────────────────────────────
hdr "Verifying clean"
LEFTOVERS=0

if aws dynamodb describe-table --region "${REGION}" --table-name "${PROJECT}-golden-records" 2>/dev/null | grep -q TableArn; then
  red "  DynamoDB table still exists — run again or check console"
  LEFTOVERS=$((LEFTOVERS+1))
fi

for bucket in "${PROJECT}-raw-${ACCOUNT}" "${PROJECT}-bronze-${ACCOUNT}"; do
  if aws s3api head-bucket --bucket "${bucket}" 2>/dev/null; then
    red "  S3 bucket still exists: ${bucket}"
    LEFTOVERS=$((LEFTOVERS+1))
  fi
done

for fn in "${PROJECT}-idv-api" "${PROJECT}-file-processor" "${PROJECT}-schema-inference"; do
  if aws lambda get-function --function-name "${fn}" --region "${REGION}" >/dev/null 2>&1; then
    red "  Lambda still exists: ${fn}"
    LEFTOVERS=$((LEFTOVERS+1))
  fi
done

if [ "${LEFTOVERS}" -eq 0 ]; then
  green "✓ All demo resources destroyed. No further AWS charges from this demo."
else
  red "△ ${LEFTOVERS} resource(s) appear to remain. Investigate in the console."
  exit 1
fi
