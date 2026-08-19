"""
Claim submission and retrieval endpoints.

POST /api/v1/claims             — submit a claim with real uploaded
                                   documents (multipart/form-data: PDF,
                                   JPEG, or PNG), run it through the
                                   complete pipeline (claim validation ->
                                   document verification -> cross-document
                                   validation -> document extraction ->
                                   policy evaluation -> financial
                                   calculation -> fraud analysis ->
                                   decision generation -> explanation),
                                   persist it, return it.
GET  /api/v1/claims             — list previously submitted claims
                                   (newest first), for the Claim History
                                   UI (Phase 4). Lightweight rows only —
                                   see ClaimSummary; fetch a claim_id's
                                   full detail via the endpoint below.
GET  /api/v1/claims/{claim_id}  — retrieve a previously submitted claim's
                                   full current state.

Production path (this endpoint):
    multipart upload -> DocumentInputAdapter.from_uploads() -> DocumentStorage
        -> ClaimsPipeline -> DocumentVerificationAgent -> AIProvider.analyze_document()

Evaluation path (test_cases.json fixtures, used by app/evaluation/runner.py,
never over HTTP): DocumentInputAdapter.to_domain() -> the same ClaimsPipeline.
Both converge on the same internal (ClaimSubmission, classifications) shape
before either ever reaches an agent — see DocumentInputAdapter's docstring.

A claim that clears every early-stop check reaches a final decision
(APPROVED/PARTIAL/REJECTED/MANUAL_REVIEW) via Stage 8 (Decision
Generation) — see `claim.decision` on the response. A claim that stops
early (wrong/missing/unreadable document, patient mismatch, unknown
member) comes back with `status=BLOCKED`/`DOCUMENTS_PENDING` and
`decision=null`, per its own specific `user_message` — see
ARCHITECTURE.docx "3. How a Claim Flows Through the System".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.api.deps import (
    ClaimRepositoryDep,
    ClaimsPipelineDep,
    DocumentInputAdapterDep,
    DocumentStorageDep,
)
from app.api.v1.schemas import ClaimListResponse, ClaimResponse
from app.domain.models import Claim, ClaimCategory, generate_claim_id
from app.domain.trace import TraceContext
from app.repositories.trace_repository import TraceRepository
from app.tracing.service import TraceService

router = APIRouter()


@router.post(
    "/claims",
    response_model=ClaimResponse,
    status_code=201,
    summary="Submit a Claim",
    description=(
        "Submits a claim with real uploaded documents (PDF/JPEG/PNG, "
        "multipart/form-data) and runs it through claim validation, "
        "document verification, and cross-document validation. Stops "
        "early and returns a specific, actionable message if any stage "
        "blocks the claim or asks for a document to be re-uploaded."
    ),
    tags=["Claims"],
)
async def submit_claim(
    document_input_adapter: DocumentInputAdapterDep,
    pipeline: ClaimsPipelineDep,
    claim_repository: ClaimRepositoryDep,
    document_storage: DocumentStorageDep,
    member_id: str = Form(...),
    policy_id: str = Form(...),
    claim_category: str = Form(...),
    treatment_date: date = Form(...),
    claimed_amount: str = Form(...),
    hospital_name: Optional[str] = Form(None),
    ytd_claims_amount: str = Form("0"),
    simulate_component_failure: bool = Form(False),
    documents: List[UploadFile] = File(...),
) -> ClaimResponse:
    try:
        category = ClaimCategory(claim_category)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid claim_category: '{claim_category}'.")

    try:
        amount = Decimal(claimed_amount)
        ytd_amount = Decimal(ytd_claims_amount)
    except InvalidOperation:
        raise HTTPException(status_code=422, detail="claimed_amount/ytd_claims_amount must be numeric.")

    claim_id = generate_claim_id()

    # Validation/storage errors (unsupported type, empty, too large) raise
    # DocumentError subclasses — main.py's ClaimsSystemError handler turns
    # these into a structured 422, never a bare 500.
    submission = await document_input_adapter.from_uploads(
        member_id=member_id,
        policy_id=policy_id,
        claim_category=category,
        treatment_date=treatment_date,
        claimed_amount=amount,
        hospital_name=hospital_name,
        ytd_claims_amount=ytd_amount,
        uploads=documents,
        claim_id=claim_id,
        storage=document_storage,
        simulate_component_failure=simulate_component_failure,
    )
    claim = Claim(claim_id=claim_id, submission=submission)

    tracer = TraceService(TraceContext.new(claim_id=claim.claim_id), sink=TraceRepository())
    claim = await pipeline.run(claim, tracer=tracer)

    await claim_repository.save(claim)
    return ClaimResponse.from_claim(claim)


@router.get(
    "/claims",
    response_model=ClaimListResponse,
    summary="List Claims",
    description=(
        "Returns previously submitted claims, newest first, for the Claim "
        "History UI. Lightweight rows only (no documents/trace/policy "
        "detail) — fetch GET /claims/{claim_id} for a claim's full state."
    ),
    tags=["Claims"],
)
async def list_claims(
    claim_repository: ClaimRepositoryDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> ClaimListResponse:
    claims = await claim_repository.list_all(limit=limit)
    return ClaimListResponse(claims=claims, count=len(claims))


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
