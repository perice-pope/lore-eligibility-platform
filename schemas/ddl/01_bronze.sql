-- =====================================================================
-- BRONZE LAYER — landing-as-Iceberg
-- Engine: AWS Glue / EMR Spark / Trino (all read Iceberg natively)
-- Storage: S3 + Apache Iceberg + Glue Catalog
-- Encryption: SSE-KMS with per-partner CMK
-- Retention: 7 years (HIPAA), Object Lock in compliance mode at the S3 layer
-- Purpose: replay, audit, schema-on-read fidelity to source
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS bronze
WITH (
    location = 's3://lore-eligibility-bronze/',
    properties = MAP(ARRAY['retention_years'], ARRAY['7'])
);

-- One table per (partner, source-table). Partition by ingest date for cheap pruning.
CREATE TABLE IF NOT EXISTS bronze.partner_eligibility_raw (
    -- Provenance (filled by ingestion engine)
    _ingest_id           VARCHAR     NOT NULL,    -- uuid for this batch / event
    _partner_id          VARCHAR     NOT NULL,
    _source_file         VARCHAR,                  -- s3 path of source file (bulk only)
    _source_lsn          VARCHAR,                  -- LSN/offset for CDC events
    _source_op           VARCHAR,                  -- 'insert'|'update'|'delete'|'snapshot'
    _row_hash            VARCHAR     NOT NULL,    -- SHA-256 of original row for dedupe
    _ingested_at         TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    _contract_version    INTEGER     NOT NULL,    -- which data-contract revision was applied

    -- Raw payload preserved as a struct/map. We do NOT fix the schema here on purpose.
    raw_payload          JSON        NOT NULL,    -- the actual row, untouched
    raw_columns          ARRAY(VARCHAR)            -- column order for ordered-CSV replay
)
WITH (
    format = 'ICEBERG',
    partitioning = ARRAY['_partner_id', 'days(_ingested_at)'],
    format_version = 2,                           -- enables row-level deletes (MoR)
    write_compression = 'ZSTD'
);

-- Quarantine table — rows that failed schema validation get parked here, never lost.
CREATE TABLE IF NOT EXISTS bronze.partner_eligibility_quarantine (
    _ingest_id           VARCHAR     NOT NULL,
    _partner_id          VARCHAR     NOT NULL,
    _source_file         VARCHAR,
    _row_hash            VARCHAR     NOT NULL,
    _ingested_at         TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    quarantine_reason    VARCHAR     NOT NULL,    -- 'schema_invalid'|'pii_classification_drift'|'unparseable'|'oversize'
    quarantine_detail    VARCHAR,                  -- structured error
    raw_payload          JSON,
    raw_bytes_b64        VARCHAR                   -- if we couldn't even parse JSON
)
WITH (
    format = 'ICEBERG',
    partitioning = ARRAY['_partner_id', 'days(_ingested_at)'],
    format_version = 2,
    write_compression = 'ZSTD'
);

-- Per-partner schema fingerprint history. New fingerprint = potential drift = page humans.
CREATE TABLE IF NOT EXISTS bronze.partner_schema_history (
    _partner_id          VARCHAR     NOT NULL,
    schema_fingerprint   VARCHAR     NOT NULL,    -- sha256 of sorted column names + types
    sample_columns       ARRAY(VARCHAR),
    first_seen_at        TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    last_seen_at         TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    file_count           BIGINT      NOT NULL,
    contract_version_at_first_seen  INTEGER
)
WITH (
    format = 'ICEBERG',
    format_version = 2
);
