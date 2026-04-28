-- Gold layer: one row per resolved person across all partners.
-- Joins silver records to entity-resolution decisions; chooses the most-current row
-- per golden_record_id as the canonical state.

{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='golden_record_id',
    cluster_by=['zip','dob']
) }}

WITH eligible_silver AS (
    SELECT *
      FROM {{ ref('silver_eligibility_member') }}
     WHERE is_quarantined = false
),

decisions AS (
    SELECT
        incoming_silver_record_id,
        matched_golden_record_id,
        decision,
        decided_at
      FROM {{ ref('silver_entity_resolution_decisions') }}
     WHERE decision IN ('AUTO_MATCH','NO_MATCH')
),

-- Each silver record is assigned a golden_record_id: either matched or new.
assigned AS (
    SELECT
        s.*,
        COALESCE(d.matched_golden_record_id, s.silver_record_id) AS golden_record_id
      FROM eligible_silver s
      LEFT JOIN decisions d
        ON s.silver_record_id = d.incoming_silver_record_id
),

-- Pick the most-current row per golden_record_id (most recent updated_at wins).
ranked AS (
    SELECT
        a.*,
        ROW_NUMBER() OVER (PARTITION BY a.golden_record_id ORDER BY a.updated_at DESC) AS rn,
        ARRAY_AGG(DISTINCT a.partner_id)            OVER (PARTITION BY a.golden_record_id) AS source_partner_ids,
        ARRAY_AGG(DISTINCT a.silver_record_id)      OVER (PARTITION BY a.golden_record_id) AS source_silver_record_ids
      FROM assigned a
)

SELECT
    golden_record_id,
    partner_id                    AS primary_partner_id,
    partner_member_id             AS primary_partner_member_id,
    first_name,
    middle_name,
    last_name,
    suffix,
    dob,
    gender,
    city,
    state,
    zip,
    zip4,
    ssn_token,
    ssn_last4,
    email_token,
    phone_token,
    address_line_1_token,
    address_line_2_token,
    effective_start_date,
    effective_end_date,
    plan_code,
    employer_name,
    source_partner_ids,
    source_silver_record_ids,
    updated_at                    AS last_source_update_at,
    quality_score,
    (effective_end_date IS NULL OR effective_end_date >= CURRENT_DATE) AS is_active,
    CURRENT_TIMESTAMP()           AS created_at,
    CURRENT_TIMESTAMP()           AS updated_at
  FROM ranked
 WHERE rn = 1
