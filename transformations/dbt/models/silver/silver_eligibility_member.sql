-- The cleansing transformation. Bronze → Silver.
--
-- This is the staff-engineer's-eye-view of cleansing logic. It's deliberately one big
-- CTE chain so the rules are auditable in one place. In production we'd split it for
-- testability, but linear flow makes the cleansing story readable in interviews.
--
-- Tokenization of Tier-1 PII happens UPSTREAM in the cleansing job (Spark) before
-- this dbt model runs. By the time we read bronze here, ssn/email/phone are already
-- in the vault and we have the token columns. This model never touches raw PII.

{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='silver_record_id',
    on_schema_change='fail',
    cluster_by=['partner_id','effective_start_date'],
    pre_hook="ALTER SESSION SET QUERY_TAG = 'silver_cleansing'"
) }}

WITH src AS (
    SELECT
        _ingest_id,
        _partner_id                                              AS partner_id,
        _row_hash                                                AS source_row_hash,
        _ingested_at,
        _contract_version                                        AS contract_version,
        raw_payload
      FROM {{ source('bronze', 'partner_eligibility_raw') }}
    {% if is_incremental() %}
       WHERE _ingested_at >= (SELECT COALESCE(MAX(updated_at), '1970-01-01') FROM {{ this }})
    {% endif %}
),

mapped AS (
    -- Field mapping is driven by per-partner data contract resolved at runtime.
    -- Here we show the canonical extraction shape; in production we'd pivot the
    -- contract YAML to a lookup table joined here.
    SELECT
        _ingest_id,
        partner_id,
        source_row_hash,
        contract_version,
        _ingested_at,
        COALESCE(raw_payload:partner_member_id::STRING,
                 raw_payload:EmployeeID::STRING,
                 raw_payload:member_id::STRING)                  AS partner_member_id,
        raw_payload:first_name_token::STRING                     AS first_name_raw,
        raw_payload:last_name_token::STRING                      AS last_name_raw,
        raw_payload:middle_name::STRING                          AS middle_name_raw,
        raw_payload:suffix::STRING                               AS suffix_raw,
        raw_payload:dob::STRING                                  AS dob_raw,
        UPPER(raw_payload:gender::STRING)                        AS gender_raw,
        raw_payload:ssn_token::STRING                            AS ssn_token,
        raw_payload:ssn_last4::STRING                            AS ssn_last4,
        raw_payload:email_token::STRING                          AS email_token,
        raw_payload:phone_token::STRING                          AS phone_token,
        raw_payload:address_line_1_token::STRING                 AS address_line_1_token,
        raw_payload:address_line_2_token::STRING                 AS address_line_2_token,
        raw_payload:city::STRING                                 AS city_raw,
        raw_payload:state::STRING                                AS state_raw,
        raw_payload:zip::STRING                                  AS zip_raw,
        raw_payload:zip4::STRING                                 AS zip4_raw,
        raw_payload:effective_start_date::STRING                 AS effective_start_date_raw,
        raw_payload:effective_end_date::STRING                   AS effective_end_date_raw,
        raw_payload:plan_code::STRING                            AS plan_code_raw,
        raw_payload:employer_name::STRING                        AS employer_name_raw
      FROM src
),

cleansed AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['partner_id','partner_member_id','contract_version','_ingest_id']) }}
                                                                AS silver_record_id,
        partner_id,
        partner_member_id,
        _ingest_id                                              AS source_ingest_id,
        source_row_hash,
        contract_version,

        -- Names: trim, normalize whitespace, title-case but preserve diacritics
        TRIM(REGEXP_REPLACE(first_name_raw,  '\\s+', ' '))      AS first_name,
        TRIM(REGEXP_REPLACE(middle_name_raw, '\\s+', ' '))      AS middle_name,
        TRIM(REGEXP_REPLACE(last_name_raw,   '\\s+', ' '))      AS last_name,
        TRIM(suffix_raw)                                        AS suffix,

        -- DOB: try multiple formats, then enforce range.
        TRY_TO_DATE(dob_raw)                                    AS dob_iso,
        TRY_TO_DATE(dob_raw, 'MM/DD/YYYY')                      AS dob_us,
        TRY_TO_DATE(dob_raw, 'DD/MM/YYYY')                      AS dob_eu,

        CASE
            WHEN UPPER(gender_raw) IN ('M','MALE')   THEN 'M'
            WHEN UPPER(gender_raw) IN ('F','FEMALE') THEN 'F'
            WHEN UPPER(gender_raw) IN ('X','NB','NONBINARY') THEN 'X'
            ELSE 'U'
        END                                                     AS gender,

        ssn_token, ssn_last4, email_token, phone_token,
        address_line_1_token, address_line_2_token,

        INITCAP(TRIM(city_raw))                                 AS city,
        UPPER(LEFT(REGEXP_REPLACE(state_raw, '[^A-Za-z]', ''), 2)) AS state,
        LPAD(REGEXP_REPLACE(zip_raw,  '[^0-9]', ''), 5, '0')    AS zip,
        LEFT(REGEXP_REPLACE(zip4_raw, '[^0-9]', ''), 4)         AS zip4,

        TRY_TO_DATE(effective_start_date_raw)                   AS effective_start_date,
        TRY_TO_DATE(effective_end_date_raw)                     AS effective_end_date,
        UPPER(TRIM(plan_code_raw))                              AS plan_code,
        TRIM(employer_name_raw)                                 AS employer_name,
        _ingested_at
      FROM mapped
),

dq_evaluated AS (
    SELECT
        c.*,
        COALESCE(dob_iso, dob_us, dob_eu)                       AS dob,
        ARRAY_CONSTRUCT_COMPACT(
            CASE WHEN COALESCE(dob_iso,dob_us,dob_eu) IS NULL                     THEN 'dob_unparseable'                 END,
            CASE WHEN COALESCE(dob_iso,dob_us,dob_eu) > CURRENT_DATE              THEN 'dob_in_future'                   END,
            CASE WHEN COALESCE(dob_iso,dob_us,dob_eu) < '1900-01-01'              THEN 'dob_pre_1900'                    END,
            CASE WHEN c.last_name IS NULL OR LENGTH(c.last_name) = 0              THEN 'last_name_missing'               END,
            CASE WHEN c.first_name IS NULL OR LENGTH(c.first_name) = 0            THEN 'first_name_missing'              END,
            CASE WHEN c.zip IS NOT NULL AND NOT REGEXP_LIKE(c.zip, '^[0-9]{5}$')  THEN 'zip_invalid'                     END,
            CASE WHEN c.state IS NOT NULL AND LENGTH(c.state) <> 2                THEN 'state_invalid'                   END,
            CASE WHEN c.effective_end_date IS NOT NULL
                  AND c.effective_end_date < c.effective_start_date               THEN 'end_before_start'                END,
            CASE WHEN c.ssn_token IS NOT NULL
                  AND NOT c.ssn_token LIKE 'tok\\_%'                              THEN 'ssn_token_format_invalid'        END
        )                                                        AS quality_failures
      FROM cleansed c
)

SELECT
    silver_record_id,
    partner_id,
    partner_member_id,
    first_name,
    middle_name,
    last_name,
    suffix,
    dob,
    gender,
    ssn_token,
    ssn_last4,
    email_token,
    phone_token,
    address_line_1_token,
    address_line_2_token,
    city,
    state,
    zip,
    zip4,
    NULL                                                          AS address_validated,        -- set by USPS validation step downstream
    NULL                                                          AS address_validation_score,
    effective_start_date,
    effective_end_date,
    plan_code,
    employer_name,
    source_ingest_id,
    source_row_hash,
    contract_version,
    -- Quality score: 1.0 minus 0.1 per failure, floored at 0.
    GREATEST(1.0 - 0.1 * ARRAY_SIZE(quality_failures), 0.0)::DECIMAL(3,2)  AS quality_score,
    quality_failures,
    -- Quarantine if a critical failure was hit.
    ARRAY_CONTAINS('dob_unparseable'::VARIANT, quality_failures)
      OR ARRAY_CONTAINS('last_name_missing'::VARIANT, quality_failures)
      OR ARRAY_CONTAINS('ssn_token_format_invalid'::VARIANT, quality_failures)
                                                                  AS is_quarantined,
    _ingested_at                                                  AS created_at,
    CURRENT_TIMESTAMP()                                           AS updated_at
  FROM dq_evaluated
