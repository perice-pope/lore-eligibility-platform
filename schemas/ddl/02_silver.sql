-- =====================================================================
-- SILVER LAYER — cleansed, normalized, tokenized
-- Engine: AWS Glue / EMR Spark for transforms; readable from Athena/Trino/Snowflake
-- Storage: S3 + Apache Iceberg + Glue Catalog
-- PII: Tier-1 fields are TOKENS only. Raw PII is in the Skyflow vault.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS silver
WITH (location = 's3://lore-eligibility-silver/');

CREATE TABLE IF NOT EXISTS silver.eligibility_member (
    -- Surrogate identifiers
    silver_record_id        VARCHAR(36) NOT NULL,            -- uuid
    partner_id              VARCHAR(64) NOT NULL,
    partner_member_id       VARCHAR(128) NOT NULL,           -- as supplied by partner

    -- Demographic (cleansed)
    first_name              VARCHAR(100),                    -- title-cased, diacritics preserved
    middle_name             VARCHAR(100),
    last_name               VARCHAR(100),
    suffix                  VARCHAR(20),
    dob                     DATE,                            -- ISO; YEAR(dob) >= 1900
    gender                  VARCHAR(2),                      -- M|F|X|U

    -- PII tokens (Tier-1). Raw values in Skyflow only.
    ssn_token               VARCHAR(64),                     -- format-preserving Skyflow token
    ssn_last4               VARCHAR(4),                      -- kept in clear by policy
    email_token             VARCHAR(64),
    phone_token             VARCHAR(64),
    address_line_1_token    VARCHAR(64),
    address_line_2_token    VARCHAR(64),

    -- Geographic (Tier-2; in clear, dynamic-masked at read time)
    city                    VARCHAR(100),
    state                   CHAR(2),                         -- USPS abbrev
    zip                     CHAR(5),
    zip4                    CHAR(4),
    address_validated       BOOLEAN,                         -- true if USPS-verified
    address_validation_score DECIMAL(3,2),

    -- Eligibility window
    effective_start_date    DATE NOT NULL,
    effective_end_date      DATE,                            -- NULL = currently active
    plan_code               VARCHAR(64),
    employer_name           VARCHAR(200),

    -- Provenance
    source_ingest_id        VARCHAR(36) NOT NULL,
    source_row_hash         VARCHAR(64) NOT NULL,
    contract_version        INTEGER NOT NULL,

    -- Quality flags
    quality_score           DECIMAL(3,2),                    -- composite 0-1
    quality_failures        ARRAY(VARCHAR),                  -- list of failed-check codes
    is_quarantined          BOOLEAN NOT NULL,                -- true => excluded from gold

    -- Lifecycle timestamps
    created_at              TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    updated_at              TIMESTAMP(6) WITH TIME ZONE NOT NULL,

    -- Constraints
    CONSTRAINT silver_eligibility_member_pk PRIMARY KEY (silver_record_id),
    CONSTRAINT silver_eligibility_member_partner_key UNIQUE (partner_id, partner_member_id, contract_version)
)
WITH (
    format = 'ICEBERG',
    partitioning = ARRAY['partner_id', 'months(effective_start_date)'],
    format_version = 2,
    write_compression = 'ZSTD',
    sorted_by = ARRAY['dob', 'last_name']             -- improves nearest-neighbor read patterns
);

-- Resolution decisions emitted by the entity-resolution service.
-- One row per merge or no-match decision. Kept indefinitely as audit.
CREATE TABLE IF NOT EXISTS silver.entity_resolution_decisions (
    decision_id             VARCHAR(36) NOT NULL,
    incoming_silver_record_id VARCHAR(36) NOT NULL,
    matched_golden_record_id  VARCHAR(36),                   -- null on no-match
    decision                VARCHAR(16) NOT NULL,            -- AUTO_MATCH|REVIEW|NO_MATCH
    score                   DECIMAL(4,3) NOT NULL,
    stage                   VARCHAR(32) NOT NULL,            -- deterministic|embedding|embedding+llm|no_candidate
    embedding_similarity    DECIMAL(4,3),
    llm_reasoning           VARCHAR(1000),
    candidates_considered   INTEGER,
    decided_at              TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    resolver_version        VARCHAR(32) NOT NULL,
    model_id                VARCHAR(128)
)
WITH (
    format = 'ICEBERG',
    partitioning = ARRAY['days(decided_at)'],
    format_version = 2
);
