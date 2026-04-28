"""Tests for the Identity Verification FastAPI service.

Uses FastAPI's TestClient with the in-memory golden-record store seeded from samples/.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.identity_verification_api.main import app


@pytest.fixture(scope="module")
def client():
    seed = Path(__file__).resolve().parents[1] / "samples" / "golden_records_seed.json"
    os.environ["LORE_IDV_SEED_FILE"] = str(seed)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_readyz_reports_dependencies(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["dependencies"]["golden_record_store"]["records"] >= 5


def test_verify_exact_match_returns_verified(client):
    r = client.post("/v1/verify", json={
        "first_name": "Robert", "last_name": "Smith",
        "dob": "1962-04-12", "zip": "90210", "ssn_last4": "6789",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "VERIFIED"
    assert body["golden_record_id"] == "G-0001"
    assert body["score"] == 1.0


def test_verify_unknown_member_returns_not_found(client):
    r = client.post("/v1/verify", json={
        "first_name": "Nobody", "last_name": "Whoever",
        "dob": "2000-01-01", "zip": "00000",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "NOT_FOUND"


def test_verify_ineligible_when_coverage_ended(client):
    # Ethan O'Brien (G-0005) ended 2024-06-30
    r = client.post("/v1/verify", json={
        "first_name": "Ethan", "last_name": "O'Brien",
        "dob": "1955-07-18", "zip": "29401", "ssn_last4": "3344",
    })
    body = r.json()
    assert body["status"] == "INELIGIBLE"
    assert body["golden_record_id"] == "G-0005"


def test_verify_includes_correlation_id_header(client):
    r = client.post(
        "/v1/verify",
        json={"first_name": "Lin", "last_name": "Chen", "dob": "1990-01-15", "zip": "10001"},
        headers={"x-correlation-id": "test-cid-42"},
    )
    assert r.headers["x-correlation-id"] == "test-cid-42"
    assert r.json()["correlation_id"] == "test-cid-42"


def test_metrics_endpoint_reports_request_counts(client):
    client.post("/v1/verify", json={
        "first_name": "Robert", "last_name": "Smith",
        "dob": "1962-04-12", "zip": "90210", "ssn_last4": "6789",
    })
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "idv_requests_total" in body
    assert "idv_requests_verified" in body
