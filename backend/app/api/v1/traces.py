"""
Claim trace retrieval endpoint.

GET /api/v1/claims/{claim_id}/trace

Design decision — claim-scoped, not trace-scoped:
claim_id is the identifier every caller already has (it's what the claim
submission and decision are keyed by). trace_id is an internal correlation
id generated per pipeline run that a frontend/ops user has no way to know
in advance. A separate GET /api/v1/traces/{trace_id} endpoint would be
redundant for Phase 1's use case — one claim submission produces one
trace — so it's deferred until there's a concrete need (e.g. claim
reprocessing producing multiple traces per claim).

Phase 1 has no claims table yet, so there is no claim-existence check:
an unknown claim_id returns 200 with an empty events list rather than 404.
Revisit once claims are persisted (Phase 2+).
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import TraceRepositoryDep
from app.domain.trace import TraceEvent

router = APIRouter()


class ClaimTraceResponse(BaseModel):
    claim_id: str
    count: int
    events: List[TraceEvent]


@router.get(
    "/claims/{claim_id}/trace",
    response_model=ClaimTraceResponse,
    summary="Get Claim Trace",
    description="Returns all trace events recorded for a claim, in chronological order.",
    tags=["Trace"],
)
async def get_claim_trace(
    claim_id: str,
    trace_repository: TraceRepositoryDep,
) -> ClaimTraceResponse:
    events = await trace_repository.list_by_claim_id(claim_id)
    return ClaimTraceResponse(claim_id=claim_id, count=len(events), events=events)
