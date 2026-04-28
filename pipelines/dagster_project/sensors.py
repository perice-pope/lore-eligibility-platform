"""S3 landing-zone sensor.

Monitors `s3://lore-eligibility-raw-{partner_id}/inbox/` for new files, validates
against per-partner expected cadence, and triggers the bronze asset for the
relevant partition.
"""

from __future__ import annotations

from dagster import (
    DefaultSensorStatus,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    sensor,
)


@sensor(
    minimum_interval_seconds=60,
    default_status=DefaultSensorStatus.RUNNING,
    description="Polls partner SFTP landing buckets for new files; triggers bronze ingest.",
)
def partner_file_landed_sensor(context: SensorEvaluationContext):
    # In production: enumerate S3 keys via boto3 list_objects_v2 with the prefix
    # last_seen marker stored in Dagster cursor. For each new key:
    #   1. Read schema fingerprint from object metadata (set by AWS Transfer Family hook).
    #   2. If fingerprint != registered contract, raise an alert AND still ingest
    #      to quarantine (so we don't silently drop files).
    #   3. Submit a RunRequest for bronze_partner_eligibility with the partition key.
    new_files = []  # placeholder
    if not new_files:
        return SkipReason("no new partner files in landing zone")

    return [
        RunRequest(
            run_key=f"{f['partner']}-{f['key']}",
            tags={"partner_id": f["partner"], "source_key": f["key"]},
            partition_key=f["partition_date"],
        )
        for f in new_files
    ]
