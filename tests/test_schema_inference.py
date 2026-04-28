"""Tests for the Bedrock schema-inference service.

We pin tests to the local-mock backend so CI doesn't need AWS credentials and the
suite is hermetic. Bedrock-mode behavior is exercised in a separate integration
suite (not in this repo).
"""

from __future__ import annotations

from services.schema_inference import infer_schema


def test_infer_schema_classifies_obvious_columns():
    sample = [
        {"EmployeeID": "E-1", "FirstName": "Bob", "LastName": "Smith",
         "DOB": "1962-04-12", "SSN": "123-45-6789", "Email": "bob@x.com",
         "PostalCode": "90210", "State": "CA", "EligStartDate": "2024-01-01"},
        {"EmployeeID": "E-2", "FirstName": "Maria", "LastName": "Garcia",
         "DOB": "1985-09-30", "SSN": "987-65-4321", "Email": "m@x.com",
         "PostalCode": "78701", "State": "TX", "EligStartDate": "2024-01-01"},
    ]
    result = infer_schema("acme.csv", sample, mode="local")

    by_col = {c.source_column: c for c in result.columns}
    assert by_col["SSN"].canonical_field == "ssn"
    assert by_col["SSN"].pii_tier == "TIER_1_DIRECT"
    assert by_col["DOB"].canonical_field == "dob"
    assert by_col["DOB"].pii_tier == "TIER_1_DIRECT"
    assert by_col["FirstName"].canonical_field == "first_name"
    assert by_col["LastName"].canonical_field == "last_name"
    assert by_col["Email"].canonical_field == "email"
    assert by_col["PostalCode"].canonical_field == "zip"
    assert by_col["State"].canonical_field == "state"
    assert by_col["EligStartDate"].canonical_field == "effective_start_date"
    assert by_col["EmployeeID"].canonical_field == "partner_member_id"


def test_infer_schema_falls_back_to_value_sniffing_when_header_unknown():
    # Headers don't tell us anything; values do.
    sample = [
        {"col1": "1962-04-12", "col2": "bob@example.com", "col3": "90210"},
        {"col1": "1985-09-30", "col2": "maria@example.com", "col3": "78701"},
    ]
    result = infer_schema("opaque.csv", sample, mode="local")
    by_col = {c.source_column: c for c in result.columns}
    assert by_col["col1"].canonical_field == "dob"
    assert by_col["col2"].canonical_field == "email"
    assert by_col["col3"].canonical_field == "zip"


def test_overall_quality_risk_is_set():
    sample = [{"foo": "bar"}, {"foo": "baz"}]
    result = infer_schema("mystery.csv", sample, mode="local")
    assert result.overall_quality_risk in {"LOW", "MEDIUM", "HIGH"}
    # Mostly-unmapped file should NOT be LOW
    assert result.overall_quality_risk in {"MEDIUM", "HIGH"}


def test_yaml_rendering_round_trips():
    sample = [{"FirstName": "Bob", "LastName": "Smith", "DOB": "1962-04-12"}]
    result = infer_schema("x.csv", sample, mode="local")
    yaml_text = result.to_data_contract_yaml()
    assert "FirstName" in yaml_text
    assert "first_name" in yaml_text
    assert "TIER_1_DIRECT" in yaml_text
    assert result.prompt_version in yaml_text


def test_empty_sample_raises():
    import pytest
    with pytest.raises(ValueError):
        infer_schema("x.csv", [], mode="local")
