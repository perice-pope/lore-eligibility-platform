"""Golden record store abstraction.

The IDV API talks to a `GoldenRecordStore` interface (Protocol). Concrete backends:

  - **InMemory** — JSON seed file. Used in tests and the local demo.
  - **DynamoDB** — used for the AWS cloud demo. Stand-in for Aurora in production.
  - **(Production)** — Aurora Postgres + OpenSearch for vector fuzzy fallback.

The interface is narrow on purpose: the IDV API uses only `lookup`, `fuzzy_search`,
and `health`. Anything fancier belongs in the offline pipeline. Backends are picked
at runtime by `from_env()` reading `LORE_IDV_STORE_BACKEND` (`memory` | `dynamodb`).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

log = logging.getLogger("idv.store")


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


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class GoldenRecordStoreProtocol(Protocol):
    def lookup(
        self, *, dob: str, zip: str, last_name: str, ssn_last4: Optional[str]
    ) -> list[GoldenRecord]: ...

    def fuzzy_search(
        self, *, last_name: str, dob: str, zip3: str, limit: int = 10
    ) -> list[GoldenRecord]: ...

    def health(self) -> dict: ...


# ---------------------------------------------------------------------------
# In-memory backend (default; used in tests and local demo)
# ---------------------------------------------------------------------------


class GoldenRecordStore:
    """In-memory backend. Read-only — the IDV API never writes."""

    def __init__(self, *, backend: str = "memory", seed_path: Optional[Path] = None):
        self.backend = backend
        self._records: list[GoldenRecord] = []
        if backend == "memory" and seed_path:
            self._load(seed_path)

    def _load(self, path: Path) -> None:
        data = json.loads(path.read_text())
        self._records = [GoldenRecord(**rec) for rec in data]

    def lookup(
        self, *, dob: str, zip: str, last_name: str, ssn_last4: Optional[str]
    ) -> list[GoldenRecord]:
        """Mirrors the production SQL:

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

    def fuzzy_search(
        self, *, last_name: str, dob: str, zip3: str, limit: int = 10
    ) -> list[GoldenRecord]:
        """Coarser search for the "verify failed deterministic" path.

        In production: OpenSearch k-NN query against Titan embeddings.
        Here: simple last-name-prefix + same-dob-year + same-zip3 filter.
        """
        ln = last_name.lower().strip()[:4]
        year = dob[:4]
        out: list[GoldenRecord] = []
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


# ---------------------------------------------------------------------------
# DynamoDB backend (used in the AWS cloud demo)
# ---------------------------------------------------------------------------


class DynamoDBGoldenRecordStore:
    """DynamoDB-backed store with the same read interface as the in-memory one.

    Production analog: Aurora Postgres with the indexes defined in
    `schemas/ddl/04_aurora_idv.sql`. Aurora gives us joins, transactions, trigram
    indexes, and predictable per-row latency. For the demo, DynamoDB delivers the
    same sub-10ms reads at zero cost on free tier.

    Schema:
      Primary key:
          golden_record_id (string)
      Global secondary index `lookup_key_index`:
          PK = lookup_key (string) = "{zip}#{dob}#{last_name_lower}"
      All other fields stored as attributes; `last_name_lower` and `lookup_key`
      are computed at write time by the seed/processor lambdas.
    """

    LOOKUP_GSI = "lookup_key_index"

    def __init__(self, *, table_name: str, region: str = "us-east-1"):
        try:
            import boto3  # imported lazily so memory mode works without boto3
        except ImportError as e:
            raise RuntimeError(
                "boto3 is required for the DynamoDB backend. "
                "Install with `pip install boto3` or use the memory backend."
            ) from e
        self.table_name = table_name
        self.region = region
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        log.info("dynamodb store ready table=%s region=%s", table_name, region)

    @staticmethod
    def make_lookup_key(*, zip: str, dob: str, last_name: str) -> str:
        return f"{zip}#{dob}#{last_name.lower().strip()}"

    def lookup(
        self, *, dob: str, zip: str, last_name: str, ssn_last4: Optional[str]
    ) -> list[GoldenRecord]:
        from boto3.dynamodb.conditions import Key

        key = self.make_lookup_key(zip=zip, dob=dob, last_name=last_name)
        resp = self._table.query(
            IndexName=self.LOOKUP_GSI,
            KeyConditionExpression=Key("lookup_key").eq(key),
        )
        out: list[GoldenRecord] = []
        for item in resp.get("Items", []):
            if ssn_last4 and item.get("ssn_last4") and item["ssn_last4"] != ssn_last4:
                continue
            out.append(_item_to_record(item))
        return out

    def fuzzy_search(
        self, *, last_name: str, dob: str, zip3: str, limit: int = 10
    ) -> list[GoldenRecord]:
        """For the demo, Scan is fine (≤100 records). At production scale this would
        be an OpenSearch k-NN query — see services/entity_resolution/embeddings.py.
        """
        from boto3.dynamodb.conditions import Attr

        ln = last_name.lower().strip()[:4]
        year = dob[:4]
        resp = self._table.scan(
            FilterExpression=(
                Attr("zip").begins_with(zip3)
                & Attr("dob").begins_with(year)
                & Attr("last_name_lower").begins_with(ln)
            ),
            Limit=max(limit * 4, 25),  # FilterExpression filters AFTER read
        )
        items = resp.get("Items", [])[:limit]
        return [_item_to_record(item) for item in items]

    def health(self) -> dict:
        try:
            desc = self._table.meta.client.describe_table(TableName=self.table_name)
            t = desc["Table"]
            return {
                "backend": "dynamodb",
                "table": self.table_name,
                "region": self.region,
                "status": t["TableStatus"],
                "item_count": t.get("ItemCount", "unknown"),
            }
        except Exception as e:
            return {
                "backend": "dynamodb",
                "table": self.table_name,
                "region": self.region,
                "status": "error",
                "error": str(e),
            }


def _item_to_record(item: dict) -> GoldenRecord:
    return GoldenRecord(
        golden_record_id=item["golden_record_id"],
        partner_id=item["partner_id"],
        partner_member_id=item["partner_member_id"],
        first_name=item["first_name"],
        last_name=item["last_name"],
        dob=item["dob"],
        zip=item["zip"],
        ssn_last4=item.get("ssn_last4"),
        email_token=item.get("email_token"),
        phone_token=item.get("phone_token"),
        address_line_1_token=item.get("address_line_1_token"),
        effective_start_date=item["effective_start_date"],
        effective_end_date=item.get("effective_end_date"),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def from_env() -> GoldenRecordStoreProtocol:
    """Construct a store from environment variables.

    Env vars:
        LORE_IDV_STORE_BACKEND  — "memory" (default) | "dynamodb"
        LORE_IDV_SEED_FILE      — JSON path for memory backend (optional)
        LORE_IDV_DDB_TABLE      — table name for dynamodb backend
                                  (default: "lore-eligibility-golden-records")
        AWS_REGION              — region for dynamodb backend (default: us-east-1)
    """
    backend = os.environ.get("LORE_IDV_STORE_BACKEND", "memory").lower()

    if backend == "dynamodb":
        return DynamoDBGoldenRecordStore(
            table_name=os.environ.get(
                "LORE_IDV_DDB_TABLE", "lore-eligibility-golden-records"
            ),
            region=os.environ.get("AWS_REGION", "us-east-1"),
        )

    # default: in-memory backend
    seed_env = os.environ.get("LORE_IDV_SEED_FILE")
    if seed_env and Path(seed_env).exists():
        return GoldenRecordStore(backend="memory", seed_path=Path(seed_env))

    # fall back to repo's bundled seed
    default = (
        Path(__file__).resolve().parent.parent.parent
        / "samples"
        / "golden_records_seed.json"
    )
    if default.exists():
        return GoldenRecordStore(backend="memory", seed_path=default)

    return GoldenRecordStore(backend="memory")
