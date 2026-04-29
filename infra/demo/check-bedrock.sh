#!/usr/bin/env bash
# Poll Bedrock until Claude + Titan both invoke successfully.
# Run in a second terminal while you submit the Anthropic use-case form.

set -u
REGION="${AWS_REGION:-us-east-1}"

green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*"; }

claude_ok=0; titan_ok=0
i=0
while :; do
  i=$((i+1))
  ts="$(date '+%H:%M:%S')"

  if [ "$claude_ok" = "0" ]; then
    out=$(aws bedrock-runtime invoke-model --region "$REGION" \
      --model-id us.anthropic.claude-sonnet-4-6 \
      --body "$(printf '{"anthropic_version":"bedrock-2023-05-31","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}' | base64)" \
      /tmp/c.json 2>&1)
    if [ -f /tmp/c.json ]; then
      green "[$ts] claude OK"
      claude_ok=1
      rm -f /tmp/c.json
    else
      msg="$(echo "$out" | head -1)"
      yellow "[$ts] claude: ${msg:0:120}"
    fi
  fi

  if [ "$titan_ok" = "0" ]; then
    out=$(aws bedrock-runtime invoke-model --region "$REGION" \
      --model-id amazon.titan-embed-text-v2:0 \
      --body "$(printf '{"inputText":"hi"}' | base64)" \
      /tmp/t.json 2>&1)
    if [ -f /tmp/t.json ]; then
      green "[$ts] titan OK"
      titan_ok=1
      rm -f /tmp/t.json
    else
      msg="$(echo "$out" | head -1)"
      yellow "[$ts] titan:  ${msg:0:120}"
    fi
  fi

  if [ "$claude_ok" = "1" ] && [ "$titan_ok" = "1" ]; then
    green "✓ Both models working — demo is safe."
    exit 0
  fi

  sleep 15
done
