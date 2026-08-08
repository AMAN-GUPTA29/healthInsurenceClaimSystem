"""
Claim submission and retrieval endpoints.

POST /api/v1/claims             — submit a claim, run it through the
                                   Phase 2A pipeline (claim validation ->
                                   document verification -> cross-document
                                   validation), persist it, return it.
GET  /api/v1/claims/{claim_id}  — retrieve a previously submitted claim's
                                   current state.

Neither endpoint produces a final decision yet (APPROVED/PARTIAL/REJECTED/
MANUAL_REVIEW) — that's later phases. A claim that clears all three
Phase 2A stages comes back with status=PROCESSING and an explanatory
message; policy evaluation hasn't run.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import (
    ClaimRepositoryDep,
    ClaimsPipelineDep,
    DocumentInputAdapterDep,
)
from app.api.v1.schemas import ClaimResponse
from app.domain.models import Claim
from app.domain.trace import TraceContext
from app.repositories.trace_repository import TraceRepository
from app.services.document_input_adapter import ClaimSubmissionRequest
from app.tracing.service import TraceService

router = APIRouter()


@router.post(
    "/claims",
    response_model=ClaimResponse,
    status_code=201,
    summary="Submit a Claim",
    description=(
        "Submits a claim and runs it through claim validation, document "
        "verification, and cross-document validation. Stops early and "
        "returns a specific, actionable message if any stage blocks the "
        "claim or asks for a document to be re-uploaded."
    ),
    tags=["Claims"],
)
async def submit_claim(
    request: ClaimSubmissionRequest,
    document_input_adapter: DocumentInputAdapterDep,
    pipeline: ClaimsPipelineDep,
    claim_repository: ClaimRepositoryDep,
) -> ClaimResponse:
    submission, classifications = document_input_adapter.to_domain(request)
    claim = Claim(submission=submission)

    tracer = TraceService(TraceContext.new(claim_id=claim.claim_id), sink=TraceRepository())
    claim = await pipeline.run(claim, classifications=classifications, tracer=tracer)

    await claim_repository.save(claim)
    return ClaimResponse.from_claim(claim)


@router.get(
    "/claims/{claim_id}",
    response_model=ClaimResponse,
    summary="Get Claim",
    description="Returns a previously submitted claim's current processing state.",
    tags=["Claims"],
)
async def get_claim(claim_id: str, claim_repository: ClaimRepositoryDep) -> ClaimResponse:
    claim = await claim_repository.get_by_id(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")
    return ClaimResponse.from_claim(claim)
