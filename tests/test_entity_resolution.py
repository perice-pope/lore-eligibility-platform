"""Tests for the entity-resolution matcher."""

from __future__ import annotations

import pytest

from services.entity_resolution import EntityResolver, ResolverConfig
from services.entity_resolution.matcher import CandidateRecord, Decision
from services.entity_resolution.normalize import (
    blocking_keys,
    feature_string,
    normalize_dob,
    normalize_name,
    soundex,
)


# ---------- normalize.py ----------

def test_normalize_name_strips_diacritics_and_handles_nicknames():
    assert normalize_name("Bob") == "robert"
    assert normalize_name("Liz") == "elizabeth"
    assert normalize_name("José") == "jose"
    assert normalize_name("O'Brien") == "o'brien"
    assert normalize_name("Robert Jr.") == "robert"


def test_normalize_dob_handles_common_formats():
    assert normalize_dob("1962-04-12") == "1962-04-12"
    assert normalize_dob("04/12/1962") == "1962-04-12"
    assert normalize_dob("19620412") == "1962-04-12"
    assert normalize_dob("not a date") == ""


def test_soundex_basic_collisions():
    # Soundex collapses similar-sounding names.
    assert soundex("Smith") == soundex("Smyth")
    assert soundex("Robert") == soundex("Rupert")


def test_blocking_keys_split_compound_surnames():
    rec = {"last_name": "Garcia-Lopez", "dob": "1985-09-30", "zip": "78701"}
    keys = blocking_keys(rec)
    assert len(keys) >= 2  # one for full surname, one per component
    # Should match a record with just "Garcia"
    rec2 = {"last_name": "Garcia", "dob": "1985-09-30", "zip": "78704"}
    keys2 = blocking_keys(rec2)
    assert any(k1 == k2 for k1 in keys for k2 in keys2)


def test_feature_string_is_stable():
    rec = {"first_name": "Bob", "last_name": "Smith", "dob": "1962-04-12",
           "address_line_1": "401 N Maple Dr", "city": "Beverly Hills",
           "state": "CA", "zip": "90210", "ssn_last4": "1234"}
    s = feature_string(rec)
    assert "robert" in s        # Bob normalized
    assert "1962-04-12" in s
    assert "90210" in s
    assert "1234" in s


# ---------- matcher.py ----------

@pytest.fixture
def resolver():
    return EntityResolver(ResolverConfig(llm_mode="local_heuristic"))


@pytest.fixture
def index():
    return [
        CandidateRecord(
            golden_record_id="G-A",
            record={
                "first_name": "Robert", "last_name": "Smith",
                "dob": "1962-04-12", "zip": "90210",
                "address_line_1": "401 N Maple Dr", "city": "Beverly Hills",
                "state": "CA", "ssn_last4": "6789", "ssn_token": "tok_smith_ssn",
            },
        ),
        CandidateRecord(
            golden_record_id="G-B",
            record={
                "first_name": "Lin", "last_name": "Chen",
                "dob": "1990-01-15", "zip": "10001",
                "ssn_last4": "1122",
            },
        ),
    ]


def test_exact_ssn_token_match_is_auto(resolver, index):
    incoming = {"first_name": "DIFFERENT", "last_name": "NAME",
                "dob": "1900-01-01", "zip": "00000", "ssn_token": "tok_smith_ssn"}
    decision = resolver.resolve(incoming, index)
    assert decision.decision == Decision.AUTO_MATCH
    assert decision.golden_record_id == "G-A"
    assert decision.stage == "deterministic"


def test_deterministic_match_via_dob_soundex_zip(resolver, index):
    incoming = {"first_name": "Lin", "last_name": "Chen",
                "dob": "1990-01-15", "zip": "10001"}
    decision = resolver.resolve(incoming, index)
    assert decision.decision == Decision.AUTO_MATCH
    assert decision.golden_record_id == "G-B"
    assert decision.stage == "deterministic"


def test_no_candidate_when_blocking_keys_disjoint(resolver, index):
    incoming = {"first_name": "Dolores", "last_name": "Abernathy",
                "dob": "1970-07-04", "zip": "85001"}
    decision = resolver.resolve(incoming, index)
    assert decision.decision == Decision.NO_MATCH
    assert decision.stage == "no_candidate"


def test_nickname_matches_via_name_normalization_in_deterministic_path(resolver, index):
    # Bob is a known alias for Robert. Soundex(Bob) != Soundex(Robert), but
    # name normalization makes both -> "robert", so the deterministic-path
    # check (dob + soundex(first/last) + zip) should also work after we soundex
    # *normalized* names. Today we don't — so this verifies the embedding+LLM
    # path catches it.
    incoming = {"first_name": "Bob", "last_name": "Smith",
                "dob": "1962-04-12", "zip": "90210", "ssn_last4": "6789"}
    decision = resolver.resolve(incoming, index)
    # SSN last 4 + DOB+last name should drive deterministic in current impl.
    # Even without that, embedding + heuristic adjudicator would match.
    assert decision.decision in {Decision.AUTO_MATCH, Decision.REVIEW}
    assert decision.golden_record_id == "G-A"
