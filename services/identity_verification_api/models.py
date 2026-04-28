"""API request/response models. Pydantic for validation + auto-OpenAPI."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class VerifyRequest(BaseModel):
    """Inbound verification attempt from the mobile app sign-up flow."""
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    dob: date
    zip: str = Field(..., min_length=3, max_length=10)
    ssn_last4: Optional[str] = Field(None, min_length=4, max_length=4, pattern=r"^\d{4}$")
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    partner_hint: Optional[str] = Field(
        None, description="Optional partner code from referral/landing page; speeds up matching."
    )

    @field_validator("zip")
    @classmethod
    def _zip_norm(cls, v: str) -> str:
        digits = "".join(c for c in v if c.isdigit())
        return digits[:5].rjust(5, "0")


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"        # multiple plausible matches → step-up KBA
    NEEDS_REVIEW = "NEEDS_REVIEW"  # match score in human-review band
    INELIGIBLE = "INELIGIBLE"      # found but coverage_end_date < today


class VerifyResponse(BaseModel):
    status: VerificationStatus
    correlation_id: str = Field(..., description="Use in support tickets and logs.")
    golden_record_id: Optional[str] = None
    partner_id: Optional[str] = None
    score: float = Field(..., ge=0.0, le=1.0)
    decision_basis: str = Field(..., description="One-line human-readable reason; safe to surface.")
    detail: Optional[dict] = Field(
        None,
        description=(
            "Additional metadata for ops debugging; not surfaced to the end user. "
            "Never includes raw PII; only golden_record_id and stage information."
        ),
    )


class HealthResponse(BaseModel):
    status: str
    version: str
    dependencies: dict
