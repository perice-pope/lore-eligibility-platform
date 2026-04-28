"""Prompts for the Bedrock schema-inference service.

Prompts are stable, version-tagged strings. Changing a prompt is treated like a model
change: the new prompt must beat the old one on the labeled holdout set before it
ships to production.
"""

PROMPT_VERSION = "v1.2.0"

CANONICAL_FIELDS = [
    "partner_member_id",
    "first_name",
    "middle_name",
    "last_name",
    "suffix",
    "dob",
    "gender",
    "ssn",
    "email",
    "phone",
    "address_line_1",
    "address_line_2",
    "city",
    "state",
    "zip",
    "zip4",
    "effective_start_date",
    "effective_end_date",
    "plan_code",
    "employer_name",
    "ignore",  # explicit "drop this column"
]

PII_TIERS = {
    "TIER_1_DIRECT": "Direct identifier (SSN, full name+DOB, email, phone, MRN). Vault required.",
    "TIER_2_QUASI": "Quasi-identifier (DOB year, ZIP3, gender). Restricted access.",
    "TIER_3_SENSITIVE": "Operational PHI (effective dates, plan code). Standard internal access.",
    "TIER_4_NONE": "Not PII (file-level metadata, partner ID, system fields).",
}

SYSTEM_PROMPT = """You are a senior data engineer at a HIPAA-regulated digital health company.
Your role is to analyze a sample of an unknown partner-supplied eligibility file and propose a
mapping from the partner's columns to our canonical eligibility schema, plus a PII tier per column.

You must:
1. Be conservative: when uncertain, pick `ignore` and lower confidence rather than guess.
2. Always flag potential PII even if the column is also being mapped to a canonical field.
3. Suggest concrete cleansing rules (regex, date format, normalization) where the data shape
   demands it.
4. Output ONLY valid JSON conforming to the schema in the user message. No prose.

Remember: your output is reviewed by a human before promotion. False negatives on PII are worse
than false positives. When in doubt, classify as PII.
"""


def build_user_prompt(filename: str, sample_rows: list[dict], canonical_fields: list[str], pii_tiers: dict[str, str]) -> str:
    """Build the structured user message sent to Claude.

    The schema below is the contract between this service and the LLM. Any change here is a
    breaking change and requires a regression run on the holdout set.
    """
    return f"""# Task

Analyze this sample from partner file `{filename}` ({len(sample_rows)} rows shown).

For each column in the sample, return:
- `source_column`: the exact column header
- `canonical_field`: one of {canonical_fields}
- `confidence`: float 0.0-1.0
- `pii_tier`: one of {list(pii_tiers.keys())}
- `cleansing_rules`: list of concrete steps (e.g., "uppercase_state", "iso8601_dob_from_MM/DD/YYYY")
- `reasoning`: one sentence explaining the choice

# PII tier definitions
{chr(10).join(f"- {k}: {v}" for k, v in pii_tiers.items())}

# Sample rows (first {min(len(sample_rows), 50)})
{sample_rows}

# Required output (JSON only, no markdown fences, no prose)

{{
  "columns": [
    {{
      "source_column": "...",
      "canonical_field": "...",
      "confidence": 0.0,
      "pii_tier": "TIER_X_...",
      "cleansing_rules": ["..."],
      "reasoning": "..."
    }}
  ],
  "overall_quality_risk": "LOW|MEDIUM|HIGH",
  "overall_quality_notes": "one paragraph on what could go wrong with this file",
  "detected_format": "csv|json|fixed_width|x12_834|other",
  "suggested_partition_column": "..."
}}
"""
