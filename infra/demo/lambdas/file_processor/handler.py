"""S3-triggered Lambda: parse a partner CSV from `inbox/`, write rows to
DynamoDB as golden records, and copy the file to the bronze tier with a date
partition prefix.

This is the demo's stand-in for the EMR Serverless cleansing job. It's
deliberately small — we're showing the *flow* (file lands → records appear in
DynamoDB → IDV API can verify against them), not production-grade Spark.

For each row in the CSV we compute the lookup_key (zip#dob#last_name_lower)
that the DDB GSI indexes on — same shape the in-memory store does internally.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

DDB_TABLE = os.environ["LORE_IDV_DDB_TABLE"]
BRONZE_BUCKET = os.environ.get("BRONZE_BUCKET")

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb").Table(DDB_TABLE)


def handler(event, _context):
    """S3 event -> upserts to DynamoDB."""
    written = 0
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        log.info("processing s3://%s/%s", bucket, key)

        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        partner_id = _partner_from_key(key)

        rows = list(csv.DictReader(io.StringIO(body)))
        log.info("parsed %d rows for partner=%s", len(rows), partner_id)

        with ddb.batch_writer() as batch:
            for row in rows:
                item = _row_to_item(row, partner_id)
                if item is None:
                    continue
                batch.put_item(Item=item)
                written += 1

        if BRONZE_BUCKET:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            bronze_key = f"partner_id={partner_id}/dt={today}/{key.split('/')[-1]}"
            s3.copy_object(
                Bucket=BRONZE_BUCKET,
                Key=bronze_key,
                CopySource={"Bucket": bucket, "Key": key},
            )
            log.info("copied to s3://%s/%s", BRONZE_BUCKET, bronze_key)

    return {"statusCode": 200, "body": json.dumps({"records_written": written})}


def _partner_from_key(key: str) -> str:
    """Extract partner id from `inbox/<partner_id>/file.csv` or fall back to filename."""
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] == "inbox":
        return parts[1]
    filename = parts[-1]
    # Try to read the slug from "partner_acme_employer.csv" style names.
    if filename.startswith("partner_"):
        return filename.split("_")[1]
    return "unknown"


# Mapping from common partner-CSV column names to canonical fields. Mirrors the
# heuristics in services/schema_inference for known shapes — in production this
# would be driven by the partner data contract YAML.
COLUMN_MAP = {
    "EmployeeID": "partner_member_id",
    "FirstName": "first_name",
    "LastName": "last_name",
    "DOB": "dob",
    "SSN": "ssn",
    "Email": "email",
    "PostalCode": "zip",
    "State": "state",
    "EligStartDate": "effective_start_date",
    "EligEndDate": "effective_end_date",
    "PlanCode": "plan_code",
}


def _row_to_item(row: dict, partner_id: str) -> dict | None:
    """Translate a raw partner CSV row into a DynamoDB item."""
    canonical: dict[str, str] = {}
    for src, value in row.items():
        canon = COLUMN_MAP.get(src)
        if canon and value:
            canonical[canon] = value.strip()

    # Required minimum to be useful.
    needed = {"partner_member_id", "first_name", "last_name", "dob", "zip"}
    if not needed.issubset(canonical):
        log.warning("row missing required fields, skipping: %s", canonical)
        return None

    dob_iso = _coerce_date(canonical["dob"])
    if not dob_iso:
        log.warning("unparseable dob, skipping row: %s", canonical["dob"])
        return None
    canonical["dob"] = dob_iso

    # Normalize zip
    canonical["zip"] = "".join(c for c in canonical["zip"] if c.isdigit())[:5].rjust(5, "0")

    # SSN-last-4 in clear; the full SSN does NOT enter DynamoDB. In production this
    # is where we'd call Skyflow to tokenize the full value. For the demo we keep
    # only the last 4.
    if canonical.get("ssn"):
        digits = "".join(c for c in canonical["ssn"] if c.isdigit())
        if len(digits) >= 4:
            canonical["ssn_last4"] = digits[-4:]
        canonical.pop("ssn", None)

    if canonical.get("effective_start_date"):
        canonical["effective_start_date"] = _coerce_date(canonical["effective_start_date"]) or canonical["effective_start_date"]
    if canonical.get("effective_end_date"):
        canonical["effective_end_date"] = _coerce_date(canonical["effective_end_date"]) or None

    # Build the item.
    grid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{partner_id}|{canonical['partner_member_id']}"))
    last_name_lower = canonical["last_name"].lower().strip()
    item = {
        "golden_record_id": grid,
        "partner_id": partner_id,
        "partner_member_id": canonical["partner_member_id"],
        "first_name": canonical["first_name"],
        "last_name": canonical["last_name"],
        "last_name_lower": last_name_lower,
        "dob": canonical["dob"],
        "zip": canonical["zip"],
        "lookup_key": f"{canonical['zip']}#{canonical['dob']}#{last_name_lower}",
        "effective_start_date": canonical.get("effective_start_date") or "1970-01-01",
    }
    if canonical.get("effective_end_date"):
        item["effective_end_date"] = canonical["effective_end_date"]
    if canonical.get("ssn_last4"):
        item["ssn_last4"] = canonical["ssn_last4"]
    return item


def _coerce_date(value: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y%m%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None
