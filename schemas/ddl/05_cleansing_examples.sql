-- =====================================================================
-- Cleansing example queries — the kind of inconsistency-detection logic
-- referenced by the case study brief. Each example is paired with the
-- *correction* it would drive (a real pipeline parameterizes these).
-- =====================================================================


-- =====================================================================
-- 1. DUPLICATE PII DETECTION — same person, multiple partner_member_ids
--
-- We never trust a single rule; we triangulate.
--
-- HARD signal: same SSN token (across partners) → very likely same person.
-- SOFT signal: same DOB + soundex(name) + ZIP → review.
-- =====================================================================

WITH ssn_dupes AS (
    SELECT
        ssn_token,
        ARRAY_AGG(silver_record_id ORDER BY updated_at DESC)            AS silver_records,
        ARRAY_AGG(DISTINCT partner_id)                                  AS partners,
        ARRAY_AGG(DISTINCT partner_member_id)                           AS partner_member_ids,
        COUNT(*)                                                        AS occurrences
      FROM silver.eligibility_member
     WHERE ssn_token IS NOT NULL
       AND is_quarantined = false
     GROUP BY ssn_token
    HAVING COUNT(DISTINCT (partner_id || '|' || partner_member_id)) > 1
),
soft_dupes AS (
    -- DOB + last_name soundex + zip3 + first_name initial. Won't catch every
    -- variant but is a useful weak signal complement to SSN-based matching.
    SELECT
        dob,
        SOUNDEX(last_name)                                              AS last_sx,
        SUBSTR(zip, 1, 3)                                               AS zip3,
        UPPER(LEFT(first_name, 1))                                      AS first_initial,
        ARRAY_AGG(silver_record_id ORDER BY updated_at DESC)            AS silver_records,
        ARRAY_AGG(DISTINCT partner_id)                                  AS partners,
        COUNT(DISTINCT (partner_id || '|' || partner_member_id))        AS uniq_member_ids
      FROM silver.eligibility_member
     WHERE dob IS NOT NULL
       AND is_quarantined = false
     GROUP BY dob, SOUNDEX(last_name), SUBSTR(zip, 1, 3), UPPER(LEFT(first_name, 1))
    HAVING COUNT(DISTINCT (partner_id || '|' || partner_member_id)) > 1
)
SELECT 'hard_ssn_match'   AS signal, ssn_token AS key, silver_records, partners, occurrences AS uniq_ids
  FROM ssn_dupes
 UNION ALL
SELECT 'soft_demo_match'  AS signal,
       CONCAT_WS('|', dob::STRING, last_sx, zip3, first_initial) AS key,
       silver_records, partners, uniq_member_ids
  FROM soft_dupes;


-- =====================================================================
-- 2. FORMAT-ERROR DETECTION — five specific anomalies seen in the wild
-- =====================================================================

WITH formatted AS (
    SELECT silver_record_id, partner_id, ssn_token, ssn_last4, dob, zip, state, email_token,
           -- Each row gets a list of detected format anomalies.
           ARRAY_CONSTRUCT_COMPACT(
               CASE WHEN ssn_last4 IS NOT NULL
                    AND NOT REGEXP_LIKE(ssn_last4, '^[0-9]{4}$')           THEN 'ssn_last4_nondigits'    END,
               CASE WHEN ssn_last4 IN ('0000','1234','9999')               THEN 'ssn_last4_suspicious'  END,
               CASE WHEN dob IS NOT NULL AND dob > CURRENT_DATE             THEN 'dob_in_future'        END,
               CASE WHEN dob IS NOT NULL AND dob < '1900-01-01'             THEN 'dob_pre_1900'         END,
               CASE WHEN zip IS NOT NULL AND NOT REGEXP_LIKE(zip,'^[0-9]{5}$') THEN 'zip_invalid'      END,
               CASE WHEN zip IS NOT NULL AND zip = '00000'                  THEN 'zip_placeholder'     END,
               CASE WHEN state IS NOT NULL AND LENGTH(state) <> 2           THEN 'state_not_two_char'  END,
               CASE WHEN email_token IS NOT NULL
                    AND NOT email_token LIKE 'tok\\_%'                      THEN 'email_token_invalid' END
           ) AS format_anomalies
      FROM silver.eligibility_member
)
SELECT *
  FROM formatted
 WHERE ARRAY_SIZE(format_anomalies) > 0;


-- =====================================================================
-- 3. CROSS-PARTNER NAME REFRESH (newer partner has a more current name)
-- A common pattern: HR system has the post-marriage surname; older partner
-- doesn't. Use the most recently updated record per golden_record_id.
-- This is what gold/gold_eligibility_member.sql does in production; this
-- query is the diagnostic version.
-- =====================================================================

WITH per_golden AS (
    SELECT
        g.golden_record_id,
        s.partner_id,
        s.last_name,
        s.first_name,
        s.updated_at,
        ROW_NUMBER() OVER (PARTITION BY g.golden_record_id ORDER BY s.updated_at DESC) AS rn
      FROM gold.eligibility_member g
      JOIN silver.eligibility_member s
        ON s.silver_record_id = ANY(g.source_silver_record_ids)
)
SELECT golden_record_id, partner_id AS most_recent_partner, last_name, first_name, updated_at
  FROM per_golden
 WHERE rn = 1;


-- =====================================================================
-- 4. ATTRITION DETECTION — partner sent a snapshot that excludes a
-- previously-active member. Difference between "updated to inactive"
-- and "silently dropped" matters; we treat the latter as soft-attrition
-- requiring confirmation from the partner.
-- =====================================================================

WITH last_seen AS (
    SELECT partner_id, partner_member_id, MAX(updated_at) AS last_seen_at
      FROM silver.eligibility_member
     GROUP BY partner_id, partner_member_id
),
expected_actives AS (
    SELECT primary_partner_id AS partner_id, primary_partner_member_id AS partner_member_id
      FROM gold.eligibility_member
     WHERE is_active = true
),
suspicious_dropouts AS (
    SELECT e.partner_id, e.partner_member_id, ls.last_seen_at,
           DATEDIFF('day', ls.last_seen_at, CURRENT_DATE) AS days_since_seen
      FROM expected_actives e
      LEFT JOIN last_seen ls
        ON e.partner_id = ls.partner_id
       AND e.partner_member_id = ls.partner_member_id
     WHERE ls.last_seen_at IS NULL OR ls.last_seen_at < CURRENT_DATE - INTERVAL '7' DAY
)
SELECT *
  FROM suspicious_dropouts
 ORDER BY days_since_seen DESC;
