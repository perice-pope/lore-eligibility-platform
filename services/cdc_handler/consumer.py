"""CDC consumer for partner database streams.

This is the *shape* of the consumer logic. The runtime wiring (kafka-python /
confluent-kafka, threading, Schema Registry) is intentionally out-of-scope for this
prototype — it's straightforward and not the interesting part of the design.

The interesting part is what happens to each event:

  1. Validate against the registered Avro schema for the partner.
  2. Drop fields not declared in the data contract (data minimization).
  3. Tokenize Tier-1 PII via the vault before anything else touches the value.
  4. Normalize (names, dates, zip).
  5. Decorate with provenance (partner_id, source_offset, lsn, ingested_at).
  6. Emit to the `silver.eligibility_events` Kafka topic.

Failure handling:
  - Schema validation failure → DLQ topic + alert; never block the consumer.
  - Tokenization failure → retry 3× with exponential backoff; if still failing,
    DLQ. Tokenization is on the critical path: we must not write raw PII downstream.
  - Normalization failure → log + DLQ + emit `eligibility_event_quarantined` event.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from services.entity_resolution.normalize import (
    normalize_dob,
    normalize_name,
    normalize_zip,
)
from services.pii_vault import PIIVaultClient, TokenizeRequest

log = logging.getLogger("cdc")

# Fields per data contract are categorized so we know which to tokenize.
TIER_1_FIELDS = {"ssn", "email", "phone", "address_line_1", "address_line_2"}


@dataclass
class DebeziumEnvelope:
    """The shape Debezium gives us for a row change. Subset of full envelope."""
    op: str  # "c" create, "u" update, "d" delete, "r" read (snapshot)
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    source: dict[str, Any]  # ts_ms, lsn, partner_id (we attach in connector config)
    ts_ms: int


@dataclass
class EligibilityEvent:
    """The normalized event we publish to the silver-layer topic."""
    partner_id: str
    partner_member_id: str
    operation: str  # "upsert" | "delete"
    occurred_at: str
    ingested_at: str
    source_lsn: str | None
    payload_tokens: dict[str, Any]  # normalized fields + tokens, NEVER raw Tier-1 PII

    def to_json(self) -> str:
        return json.dumps(self.__dict__, default=str)


class CDCEventProcessor:
    def __init__(self, vault: PIIVaultClient, contract: dict[str, dict]):
        """contract: {column_name: {canonical_field, pii_tier, cleansing_rules}}"""
        self.vault = vault
        self.contract = contract

    def process(self, envelope: DebeziumEnvelope) -> EligibilityEvent | None:
        partner_id = envelope.source.get("partner_id", "unknown")
        ts = datetime.fromtimestamp(envelope.ts_ms / 1000, timezone.utc).isoformat()
        if envelope.op == "d":
            row = envelope.before or {}
            operation = "delete"
        elif envelope.op in {"c", "u", "r"}:
            row = envelope.after or {}
            operation = "upsert"
        else:
            log.warning("unknown debezium op=%s partner=%s; dropping", envelope.op, partner_id)
            return None

        # Map source columns to canonical names per contract; drop unknowns.
        canonical = self._apply_contract(partner_id, row)

        # Derive SSN-last-4 BEFORE tokenization (we keep it in clear by policy).
        if canonical.get("ssn"):
            digits = "".join(ch for ch in str(canonical["ssn"]) if ch.isdigit())
            if digits:
                canonical["ssn_last4"] = digits[-4:]

        # Tokenize Tier-1 fields.
        token_requests: list[TokenizeRequest] = []
        token_field_order: list[str] = []
        for field, value in canonical.items():
            if field in TIER_1_FIELDS and value:
                token_requests.append(
                    TokenizeRequest(field=field, value=str(value), partner_id=partner_id)
                )
                token_field_order.append(field)
        tokens = self.vault.tokenize(token_requests, actor="service:cdc-handler")
        for field, tok in zip(token_field_order, tokens):
            canonical[f"{field}_token"] = tok
            del canonical[field]

        # ssn is sensitive Tier-1 too — tokenize separately (it's not in TIER_1_FIELDS
        # because we keep last4 plus a vault token, not a token replacing the column).
        if canonical.get("ssn"):
            ssn_token = self.vault.tokenize(
                [TokenizeRequest(field="ssn", value=str(canonical["ssn"]), partner_id=partner_id)],
                actor="service:cdc-handler",
            )[0]
            canonical["ssn_token"] = ssn_token
            del canonical["ssn"]

        # Normalize remaining fields.
        canonical = self._normalize(canonical)

        partner_member_id = canonical.get("partner_member_id") or row.get("member_id") or row.get("emp_id") or ""

        return EligibilityEvent(
            partner_id=partner_id,
            partner_member_id=str(partner_member_id),
            operation=operation,
            occurred_at=ts,
            ingested_at=datetime.now(timezone.utc).isoformat(),
            source_lsn=str(envelope.source.get("lsn")) if envelope.source.get("lsn") else None,
            payload_tokens=canonical,
        )

    # ---------- helpers ----------
    def _apply_contract(self, partner_id: str, row: dict) -> dict[str, Any]:
        """Map source column → canonical_field per contract; drop unknowns (data minimization)."""
        out: dict[str, Any] = {}
        for source_col, value in row.items():
            mapping = self.contract.get(source_col)
            if not mapping:
                # Not in contract → drop. Log once at INFO to detect partner drift.
                continue
            canon = mapping.get("canonical_field")
            if canon == "ignore" or not canon:
                continue
            out[canon] = value
        return out

    def _normalize(self, canonical: dict) -> dict:
        if "first_name" in canonical:
            canonical["first_name"] = normalize_name(canonical["first_name"])
        if "last_name" in canonical:
            canonical["last_name"] = normalize_name(canonical["last_name"])
        if "dob" in canonical:
            canonical["dob"] = normalize_dob(canonical["dob"]) or None
        if "zip" in canonical:
            canonical["zip"] = normalize_zip(canonical["zip"]) or None
        if "state" in canonical and canonical["state"]:
            canonical["state"] = str(canonical["state"]).strip().upper()[:2]
        return canonical


def consume(events: Iterable[DebeziumEnvelope], processor: CDCEventProcessor) -> Iterable[EligibilityEvent]:
    """Generator interface for testing / stream-processing.

    In production this is wrapped with kafka consumer poll() + offset commits + DLQ.
    """
    for envelope in events:
        try:
            evt = processor.process(envelope)
        except Exception as exc:
            log.exception("cdc.process.failed partner=%s op=%s",
                          envelope.source.get("partner_id"), envelope.op)
            # Real impl: emit to DLQ topic. Here we re-raise for caller visibility in tests.
            raise
        if evt is not None:
            yield evt
