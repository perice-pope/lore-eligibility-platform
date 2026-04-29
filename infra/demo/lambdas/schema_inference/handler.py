"""Schema inference Lambda — invokes real Bedrock Claude during the demo.

This Lambda is invoked manually during the panel ("here's what real Claude
returns when we hand it an unknown partner file"). It re-uses the existing
schema-inference service code shipped from `services/schema_inference/`.

Invocation:
    aws lambda invoke \\
      --function-name lore-elig-demo-schema-inference \\
      --cli-binary-format raw-in-base64-out \\
      --payload '{"filename": "partner_acme_employer.csv", "sample": [...]}' \\
      response.json && cat response.json | jq -r .body | jq

If Bedrock throttles or model access is gated, the handler falls back to the
deterministic local heuristic — same output schema. The panel still gets a
valid response; we just lose the "real Claude reasoning" bit.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from services.schema_inference import infer_schema

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event: dict, _context) -> dict:
    body = _coerce_body(event)
    filename = body.get("filename") or "partner-file.csv"
    sample = body.get("sample") or []
    # If the caller doesn't specify a mode, fall through (None) so infer_schema
    # uses LORE_SCHEMA_INFERENCE_MODE env var instead.
    requested_mode = body.get("mode")  # None | auto | bedrock | anthropic | local

    if not sample:
        return _resp(400, {"error": "missing 'sample' (list of dicts representing rows)"})

    log.info("inferring schema filename=%s rows=%d mode=%s",
             filename, len(sample), requested_mode)

    try:
        result = infer_schema(filename, sample, mode=requested_mode)
    except Exception as exc:
        log.exception("schema inference failed")
        return _resp(500, {"error": str(exc), "type": exc.__class__.__name__})

    return _resp(200, {
        "mode": result.mode,
        "model_id": result.model_id,
        "prompt_version": result.prompt_version,
        "detected_format": result.detected_format,
        "overall_quality_risk": result.overall_quality_risk,
        "overall_quality_notes": result.overall_quality_notes,
        "suggested_partition_column": result.suggested_partition_column,
        "columns": [
            {
                "source_column": c.source_column,
                "canonical_field": c.canonical_field,
                "confidence": c.confidence,
                "pii_tier": c.pii_tier,
                "cleansing_rules": c.cleansing_rules,
                "reasoning": c.reasoning,
            }
            for c in result.columns
        ],
        "draft_contract_yaml": result.to_data_contract_yaml(),
    })


def _coerce_body(event: dict) -> dict:
    """Accept both raw lambda invoke payloads and API Gateway-wrapped events."""
    if "body" in event and isinstance(event["body"], str):
        try:
            return json.loads(event["body"])
        except json.JSONDecodeError:
            return {}
    return event if isinstance(event, dict) else {}


def _resp(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=str),
    }
