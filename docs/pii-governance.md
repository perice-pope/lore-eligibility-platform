# PII & PHI Governance

Plain English: **how we protect the personal data partners send us**, who can see what,
how we prove it to auditors, and how we recover if something goes wrong.

---

## Regulatory scope

Lore is a HIPAA-covered entity (Medicare ACO context). The eligibility data we ingest is **PHI**
under HIPAA because it links an individual to receipt of healthcare. Therefore:

- **HIPAA Privacy Rule** — minimum necessary use, individual rights, BAAs with all subprocessors.
- **HIPAA Security Rule** — administrative, physical, technical safeguards.
- **HITECH** — breach notification within 60 days of discovery.
- **State laws** — CCPA/CPRA (California), NY SHIELD, Texas Identity Theft Enforcement and
  Protection Act, MA 201 CMR 17, Colorado CPA. The strictest state's requirement applies for that
  state's residents.
- **SOC 2 Type II** — operating effectiveness over time. We are audited annually.

---

## The PII inventory

We classify every field that flows through the platform into one of four tiers. Treatment differs
by tier.

| Tier | Examples | Treatment |
|---|---|---|
| **Tier 1 — Direct identifiers** | SSN, full name + DOB, member ID, email, phone, address, MRN | Skyflow vault token; never in plaintext outside vault. |
| **Tier 2 — Quasi-identifiers** | DOB year, ZIP3, gender, employer name | Stored in clear in curated, but row-access policies restrict. |
| **Tier 3 — Sensitive context** | Effective dates, plan code, partner ID | Stored in clear; access logged but unrestricted internally. |
| **Tier 4 — Non-PII metadata** | File land time, record count, partner-side schema version | Open. |

The classification per field per partner is in the data contract (`schemas/data_contracts/*.yml`)
and is enforced programmatically — Skyflow rejects writes of Tier-1 values to non-vault tables.

---

## Encryption

| Where | Mechanism | Key |
|---|---|---|
| In transit | TLS 1.3, mTLS for service-to-service inside VPC | ACM-issued |
| At rest, S3 | SSE-KMS, AWS KMS CMK | One CMK per partner |
| At rest, Aurora | KMS encryption + cluster-level | One CMK per cluster |
| At rest, Snowflake | Tri-Secret Secure (Snowflake-managed key + customer-managed key in KMS) | Per env |
| In Skyflow | AES-256 + format-preserving + per-record DEK | Vault-managed |
| Application layer | Field-level encryption for Tier 1 in transit between services | KMS data keys, cached 5min |

**One CMK per partner** is deliberate. When a partner offboards, we revoke their CMK and all
their data is cryptographically shredded — even if a backup tape exists. This is a HIPAA
right-to-be-forgotten lever that's hard to pull with shared keys.

---

## Tokenization (Skyflow)

Skyflow is a managed PII vault. Architecturally:

```
Application services  ──store token──▶  Application DB / warehouse
        │
        │  detokenize(token) for purpose=X
        ▼
   Skyflow Vault  ───decrypt + return + audit log───▶  caller
        │
        │  raw value stored encrypted, sharded across vaults per data residency
        ▼
   Skyflow infra
```

What we put in the vault: SSN, full address, email, phone, MRN, DOB (in some configurations).
What we keep outside: tokens, last-4 SSN, date components for analytics (year only), masked
versions for display.

**Why tokens not encryption?** Encryption gives confidentiality but the ciphertext is unique
per encryption — joins break. Tokenization gives a stable token per value: we can join two
tables on tokenized SSN without ever seeing plaintext.

**Format-preserving tokens** keep the same shape (`123-45-6789`), so legacy applications and
downstream systems don't need refactoring.

---

## Access control

### Identity layer
- AWS IAM Identity Center (SSO) → all engineers federate.
- No long-lived IAM users for humans. Roles only.
- Service-to-service: IRSA (EKS) or task roles (ECS).

### Data layer

| Resource | Default access | Elevated access |
|---|---|---|
| S3 raw landing buckets | Closed; only ingest service role can read | Engineers read via Athena with masked queries |
| S3 silver bucket | Engineers read (tokenized data only) | — |
| Snowflake gold | BI users read masked views | Engineers read unmasked with row-level policy and reason logging |
| Aurora golden record | Only IDV service can read | Read replicas for engineers, masked |
| Skyflow detokenize | Reserved for IDV API + named operational use cases | Manual approval flow with two-person rule |

### Two-person rule for detokenization

Any human-initiated detokenize call requires:

1. A logged business reason ("Member support case #12345").
2. Approval from a second engineer (Slack approval workflow).
3. Auto-expiring temporary credentials (max 60 min).
4. Posted to `#pii-access` Slack channel (read-only audit feed).

This is a Skyflow policy, not something we enforce in app code. Auditors love this.

### Row-level access policies in Snowflake

```sql
-- Snowflake row access policy: engineer can only see partners they're authorized for
CREATE ROW ACCESS POLICY rap_partner_visibility AS (partner_id VARCHAR) RETURNS BOOLEAN ->
  EXISTS (
    SELECT 1 FROM admin.engineer_partner_grants
    WHERE engineer = CURRENT_USER()
      AND partner_id = partner_id
      AND granted_until > CURRENT_TIMESTAMP()
  );

ALTER TABLE gold.eligibility_member ADD ROW ACCESS POLICY rap_partner_visibility ON (partner_id);
```

### Dynamic data masking on Tier 2 fields

```sql
CREATE MASKING POLICY mask_dob_to_year AS (val DATE) RETURNS DATE ->
  CASE
    WHEN CURRENT_ROLE() IN ('IDV_SERVICE_ROLE', 'COMPLIANCE_AUDITOR') THEN val
    ELSE DATE_FROM_PARTS(YEAR(val), 1, 1)
  END;

ALTER TABLE gold.eligibility_member MODIFY COLUMN dob SET MASKING POLICY mask_dob_to_year;
```

---

## Anonymization vs pseudonymization — which we use, when

| Technique | Where used | Why |
|---|---|---|
| **Pseudonymization** (tokenization) | Operational data: gold, Aurora, IDV | Reversible by Skyflow when business need + approval; enables joins. |
| **Anonymization** (k-anonymity, generalization) | Analytics datasets exposed to data science / ML | Irreversible; supports modeling without consent fatigue. |
| **Hashing (HMAC with rotating key)** | Cross-partner deduplication signals | One-way but deterministic for joins; key rotated annually with re-hash. |
| **Differential privacy** (future) | Aggregate metrics shared with partners | Not in v1; flagged for v2. |

We do **not** rely on anonymization for HIPAA compliance because re-identification attacks are
real (the Latanya Sweeney 87% rule). Anonymized datasets are still treated as Limited Data Sets
under HIPAA.

---

## Audit logging

Every PII touch generates a structured log:

```json
{
  "ts": "2026-04-28T14:23:11Z",
  "actor": "perice@lore.co",
  "actor_type": "human",
  "action": "detokenize",
  "resource": "eligibility.ssn",
  "resource_id": "tok_8f3a...c2",
  "purpose": "support_case_12345",
  "approved_by": "lila@lore.co",
  "ip": "10.0.4.22",
  "session": "ssn_abc123",
  "result": "success"
}
```

These logs go to:

1. **CloudWatch Logs** (retention 30d, hot lookup)
2. **S3 audit bucket** with Object Lock in compliance mode (retention 7y, immutable)
3. **Datadog** for real-time alerting on anomalous patterns

Anomaly detection on the audit feed catches: unusual hours, unusual volume, unusual
record-per-actor ratios. Alerts go to the Compliance Slack channel.

---

## Breach response

We treat any unintended PII access as a potential breach. The runbook:

1. **Contain** — disable the affected credential / role / service within 15 minutes.
2. **Assess** — what was accessed? How many records? Which partners? Use the audit log.
3. **Notify internally** within 1 hour — Compliance, Legal, CTO.
4. **Notify partners** within 24 hours per BAA terms.
5. **Notify individuals** per HIPAA breach notification rule (≥500 affected → HHS + media in 60 days).
6. **Postmortem** within 14 days, blameless, published internally with action items tracked.

We rehearse this quarterly. Tabletop exercise covers a different scenario each time
(insider threat, vendor breach, ransomware on a partner's system).

---

## Data minimization

We **only ingest fields the data contract explicitly authorizes.** If a partner sends extra
fields (a common drift mode), the cleansing job logs a warning and **drops** them — does not
store them. This is enforced in the bronze→silver transformation.

We **delete or de-identify** records when:

- Partner offboards (90 days post-termination, per BAA).
- Member opts out of Lore (full deletion within 30 days, retain de-identified shadow for ACO compliance).
- Retention period expires (7 years post-last-eligibility, HIPAA minimum).

Deletion is not soft-delete. We run a quarterly deletion job that:

1. Identifies records hitting retention cutoff.
2. Detokenizes from Skyflow → null.
3. Deletes from Aurora.
4. Deletes from Snowflake gold.
5. Deletes from Iceberg silver via `DELETE FROM` (Iceberg supports it natively).
6. **Bronze landing files are kept** because they're the immutable audit trail. They're encrypted
   with the partner CMK, which we destroy at the 7-year mark — cryptographic shredding.

---

## Subprocessor list (BAA required for each)

- AWS (everything we run on)
- Snowflake
- Skyflow
- Datadog
- Dagster Cloud (hybrid agent — only metadata leaves our VPC, but we still BAA)
- Lob (USPS validation)
- PagerDuty (no PHI in payloads, but we BAA defensively)

Anyone we add to this list needs Compliance review + signed BAA before we pipe a single byte of
PHI through them.
