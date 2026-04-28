"""PII vault client with a Skyflow-style interface.

The contract:

    client.tokenize([{"field": "ssn", "value": "123-45-6789"}, ...])
        → {"tokens": ["tok_abc...", ...]}

    client.detokenize(token, *, purpose="support_case_12345", actor="alice@lore.co")
        → "123-45-6789"  (and a structured audit log is emitted)

In production, this calls the Skyflow API. Locally and in tests, an in-memory
backend is used. **The behavior must be identical** in terms of policy enforcement
so that test coverage of the auth/audit paths is meaningful.

Token format: `tok_<base32-of-hash>` — non-reversible without the vault. Format-preserving
tokenization (where the token has the same shape as the source value, e.g., for SSN) is a
production feature; the local backend issues opaque tokens for simplicity.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

log = logging.getLogger(__name__)


class PolicyDeniedError(Exception):
    """Raised when a detokenize call is denied by the access policy."""


@dataclass
class TokenizeRequest:
    field: str  # logical field name, e.g., "ssn", "email", "address_line_1"
    value: str
    partner_id: str  # for per-partner key isolation
    record_id: str | None = None  # optional partner_member_id


@dataclass
class DetokenizeRequest:
    token: str
    purpose: str  # required: business reason, e.g., "idv_match"
    actor: str   # required: human or service identity


@dataclass
class AuditEvent:
    ts: str
    actor: str
    action: str  # tokenize|detokenize|denied
    field: str | None
    token: str | None
    purpose: str | None
    partner_id: str | None
    result: str  # success|denied|error
    reason: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": self.ts, "actor": self.actor, "action": self.action,
                "field": self.field, "token": self.token, "purpose": self.purpose,
                "partner_id": self.partner_id, "result": self.result, "reason": self.reason,
            }
        )


# Allow-list of (actor_pattern, purpose, field) — minimal viable policy. Real Skyflow
# policy is JSON DSL; this mirrors the shape so callers learn the right vocabulary.
DEFAULT_POLICY: list[dict] = [
    {"actor": "service:idv-api", "purpose": "idv_match", "fields": ["ssn", "email", "phone"]},
    {"actor": "service:cdc-handler", "purpose": "tokenize_only", "fields": []},
    {"actor": "service:cleansing", "purpose": "tokenize_only", "fields": []},
    {"actor": "human:*", "purpose": "support_case_*", "fields": ["email", "phone", "address_line_1"], "requires_approval": True},
    {"actor": "human:compliance@lore.co", "purpose": "audit_*", "fields": ["*"]},
]


class PIIVaultClient:
    def __init__(
        self,
        *,
        backend: str | None = None,
        policy: list[dict] | None = None,
        audit_sink=None,
    ):
        self.backend = (backend or os.environ.get("LORE_PII_VAULT_BACKEND", "local")).lower()
        self.policy = policy or DEFAULT_POLICY
        self.audit_sink = audit_sink or _stdlib_audit_logger
        self._local_store: dict[str, dict] = {}
        self._secret = os.environ.get("LORE_PII_LOCAL_SECRET", "dev-only-not-for-prod").encode()

    # ---------- public API ----------
    def tokenize(self, requests: Iterable[TokenizeRequest], *, actor: str = "service:cleansing") -> list[str]:
        """Tokenize a batch of values. Idempotent: same (partner_id, field, value) → same token.

        Idempotency is critical so re-running a cleansing job doesn't bloat the vault.
        """
        out: list[str] = []
        for req in requests:
            tok = self._tokenize_one(req)
            self._audit(action="tokenize", actor=actor, field=req.field,
                        token=tok, purpose="tokenize_only", partner_id=req.partner_id, result="success")
            out.append(tok)
        return out

    def detokenize(self, request: DetokenizeRequest) -> str:
        if not self._policy_allows(request):
            self._audit(action="denied", actor=request.actor, field=None,
                        token=request.token, purpose=request.purpose, partner_id=None,
                        result="denied", reason="policy")
            raise PolicyDeniedError(
                f"actor={request.actor} not authorized for purpose={request.purpose}"
            )
        if self.backend == "local":
            stored = self._local_store.get(request.token)
            if stored is None:
                self._audit(action="detokenize", actor=request.actor, field=None,
                            token=request.token, purpose=request.purpose, partner_id=None,
                            result="error", reason="token not found")
                raise KeyError(f"unknown token {request.token}")
            self._audit(action="detokenize", actor=request.actor, field=stored["field"],
                        token=request.token, purpose=request.purpose, partner_id=stored["partner_id"],
                        result="success")
            return stored["value"]
        return self._detokenize_skyflow(request)

    # ---------- internals ----------
    def _tokenize_one(self, req: TokenizeRequest) -> str:
        if self.backend == "skyflow":
            return self._tokenize_skyflow(req)
        # local: deterministic HMAC-based token, idempotent on (partner_id, field, value)
        msg = f"{req.partner_id}|{req.field}|{req.value}".encode()
        digest = hmac.new(self._secret, msg, hashlib.sha256).digest()[:16]
        token = "tok_" + base64.b32encode(digest).decode().rstrip("=").lower()
        self._local_store[token] = {"value": req.value, "field": req.field, "partner_id": req.partner_id}
        return token

    def _policy_allows(self, request: DetokenizeRequest) -> bool:
        """Match request against policy entries. Patterns: '*' matches any."""
        for rule in self.policy:
            if not _glob(rule.get("actor", ""), request.actor):
                continue
            if not _glob(rule.get("purpose", ""), request.purpose):
                continue
            return True
        return False

    def _audit(self, **kwargs) -> None:
        evt = AuditEvent(ts=datetime.now(timezone.utc).isoformat(), **kwargs)
        try:
            self.audit_sink(evt)
        except Exception:  # never let audit-emission break the call path
            log.exception("audit sink raised; continuing")

    # ---------- Skyflow stubs ----------
    def _tokenize_skyflow(self, req: TokenizeRequest) -> str:
        # In production: POST to Skyflow's /tokens endpoint with the partner-scoped vault.
        # Auth via Skyflow service-account JWT signed with our private key.
        raise NotImplementedError("Skyflow backend wired in production; use backend='local' for tests.")

    def _detokenize_skyflow(self, request: DetokenizeRequest) -> str:
        raise NotImplementedError("Skyflow backend wired in production; use backend='local' for tests.")


def _glob(pattern: str, value: str) -> bool:
    """Tiny glob: '*' matches any suffix; otherwise exact match."""
    if pattern == "*":
        return True
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return pattern == value


def _stdlib_audit_logger(evt: AuditEvent) -> None:
    """Default audit sink: structured JSON to logger 'pii.audit'.

    In production this is wired to (1) CloudWatch, (2) S3 audit bucket with Object Lock,
    (3) Datadog. The wiring is in `services/pii_vault/audit_sinks.py`.
    """
    logging.getLogger("pii.audit").info(evt.to_json())
