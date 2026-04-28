-- =====================================================================
-- AURORA POSTGRES — IDV API hot path
-- The minimum schema needed for the verification API to hit p99 < 150ms.
-- Refreshed continuously from gold.eligibility_member_outbox via outbox-relay.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS idv;

CREATE TABLE IF NOT EXISTS idv.eligibility_member (
    golden_record_id        UUID            PRIMARY KEY,
    primary_partner_id      TEXT            NOT NULL,
    primary_partner_member_id TEXT          NOT NULL,
    first_name              TEXT            NOT NULL,
    last_name               TEXT            NOT NULL,
    dob                     DATE            NOT NULL,
    zip                     CHAR(5)         NOT NULL,
    ssn_last4               CHAR(4),
    email_token             TEXT,
    phone_token             TEXT,
    address_line_1_token    TEXT,
    effective_start_date    DATE            NOT NULL,
    effective_end_date      DATE,
    is_active               BOOLEAN         NOT NULL,
    last_source_update_at   TIMESTAMPTZ     NOT NULL,

    CONSTRAINT idv_dob_range CHECK (dob >= '1900-01-01' AND dob <= CURRENT_DATE),
    CONSTRAINT idv_zip_format CHECK (zip ~ '^[0-9]{5}$')
);

-- Compound index optimized for the primary IDV query.
CREATE INDEX IF NOT EXISTS ix_idv_member_lookup
    ON idv.eligibility_member (zip, dob, lower(last_name))
    INCLUDE (golden_record_id, primary_partner_id, ssn_last4, is_active);

-- Trigram index for the fuzzy fallback ("did you mean...") path.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS ix_idv_member_lastname_trgm
    ON idv.eligibility_member USING gin (lower(last_name) gin_trgm_ops);

-- Outbox-consumer state. Single-row table tracks the last delivered offset.
CREATE TABLE IF NOT EXISTS idv.outbox_offset (
    consumer        TEXT PRIMARY KEY,
    last_outbox_id  UUID,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Materialized stats view for ops dashboards (refreshed every minute).
CREATE MATERIALIZED VIEW IF NOT EXISTS idv.partner_member_counts AS
SELECT primary_partner_id,
       COUNT(*) FILTER (WHERE is_active)             AS active_members,
       COUNT(*) FILTER (WHERE NOT is_active)         AS inactive_members,
       MAX(last_source_update_at)                     AS most_recent_update
  FROM idv.eligibility_member
 GROUP BY primary_partner_id;

CREATE UNIQUE INDEX IF NOT EXISTS ix_partner_member_counts ON idv.partner_member_counts (primary_partner_id);
