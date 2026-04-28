#!/usr/bin/env bash
# Build the Lambda zips, terraform apply, seed DynamoDB, print live URL.
# Idempotent — safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${SCRIPT_DIR}"

REGION="${AWS_REGION:-us-east-1}"
PROJECT="lore-elig-demo"

# ─── Pretty printing ──────────────────────────────────────────────────────────
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
bold()   { printf "\033[1m%s\033[0m\n" "$*"; }
hdr()    { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }

# ─── Pre-flight ───────────────────────────────────────────────────────────────
hdr "Pre-flight checks"

command -v aws       >/dev/null || { red "aws CLI not found. brew install awscli"; exit 1; }
command -v terraform >/dev/null || { red "terraform not found. brew install terraform"; exit 1; }
command -v python3   >/dev/null || { red "python3 not found"; exit 1; }
command -v zip       >/dev/null || { red "zip not found (should be on macOS by default)"; exit 1; }

# Verify CLI is authed and using the expected region.
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  red "aws sts get-caller-identity failed. Run: aws configure"
  exit 1
fi
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
green "✓ aws cli authenticated → account ${ACCOUNT}, region ${REGION}"

# ─── Build Lambda packages ────────────────────────────────────────────────────
hdr "Building Lambda packages"

build_idv_api() {
  local out="${SCRIPT_DIR}/lambdas/idv_api/build"
  rm -rf "${out}" && mkdir -p "${out}"
  cp "${SCRIPT_DIR}/lambdas/idv_api/handler.py" "${out}/"

  # Copy services/ into the package so imports resolve at runtime.
  cp -R "${REPO_ROOT}/services" "${out}/services"
  # Drop test-only files
  find "${out}/services" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

  # Install runtime deps targeting Lambda's manylinux env.
  python3 -m pip install \
    --quiet \
    --platform manylinux2014_x86_64 \
    --target "${out}" \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    --upgrade \
    -r "${SCRIPT_DIR}/lambdas/idv_api/requirements.txt"

  green "✓ idv_api package built"
}

build_file_processor() {
  local out="${SCRIPT_DIR}/lambdas/file_processor/build"
  rm -rf "${out}" && mkdir -p "${out}"
  cp "${SCRIPT_DIR}/lambdas/file_processor/handler.py" "${out}/"
  green "✓ file_processor package built (no extra deps)"
}

build_schema_inference() {
  local out="${SCRIPT_DIR}/lambdas/schema_inference/build"
  rm -rf "${out}" && mkdir -p "${out}"
  cp "${SCRIPT_DIR}/lambdas/schema_inference/handler.py" "${out}/"
  cp -R "${REPO_ROOT}/services" "${out}/services"
  find "${out}/services" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
  green "✓ schema_inference package built"
}

build_idv_api
build_file_processor
build_schema_inference

# ─── Terraform ────────────────────────────────────────────────────────────────
hdr "Running terraform apply"

if [ ! -d ".terraform" ]; then
  terraform init -upgrade
fi

terraform apply -auto-approve -var "aws_region=${REGION}"

# ─── Seed DynamoDB ────────────────────────────────────────────────────────────
hdr "Seeding DynamoDB with golden records"

DDB_TABLE="$(terraform output -raw ddb_table)"
python3 "${SCRIPT_DIR}/seed/seed_dynamodb.py" --table "${DDB_TABLE}" --region "${REGION}"

# ─── Verify ───────────────────────────────────────────────────────────────────
hdr "Verifying deployment"

API_URL="$(terraform output -raw idv_api_url)"
HEALTH=$(curl -sS -o /dev/null -w "%{http_code}" "${API_URL}/healthz" || echo "000")
if [ "${HEALTH}" = "200" ]; then
  green "✓ IDV API returned 200 on /healthz"
else
  yellow "△ /healthz returned ${HEALTH} — Lambda may still be cold-starting; retry in a few seconds"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
hdr "🎉 Deploy complete"
echo
bold "Live API URL:"
echo "  ${API_URL}"
echo
bold "Try it now:"
echo "  curl -s ${API_URL}/healthz"
echo "  curl -sX POST ${API_URL}/v1/verify -H 'content-type: application/json' \\"
echo "       -d @${REPO_ROOT}/samples/verify_nickname.json | python3 -m json.tool"
echo
bold "Console links:"
terraform output -json demo_console_links | python3 -c "
import json, sys
for k, v in json.load(sys.stdin).items():
    print(f'  {k:20} {v}')
"
echo
bold "When you're done with the panel:"
echo "  ${SCRIPT_DIR}/teardown.sh"
echo
green "Cost meter started. Tear it down when done."
