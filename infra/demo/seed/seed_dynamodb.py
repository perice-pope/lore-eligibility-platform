"""Seed the DynamoDB golden-records table from samples/golden_records_seed.json.

Idempotent: re-running upserts the same items.

Usage:
    cd infra/demo
    python seed/seed_dynamodb.py
        --table $(terraform output -raw ddb_table) \\
        --region us-east-1
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--seed",
        default=str(Path(__file__).resolve().parents[3] / "samples" / "golden_records_seed.json"),
    )
    args = parser.parse_args()

    try:
        import boto3
    except ImportError:
        print("boto3 is required. Install with `pip install boto3`.", file=sys.stderr)
        return 2

    seed_path = Path(args.seed)
    if not seed_path.exists():
        print(f"seed file not found: {seed_path}", file=sys.stderr)
        return 3

    records = json.loads(seed_path.read_text())
    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)

    written = 0
    with table.batch_writer() as batch:
        for r in records:
            item = {
                # Use a stable golden_record_id derived from input id, keeping the
                # human-friendly G-XXXX from the seed file.
                "golden_record_id": r["golden_record_id"],
                "partner_id": r["partner_id"],
                "partner_member_id": r["partner_member_id"],
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "last_name_lower": r["last_name"].lower().strip(),
                "dob": r["dob"],
                "zip": r["zip"],
                "lookup_key": f"{r['zip']}#{r['dob']}#{r['last_name'].lower().strip()}",
                "effective_start_date": r["effective_start_date"],
            }
            for opt in ("ssn_last4", "email_token", "phone_token", "address_line_1_token", "effective_end_date"):
                v = r.get(opt)
                if v:
                    item[opt] = v
            batch.put_item(Item=item)
            written += 1

    print(f"seeded {written} golden records into {args.table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
