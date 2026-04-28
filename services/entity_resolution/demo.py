"""Runnable demo: feed a few synthetic incoming records against a small index
and print decisions. Designed for the panel — works with no AWS access (uses local
embedding mock and local heuristic adjudicator)."""

from __future__ import annotations

import json

from .matcher import CandidateRecord, EntityResolver, ResolverConfig


def main() -> None:
    # Existing golden records (the index)
    index = [
        CandidateRecord(
            golden_record_id="G-0001",
            record={
                "first_name": "Robert", "last_name": "Smith",
                "dob": "1962-04-12", "zip": "90210",
                "address_line_1": "401 N Maple Dr", "city": "Beverly Hills", "state": "CA",
                "ssn_last4": "1234", "ssn_token": "tok_abc",
            },
        ),
        CandidateRecord(
            golden_record_id="G-0002",
            record={
                "first_name": "Maria", "last_name": "Garcia-Lopez",
                "dob": "1985-09-30", "zip": "78701",
                "address_line_1": "910 Congress Ave", "city": "Austin", "state": "TX",
                "ssn_last4": "5678",
            },
        ),
        CandidateRecord(
            golden_record_id="G-0003",
            record={
                "first_name": "Lin", "last_name": "Chen",
                "dob": "1990-01-15", "zip": "10001",
                "address_line_1": "350 W 31st St", "city": "New York", "state": "NY",
            },
        ),
    ]

    # Incoming records — variations of the above plus a brand-new person.
    incoming = [
        {
            "name": "Bob Smith (nickname, same person as G-0001)",
            "record": {
                "first_name": "Bob", "last_name": "Smith",
                "dob": "1962-04-12", "zip": "90210",
                "address_line_1": "401 North Maple Drive", "city": "Beverly Hills", "state": "CA",
                "ssn_last4": "1234",
            },
        },
        {
            "name": "Maria Garcia (married/dropped surname, same person as G-0002)",
            "record": {
                "first_name": "Maria", "last_name": "Garcia",
                "dob": "1985-09-30", "zip": "78704",
                "address_line_1": "200 Lavaca St", "city": "Austin", "state": "TX",
            },
        },
        {
            "name": "Lin Chen exact match (G-0003)",
            "record": {
                "first_name": "Lin", "last_name": "Chen",
                "dob": "1990-01-15", "zip": "10001",
                "address_line_1": "350 W 31st St", "city": "New York", "state": "NY",
            },
        },
        {
            "name": "New member — should NOT match",
            "record": {
                "first_name": "Dolores", "last_name": "Abernathy",
                "dob": "1970-07-04", "zip": "85001",
                "address_line_1": "1 Sweetwater Way", "city": "Phoenix", "state": "AZ",
            },
        },
    ]

    resolver = EntityResolver(
        ResolverConfig(llm_mode="local_heuristic")  # no AWS needed for demo
    )

    print("=== Entity Resolution Demo ===\n")
    for case in incoming:
        decision = resolver.resolve(case["record"], index)
        print(f"INPUT: {case['name']}")
        print(f"  decision : {decision.decision.value}")
        print(f"  matched  : {decision.golden_record_id}")
        print(f"  score    : {decision.score:.3f}")
        print(f"  stage    : {decision.stage}")
        print(f"  reasoning: {decision.reasoning}")
        print()


if __name__ == "__main__":
    main()
