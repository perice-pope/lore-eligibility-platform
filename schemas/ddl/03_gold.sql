-- =====================================================================
-- GOLD LAYER — golden records, source-of-truth for identity verification
-- Primary engine: Snowflake (analytics + dbt builds)
-- Hot replica: Aurora Postgres (IDV API hot-path reads)
-- The Aurora copy is refreshed continuously via outbox pattern (see migration-plan.md).
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS gold;

-- One row per resolved person. Many silver records can map to one gold record.
CREATE OR REPLACE TABLE gold.eligibility_member (
    golden_record_id        VARCHAR(36)     NOT NULL,
    primary_partner_id      VARCHAR(64)     NOT NULL,        -- partner this record originated from
    primary_partner_member_id VARCHAR(128)  NOT NULL,

    -- Demographic
    first_name              VARCHAR(100)    NOT NULL,
    middle_name             VARCHAR(100),
    last_name               VARCHAR(100)    NOT NULL,
    suffix                  VARCHAR(20),
    dob                     DATE            NOT NULL,
    gender                  VARCHAR(2),

    -- Addressing (Tier-2; masked at read for non-privileged roles)
    city                    VARCHAR(100),
    state                   CHAR(2),
    zip                     CHAR(5)         NOT NULL,
    zip4                    CHAR(4),

    -- Tier-1 — tokens only
    ssn_token               VARCHAR(64),
    ssn_last4               CHAR(4),
    email_token             VARCHAR(64),
    phone_token             VARCHAR(64),
    address_line_1_token    VARCHAR(64),
    address_line_2_token    VARCHAR(64),

    -- Eligibility window (the most-current of all source records)
    effective_start_date    DATE            NOT NULL,
    effective_end_date      DATE,
    plan_code               VARCHAR(64),
    employer_name           VARCHAR(200),

    -- Source aggregation
    source_partner_ids      ARRAY,                          -- list of partners contributing rows
    source_silver_record_ids ARRAY,                         -- contributing silver records
    last_source_update_at   TIMESTAMP_TZ    NOT NULL,

    -- Quality
    quality_score           NUMBER(3,2)     NOT NULL,
    is_active               BOOLEAN         NOT NULL,        -- effective_end_date is null or in future

    -- Lifecycle
    created_at              TIMESTAMP_TZ    NOT NULL,
    updated_at              TIMESTAMP_TZ    NOT NULL,

    CONSTRAINT gold_eligibility_member_pk PRIMARY KEY (golden_record_id),
    CONSTRAINT gold_eligibility_member_dob_check CHECK (dob >= '1900-01-01' AND dob <= CURRENT_DATE)
)
CLUSTER BY (zip, dob);                                        -- primary IDV lookup pattern

COMMENT ON TABLE gold.eligibility_member IS
'Trusted golden record per person. Source of truth for new account creation in Lore.';

-- ----- Snowflake masking & row-access policies (HIPAA) -----

CREATE OR REPLACE MASKING POLICY mask_dob_to_year AS (val DATE) RETURNS DATE ->
    CASE WHEN CURRENT_ROLE() IN ('IDV_SERVICE_ROLE','COMPLIANCE_ROLE','PII_ELEVATED')
         THEN val
         ELSE DATE_FROM_PARTS(YEAR(val),1,1)
    END;

CREATE OR REPLACE MASKING POLICY mask_pii_token AS (val VARCHAR) RETURNS VARCHAR ->
    CASE WHEN CURRENT_ROLE() IN ('IDV_SERVICE_ROLE','COMPLIANCE_ROLE','PII_ELEVATED')
         THEN val
         ELSE NULL
    END;

ALTER TABLE gold.eligibility_member MODIFY COLUMN dob               SET MASKING POLICY mask_dob_to_year;
ALTER TABLE gold.eligibility_member MODIFY COLUMN ssn_token         SET MASKING POLICY mask_pii_token;
ALTER TABLE gold.eligibility_member MODIFY COLUMN email_token       SET MASKING POLICY mask_pii_token;
ALTER TABLE gold.eligibility_member MODIFY COLUMN phone_token       SET MASKING POLICY mask_pii_token;

CREATE OR REPLACE ROW ACCESS POLICY rap_partner_visibility AS (partner_id VARCHAR) RETURNS BOOLEAN ->
    EXISTS (
        SELECT 1 FROM admin.engineer_partner_grants g
         WHERE g.engineer = CURRENT_USER()
           AND g.partner_id = partner_id
           AND g.granted_until > CURRENT_TIMESTAMP()
    )
    OR CURRENT_ROLE() IN ('IDV_SERVICE_ROLE','COMPLIANCE_ROLE');

ALTER TABLE gold.eligibility_member ADD ROW ACCESS POLICY rap_partner_visibility ON (primary_partner_id);

-- Outbox table — written by every gold update; CDC'd into Aurora by a sub-minute job.
CREATE OR REPLACE TABLE gold.eligibility_member_outbox (
    outbox_id               VARCHAR(36)     NOT NULL,
    golden_record_id        VARCHAR(36)     NOT NULL,
    operation               VARCHAR(16)     NOT NULL,        -- 'upsert'|'delete'
    payload                 VARIANT         NOT NULL,        -- full row state at change
    enqueued_at             TIMESTAMP_TZ    NOT NULL,
    delivered_at            TIMESTAMP_TZ,
    delivery_attempts       NUMBER          DEFAULT 0,
    CONSTRAINT outbox_pk PRIMARY KEY (outbox_id)
);
