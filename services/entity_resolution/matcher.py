"""Three-stage entity resolver.

Stage 1: deterministic — exact match on tokenized SSN, or (DOB + soundex + zip3) cluster.
Stage 2: embedding retrieval — top-K nearest candidates by cosine similarity.
Stage 3: LLM adjudication — Claude scores borderline pairs with chain-of-thought
         reasoning that's persisted for audit.

The matcher is intentionally **idempotent** and **deterministic in its decision boundary**.
Given the same input record and the same candidate index, the same decision is produced.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable

from .embeddings import Embedder, cosine_similarity
from .normalize import (
    blocking_key,
    blocking_keys,
    feature_string,
    normalize_dob,
    normalize_name,
    normalize_zip,
    soundex,
)

log = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = "anthropic.claude-sonnet-4-6"


class Decision(str, Enum):
    AUTO_MATCH = "AUTO_MATCH"  # confidence >= auto threshold; merge
    REVIEW = "REVIEW"          # 0.80–0.95; queue for human
    NO_MATCH = "NO_MATCH"      # below review threshold; new golden record


@dataclass
class MatchDecision:
    decision: Decision
    golden_record_id: str | None
    score: float
    reasoning: str
    candidates_considered: int
    stage: str  # which stage decided ("deterministic", "embedding+llm", "no_candidate")
    audit_payload: dict = field(default_factory=dict)


@dataclass
class CandidateRecord:
    """A record in the existing golden record store."""
    golden_record_id: str
    record: dict
    embedding: list[float] | None = None
    feature_str: str | None = None


@dataclass
class ResolverConfig:
    auto_match_threshold: float = 0.95
    review_threshold: float = 0.80
    candidate_top_k: int = 10
    embedding_min_cosine: float = 0.85
    llm_model_id: str = DEFAULT_LLM_MODEL
    llm_mode: str = "auto"  # auto|bedrock|local_heuristic
    region: str = "us-east-1"


class EntityResolver:
    """Three-stage entity resolver. Stateless — index is supplied on each resolve()."""

    def __init__(self, config: ResolverConfig | None = None, embedder: Embedder | None = None):
        self.config = config or ResolverConfig()
        self.embedder = embedder or Embedder()
        self._llm_client = None

    def resolve(self, incoming: dict, index: Iterable[CandidateRecord]) -> MatchDecision:
        candidates = list(index)

        # Stage 1: deterministic.
        det = self._deterministic_match(incoming, candidates)
        if det is not None:
            return det

        # Stage 2: embedding retrieval among blocking-key matches. A record may
        # emit multiple blocking keys (compound surnames); a candidate is "blocked
        # in" if it shares any key.
        incoming_keys = set(blocking_keys(incoming))
        blocked = [c for c in candidates if incoming_keys & set(blocking_keys(c.record))]
        if not blocked:
            return MatchDecision(
                decision=Decision.NO_MATCH,
                golden_record_id=None,
                score=0.0,
                reasoning="No candidates share a blocking key (DOB year + last-name soundex + zip3).",
                candidates_considered=0,
                stage="no_candidate",
            )

        incoming_feature = feature_string(incoming)
        incoming_emb = self.embedder.embed(incoming_feature).vector

        scored: list[tuple[CandidateRecord, float]] = []
        for cand in blocked:
            if cand.embedding is None:
                cand.feature_str = cand.feature_str or feature_string(cand.record)
                cand.embedding = self.embedder.embed(cand.feature_str).vector
            sim = cosine_similarity(incoming_emb, cand.embedding)
            scored.append((cand, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_k = scored[: self.config.candidate_top_k]
        viable = [(c, s) for c, s in top_k if s >= self.config.embedding_min_cosine]

        if not viable:
            return MatchDecision(
                decision=Decision.NO_MATCH,
                golden_record_id=None,
                score=top_k[0][1] if top_k else 0.0,
                reasoning=(
                    f"Top embedding similarity {top_k[0][1]:.3f} below threshold "
                    f"{self.config.embedding_min_cosine:.2f}." if top_k else "No candidates."
                ),
                candidates_considered=len(blocked),
                stage="embedding",
            )

        # Stage 3: LLM adjudication on the top candidate (and runner-up if close).
        best, best_sim = viable[0]
        verdict = self._adjudicate(incoming, best.record, best_sim, incoming_feature, best.feature_str or "")

        return MatchDecision(
            decision=self._classify(verdict["confidence"]),
            golden_record_id=best.golden_record_id if verdict["match"] else None,
            score=verdict["confidence"],
            reasoning=verdict["reasoning"],
            candidates_considered=len(blocked),
            stage="embedding+llm",
            audit_payload={
                "embedding_similarity": best_sim,
                "llm_verdict": verdict,
                "incoming_feature": incoming_feature,
                "candidate_feature": best.feature_str,
            },
        )

    # ---------- Stage 1 ----------
    def _deterministic_match(self, incoming: dict, candidates: list[CandidateRecord]) -> MatchDecision | None:
        # Exact tokenized-SSN match wins immediately.
        ssn_tok = incoming.get("ssn_token")
        if ssn_tok:
            for c in candidates:
                if c.record.get("ssn_token") == ssn_tok:
                    return MatchDecision(
                        decision=Decision.AUTO_MATCH,
                        golden_record_id=c.golden_record_id,
                        score=1.0,
                        reasoning="Exact tokenized SSN match.",
                        candidates_considered=len(candidates),
                        stage="deterministic",
                    )

        dob = normalize_dob(incoming.get("dob"))
        sx_l = soundex(incoming.get("last_name") or "")
        sx_f = soundex(incoming.get("first_name") or "")
        zipc = normalize_zip(incoming.get("zip"))
        ssn4 = incoming.get("ssn_last4") or ""

        # SSN-last-4 + DOB + ZIP — strong combined signal.
        if ssn4 and dob and zipc:
            for c in candidates:
                if (
                    c.record.get("ssn_last4") == ssn4
                    and normalize_dob(c.record.get("dob")) == dob
                    and normalize_zip(c.record.get("zip")) == zipc
                ):
                    return MatchDecision(
                        decision=Decision.AUTO_MATCH,
                        golden_record_id=c.golden_record_id,
                        score=0.98,
                        reasoning="Exact match on DOB + ZIP5 + SSN-last-4.",
                        candidates_considered=len(candidates),
                        stage="deterministic",
                    )

        # Exact match on dob + soundex(last) + soundex(first) + zip5
        if dob and sx_l and sx_f and zipc:
            for c in candidates:
                cdob = normalize_dob(c.record.get("dob"))
                if (
                    cdob == dob
                    and soundex(c.record.get("last_name") or "") == sx_l
                    and soundex(c.record.get("first_name") or "") == sx_f
                    and normalize_zip(c.record.get("zip")) == zipc
                ):
                    return MatchDecision(
                        decision=Decision.AUTO_MATCH,
                        golden_record_id=c.golden_record_id,
                        score=0.97,
                        reasoning="Exact match on DOB + soundex(first+last) + ZIP5.",
                        candidates_considered=len(candidates),
                        stage="deterministic",
                    )
        return None

    # ---------- Stage 3 ----------
    def _adjudicate(
        self, incoming: dict, candidate: dict, embedding_sim: float, incoming_feat: str, candidate_feat: str
    ) -> dict:
        if self.config.llm_mode == "local_heuristic":
            return self._adjudicate_local(incoming, candidate, embedding_sim)
        try:
            return self._adjudicate_bedrock(incoming, candidate, embedding_sim, incoming_feat, candidate_feat)
        except Exception as exc:
            log.warning("LLM adjudication failed (%s); falling back to local heuristic", exc)
            return self._adjudicate_local(incoming, candidate, embedding_sim)

    def _adjudicate_bedrock(
        self, incoming: dict, candidate: dict, embedding_sim: float, incoming_feat: str, candidate_feat: str
    ) -> dict:
        import boto3  # lazy

        if self._llm_client is None:
            self._llm_client = boto3.client("bedrock-runtime", region_name=self.config.region)

        prompt = self._build_adjudication_prompt(incoming_feat, candidate_feat, embedding_sim)
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 600,
            "system": (
                "You are a careful identity-matching adjudicator at a HIPAA-regulated health "
                "company. You decide whether two eligibility records refer to the same person. "
                "False positives (wrong merge) are catastrophic; false negatives are recoverable. "
                "When uncertain, do not match."
            ),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        resp = self._llm_client.invoke_model(
            modelId=self.config.llm_model_id, body=json.dumps(body),
            contentType="application/json", accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        text = payload["content"][0]["text"].strip()
        return _parse_verdict_json(text)

    @staticmethod
    def _build_adjudication_prompt(incoming_feat: str, candidate_feat: str, embedding_sim: float) -> str:
        return f"""Compare two eligibility records and decide if they describe the same person.

Embedding cosine similarity (0-1): {embedding_sim:.4f}

Incoming record:
{incoming_feat}

Candidate record (existing golden record):
{candidate_feat}

Consider:
- Name variations (Bob vs Robert, transposed letters, hyphenated marriage names).
- Date typos (single-digit transposition in DOB).
- Address moves (different street within same city/zip is plausible same-person).
- Missing fields are not evidence of mismatch.

Respond with JSON ONLY in this exact shape, no prose:
{{
  "match": true | false,
  "confidence": 0.0-1.0,
  "reasoning": "one or two sentences citing the specific fields that drove the decision"
}}
"""

    def _adjudicate_local(self, incoming: dict, candidate: dict, embedding_sim: float) -> dict:
        """Fallback heuristic adjudicator. Linear-combination scoring."""
        score = 0.0
        reasons = []
        # name match
        if normalize_name(incoming.get("first_name")) == normalize_name(candidate.get("first_name")):
            score += 0.25
            reasons.append("first name matches after normalization")
        elif soundex(incoming.get("first_name") or "") == soundex(candidate.get("first_name") or ""):
            score += 0.10
            reasons.append("first names sound alike")
        if normalize_name(incoming.get("last_name")) == normalize_name(candidate.get("last_name")):
            score += 0.25
            reasons.append("last name matches")
        # dob
        if normalize_dob(incoming.get("dob")) == normalize_dob(candidate.get("dob")):
            score += 0.25
            reasons.append("DOB matches exactly")
        # zip
        if normalize_zip(incoming.get("zip")) == normalize_zip(candidate.get("zip")):
            score += 0.10
            reasons.append("ZIP matches")
        # ssn last 4
        if (incoming.get("ssn_last4") and candidate.get("ssn_last4")
            and incoming.get("ssn_last4") == candidate.get("ssn_last4")):
            score += 0.15
            reasons.append("SSN last 4 matches")

        score = min(score + (embedding_sim - 0.85) * 0.5, 0.999)
        return {
            "match": score >= 0.80,
            "confidence": round(score, 3),
            "reasoning": "; ".join(reasons) or "No strong signals.",
        }

    def _classify(self, confidence: float) -> Decision:
        if confidence >= self.config.auto_match_threshold:
            return Decision.AUTO_MATCH
        if confidence >= self.config.review_threshold:
            return Decision.REVIEW
        return Decision.NO_MATCH


def _parse_verdict_json(text: str) -> dict:
    import re

    text = text.strip()
    fenced = re.match(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    parsed = json.loads(text)
    return {
        "match": bool(parsed.get("match", False)),
        "confidence": float(parsed.get("confidence", 0.0)),
        "reasoning": str(parsed.get("reasoning", ""))[:500],
    }
