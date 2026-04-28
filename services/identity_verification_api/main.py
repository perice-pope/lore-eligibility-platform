"""FastAPI app for identity verification.

Run locally:
    uvicorn services.identity_verification_api.main:app --reload --port 8000

Smoke test:
    curl -X POST http://localhost:8000/v1/verify \\
        -H "Content-Type: application/json" \\
        -d '{"first_name":"Bob","last_name":"Smith","dob":"1962-04-12","zip":"90210","ssn_last4":"1234"}'

Endpoints:
    POST /v1/verify       — primary verification
    GET  /healthz         — liveness
    GET  /readyz          — readiness (depends on store)
    GET  /metrics         — Prometheus metrics

Production deployment: ECS Fargate behind ALB, two AZs, RDS Proxy in front of Aurora,
private VPC, no internet egress except to Skyflow + Bedrock VPC endpoints.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .models import HealthResponse, VerificationStatus, VerifyRequest, VerifyResponse
from .store import GoldenRecordStore

log = logging.getLogger("idv.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

VERSION = "0.1.0"

# ---- Metrics (Prometheus text format, pulled by Datadog) ------------------
_METRICS: dict[str, float] = {
    "idv_requests_total": 0.0,
    "idv_requests_verified": 0.0,
    "idv_requests_not_found": 0.0,
    "idv_requests_ambiguous": 0.0,
    "idv_requests_ineligible": 0.0,
    "idv_request_latency_ms_sum": 0.0,
    "idv_request_latency_ms_count": 0.0,
}


def _seed_path() -> Path | None:
    p = os.environ.get("LORE_IDV_SEED_FILE")
    if p and Path(p).exists():
        return Path(p)
    default = Path(__file__).resolve().parents[2] / "samples" / "golden_records_seed.json"
    return default if default.exists() else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("idv.api starting up — version=%s", VERSION)
    seed = _seed_path()
    app.state.store = GoldenRecordStore(backend="memory", seed_path=seed)
    log.info("golden record store initialized: %s", app.state.store.health())
    yield
    log.info("idv.api shutting down")


app = FastAPI(
    title="Lore Identity Verification API",
    version=VERSION,
    description="Source-of-truth identity verification for new account creation.",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_telemetry(request: Request, call_next):
    start = time.perf_counter()
    cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
    request.state.correlation_id = cid
    try:
        response = await call_next(request)
    except Exception:  # ensure we still emit a metric on errors
        log.exception("unhandled error correlation_id=%s", cid)
        _METRICS["idv_request_latency_ms_sum"] += (time.perf_counter() - start) * 1000
        _METRICS["idv_request_latency_ms_count"] += 1
        return JSONResponse(
            status_code=500,
            content={"detail": "internal_error", "correlation_id": cid},
        )
    response.headers["x-correlation-id"] = cid
    elapsed_ms = (time.perf_counter() - start) * 1000
    _METRICS["idv_request_latency_ms_sum"] += elapsed_ms
    _METRICS["idv_request_latency_ms_count"] += 1
    log.info(
        "method=%s path=%s status=%s ms=%.1f cid=%s",
        request.method, request.url.path, response.status_code, elapsed_ms, cid,
    )
    return response


@app.post("/v1/verify", response_model=VerifyResponse)
async def verify(request: Request, body: VerifyRequest):
    cid = request.state.correlation_id
    _METRICS["idv_requests_total"] += 1
    store: GoldenRecordStore = request.app.state.store

    # Stage 1: deterministic lookup
    matches = store.lookup(
        dob=body.dob.isoformat(),
        zip=body.zip,
        last_name=body.last_name,
        ssn_last4=body.ssn_last4,
    )

    if len(matches) == 1:
        gr = matches[0]
        if _is_ineligible(gr, today=date.today()):
            _METRICS["idv_requests_ineligible"] += 1
            return VerifyResponse(
                status=VerificationStatus.INELIGIBLE,
                correlation_id=cid,
                golden_record_id=gr.golden_record_id,
                partner_id=gr.partner_id,
                score=1.0,
                decision_basis="Member found, but coverage end date is in the past.",
            )
        _METRICS["idv_requests_verified"] += 1
        return VerifyResponse(
            status=VerificationStatus.VERIFIED,
            correlation_id=cid,
            golden_record_id=gr.golden_record_id,
            partner_id=gr.partner_id,
            score=1.0,
            decision_basis="Exact deterministic match on DOB, last name, ZIP, SSN-last-4.",
            detail={"stage": "deterministic"},
        )

    if len(matches) > 1:
        _METRICS["idv_requests_ambiguous"] += 1
        return VerifyResponse(
            status=VerificationStatus.AMBIGUOUS,
            correlation_id=cid,
            score=0.5,
            decision_basis=(
                f"Multiple ({len(matches)}) eligibility records match these inputs. "
                "Step-up KBA required."
            ),
            detail={"stage": "deterministic", "candidate_count": len(matches)},
        )

    # Stage 2: fuzzy fallback (zip3, year-of-dob, last-name prefix)
    fuzzy = store.fuzzy_search(
        last_name=body.last_name,
        dob=body.dob.isoformat(),
        zip3=body.zip[:3],
    )
    if len(fuzzy) == 1:
        # In production this would route through entity_resolution.matcher with LLM
        # adjudication. We surface as NEEDS_REVIEW: a real UX would offer KBA or
        # call the entity resolver inline (200ms budget vs sub-150ms SLO).
        _METRICS["idv_requests_ambiguous"] += 1
        return VerifyResponse(
            status=VerificationStatus.NEEDS_REVIEW,
            correlation_id=cid,
            golden_record_id=fuzzy[0].golden_record_id,
            partner_id=fuzzy[0].partner_id,
            score=0.7,
            decision_basis=(
                "Fuzzy match found one plausible record; routing to step-up "
                "verification (knowledge-based authentication)."
            ),
            detail={"stage": "fuzzy"},
        )

    _METRICS["idv_requests_not_found"] += 1
    return VerifyResponse(
        status=VerificationStatus.NOT_FOUND,
        correlation_id=cid,
        score=0.0,
        decision_basis=(
            "No eligibility record matches these inputs. "
            "Confirm spelling and partner code, or contact support."
        ),
        detail={"stage": "not_found"},
    )


def _is_ineligible(gr, *, today: date) -> bool:
    if not gr.effective_end_date:
        return False
    try:
        end = date.fromisoformat(gr.effective_end_date)
    except (TypeError, ValueError):
        return False
    return end < today


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


@app.get("/readyz", response_model=HealthResponse)
async def readyz(request: Request) -> HealthResponse:
    deps = {"golden_record_store": request.app.state.store.health()}
    return HealthResponse(status="ready", version=VERSION, dependencies=deps)


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    """Prometheus text format. Datadog scrapes this endpoint via OpenMetrics check."""
    lines = ["# HELP idv metrics from identity verification api"]
    for k, v in _METRICS.items():
        lines.append(f"# TYPE {k} counter")
        lines.append(f"{k} {v}")
    return "\n".join(lines) + "\n"
