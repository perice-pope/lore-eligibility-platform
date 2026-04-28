"""Tests for the PII vault client."""

from __future__ import annotations

import pytest

from services.pii_vault import (
    DetokenizeRequest,
    PIIVaultClient,
    PolicyDeniedError,
    TokenizeRequest,
)


def test_tokenize_is_idempotent():
    client = PIIVaultClient(backend="local")
    req = TokenizeRequest(field="ssn", value="123-45-6789", partner_id="acme-corp")
    a = client.tokenize([req])
    b = client.tokenize([req])
    assert a == b
    assert a[0].startswith("tok_")


def test_tokenize_partner_isolated():
    client = PIIVaultClient(backend="local")
    a = client.tokenize([TokenizeRequest(field="ssn", value="123-45-6789", partner_id="acme")])
    b = client.tokenize([TokenizeRequest(field="ssn", value="123-45-6789", partner_id="blue")])
    # Same value, different partners → different tokens (partner-scoped key isolation).
    assert a[0] != b[0]


def test_detokenize_round_trips_with_authorized_actor():
    client = PIIVaultClient(backend="local")
    tokens = client.tokenize([
        TokenizeRequest(field="ssn", value="123-45-6789", partner_id="acme")
    ])
    plain = client.detokenize(DetokenizeRequest(
        token=tokens[0], purpose="idv_match", actor="service:idv-api"
    ))
    assert plain == "123-45-6789"


def test_detokenize_denies_unauthorized_actor():
    client = PIIVaultClient(backend="local")
    tokens = client.tokenize([
        TokenizeRequest(field="ssn", value="123-45-6789", partner_id="acme")
    ])
    with pytest.raises(PolicyDeniedError):
        client.detokenize(DetokenizeRequest(
            token=tokens[0], purpose="random_browse", actor="human:eve@external.com"
        ))


def test_detokenize_for_human_support_with_correct_purpose_succeeds():
    client = PIIVaultClient(backend="local")
    tokens = client.tokenize([
        TokenizeRequest(field="email", value="user@example.com", partner_id="acme")
    ])
    plain = client.detokenize(DetokenizeRequest(
        token=tokens[0], purpose="support_case_12345", actor="human:alice@lore.co"
    ))
    assert plain == "user@example.com"


def test_audit_events_emitted_on_both_paths():
    captured = []
    client = PIIVaultClient(backend="local", audit_sink=captured.append)
    tokens = client.tokenize([
        TokenizeRequest(field="ssn", value="111-22-3333", partner_id="acme")
    ])
    # success path
    client.detokenize(DetokenizeRequest(
        token=tokens[0], purpose="idv_match", actor="service:idv-api"
    ))
    # denied path
    with pytest.raises(PolicyDeniedError):
        client.detokenize(DetokenizeRequest(
            token=tokens[0], purpose="curious", actor="human:rando"
        ))

    actions = [e.action for e in captured]
    assert "tokenize" in actions
    assert "detokenize" in actions
    assert "denied" in actions
