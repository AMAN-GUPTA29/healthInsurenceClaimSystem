"""
CrossDocumentValidationAgent — the third pipeline stage.

Checks consistency *across* already-classified documents. Phase 2A only
implements patient-identity consistency (the check TC003 needs) —
deliberately not a general validation engine yet.

Purely deterministic: it compares patient names DocumentVerificationAgent
(or the fixture adapter) already extracted. It never calls AI itself and
never re-reads a document — if two documents' extracted names don't match,
that's the signal, regardless of how the name was obtained.
"""

from __future__ import annotations

import re
from typing import List

from app.agents.base_agent import BaseAgent
from app.domain.verification import (
    CrossDocumentValidationResult,
    CrossDocumentValidationStatus,
    DocumentClassification,
)


def _normalize_name(name: str) -> str:
    """Case/whitespace-insensitive comparison key. Not fuzzy matching —
    see docs/component-contracts.md for why that's a documented limitation."""
    return re.sub(r"\s+", " ", name.strip()).lower()


def _label(document_type) -> str:
    return document_type.value.replace("_", " ").title()


class CrossDocumentValidationAgent(BaseAgent):
    def __init__(self, *, agent_name: str | None = None) -> None:
        super().__init__(ai_provider=None, agent_name=agent_name)

    async def run(self, classifications: List[DocumentClassification]) -> CrossDocumentValidationResult:
        named = [c for c in classifications if c.patient_name]

        if len(named) < 2:
            return CrossDocumentValidationResult(
                status=CrossDocumentValidationStatus.PASS,
                patient_names={_label(c.document_type): c.patient_name for c in named},
                user_message="Not enough documents with an identifiable patient name to cross-check.",
            )

        distinct = {_normalize_name(c.patient_name) for c in named}
        patient_names = {_label(c.document_type): c.patient_name for c in named}

        if len(distinct) > 1:
            detail = "; ".join(f"{label}: {name}" for label, name in patient_names.items())
            message = (
                "The documents appear to belong to different patients. "
                f"{detail}. Please upload documents belonging to the same patient."
            )
            confidences = [c.confidence for c in named if c.confidence is not None]
            return CrossDocumentValidationResult(
                status=CrossDocumentValidationStatus.BLOCKED,
                patient_names=patient_names,
                user_message=message,
                confidence=min(confidences) if confidences else None,
            )

        return CrossDocumentValidationResult(
            status=CrossDocumentValidationStatus.PASS,
            patient_names=patient_names,
            user_message="All documents belong to the same patient.",
        )
