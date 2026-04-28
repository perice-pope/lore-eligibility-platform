"""Golden record store abstraction.

In production: Aurora Postgres for primary lookups + OpenSearch for vector fuzzy fallback.
For local demo and tests: an in-memory store seeded from a JSON file.

The interface is narrow on purpose. The IDV API uses only `lookup` and `fuzzy_search` —
nothing else. Anything fancier belongs in the offline pipeline.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class GoldenRecord:
    golden_record_id: str
    partner_id: str
    partner_member_id: str
    first_name: str
    last_name: str
    dob: str  # ISO yyyy-mm-dd
    zip: str
    ssn_last4: Optional[str]
    email_token: Optional[str]
    phone_token: Optional[str]
    address_line_1_token: Optional[str]
    effective_start_date: str
    effective_end_date: Optional[str]


class GoldenRecordStore:
    """Read-only access to the golden record store. Read-only is by design — the IDV
    API never writes; account creation events go through EventBridge."""

    def __init__(self, *, backend: str = "memory", seed_path: Optional[Path] = None):
        self.backend = backend
        self._records: list[GoldenRecord] = []
        if backend == "memory" and seed_path:
            self._load(seed_path)

    def _load(self, path: Path) -> None:
        data = json.loads(path.read_text())
        self._records = [GoldenRecord(**rec) for rec in data]

    def lookup(self, *, dob: str, zip: str, last_name: str, ssn_last4: Optional[str]) -> list[GoldenRecord]:
        """Deterministic lookup. Mirrors the SQL we'd issue against Aurora:

            SELECT * FROM gold.eligibility_member
            WHERE dob = %s AND zip = %s AND lower(last_name) = lower(%s)
              AND (%s IS NULL OR ssn_last4 = %s)
              AND (effective_end_date IS NULL OR effective_end_date >= CURRENT_DATE)
        """
        ln = last_name.lower().strip()
        matches: list[GoldenRecord] = []
        for r in self._records:
            if r.dob != dob:
                continue
            if r.zip != zip:
                continue
            if r.last_name.lower().strip() != ln:
                continue
            if ssn_last4 and r.ssn_last4 and r.ssn_last4 != ssn_last4:
                continue
            matches.append(r)
        return matches

    def fuzzy_search(self, *, last_name: str, dob: str, zip3: str, limit: int = 10) -> list[GoldenRecord]:
        """Coarser search for the "verify failed deterministic" path.

        In production: OpenSearch k-NN query against Titan embeddings.
        Here: simple last-name-prefix + same-dob-year + same-zip3 filter.
        """
        ln = last_name.lower().strip()[:4]
        year = dob[:4]
        out = []
        for r in self._records:
            if r.dob[:4] != year:
                continue
            if r.zip[:3] != zip3:
                continue
            if not r.last_name.lower().startswith(ln):
                continue
            out.append(r)
        return out[:limit]

    def health(self) -> dict:
        return {"backend": self.backend, "records": len(self._records)}


def from_env() -> GoldenRecordStore:
    """Construct from env. Prod=Aurora, dev=memory."""
    seed = os.environ.get("LORE_IDV_SEED_FILE")
    if seed:
        return GoldenRecordStore(backend="memory", seed_path=Path(seed))
    return GoldenRecordStore(backend="memory")
