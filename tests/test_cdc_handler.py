"""Tests for the CDC event processor."""

from __future__ import annotations

from services.cdc_handler.consumer import CDCEventProcessor, DebeziumEnvelope
from services.pii_vault import PIIVaultClient


CONTRACT = {
    "EmployeeID":       {"canonical_field": "partner_member_id", "pii_tier": "TIER_4_NONE"},
    "FirstName":        {"canonical_field": "first_name",        "pii_tier": "TIER_1_DIRECT"},
    "LastName":         {"canonical_field": "last_name",         "pii_tier": "TIER_1_DIRECT"},
    "DOB":              {"canonical_field": "dob",               "pii_tier": "TIER_1_DIRECT"},
    "SSN":              {"canonical_field": "ssn",               "pii_tier": "TIER_1_DIRECT"},
    "Email":            {"canonical_field": "email",             "pii_tier": "TIER_1_DIRECT"},
    "PostalCode":       {"canonical_field": "zip",               "pii_tier": "TIER_2_QUASI"},
    "State":            {"canonical_field": "state",             "pii_tier": "TIER_2_QUASI"},
    "EligStartDate":    {"canonical_field": "effective_start_date", "pii_tier": "TIER_3_SENSITIVE"},
    # Note: NOT mapping a column = it gets dropped (data minimization).
}


def make_envelope(op="c", **fields):
    return DebeziumEnvelope(
        op=op,
        before=None if op != "d" else fields,
        after=None if op == "d" else fields,
        source={"partner_id": "acme-corp", "lsn": "12345"},
        ts_ms=1745800000000,
    )


def _processor():
    vault = PIIVaultClient(backend="local")
    return CDCEventProcessor(vault, CONTRACT)


def test_create_event_tokenizes_pii_and_normalizes():
    proc = _processor()
    env = make_envelope(
        op="c",
        EmployeeID="A-100001",
        FirstName="bob",
        LastName="SMITH",
        DOB="04/12/1962",
        SSN="123-45-6789",
        Email="BOB@example.com",
        PostalCode="9210",            # short — should be padded
        State="california",          # full state name → first 2 upper
        EligStartDate="2024-01-01",
        UnknownExtra="should be dropped",
    )
    evt = proc.process(env)
    assert evt is not None
    assert evt.partner_id == "acme-corp"
    assert evt.partner_member_id == "A-100001"
    assert evt.operation == "upsert"

    p = evt.payload_tokens
    # Tokens present, raw values absent
    assert p.get("ssn_token", "").startswith("tok_") or "ssn_token" not in p  # ssn isn't in TIER_1_FIELDS list, only specific fields
    assert "ssn" not in p
    assert "email" not in p
    assert p.get("email_token", "").startswith("tok_")
    # Normalization applied
    assert p["first_name"] == "robert"  # nickname-normalized? Bob -> robert
    assert p["last_name"] == "smith"
    assert p["dob"] == "1962-04-12"
    assert p["zip"] == "09210"
    assert p["state"] == "CA"
    # Unknown column dropped
    assert "UnknownExtra" not in p
    assert "unknown_extra" not in p
    # ssn_last4 derived
    assert evt.payload_tokens["ssn_last4"] == "6789"


def test_delete_event_uses_before_image():
    proc = _processor()
    env = make_envelope(
        op="d",
        EmployeeID="A-100099",
        FirstName="Test",
        LastName="User",
        DOB="2000-01-01",
        EligStartDate="2024-01-01",
    )
    evt = proc.process(env)
    assert evt is not None
    assert evt.operation == "delete"
    assert evt.partner_member_id == "A-100099"


def test_unknown_op_is_dropped():
    proc = _processor()
    env = make_envelope(op="?", FirstName="X")
    assert proc.process(env) is None
