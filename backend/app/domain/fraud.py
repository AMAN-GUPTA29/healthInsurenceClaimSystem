"""
Fraud analysis domain models — Phase 2C.

Answers "are there fraud/manual-review signals for this claim?" —
deterministically, from policy_terms.json's fraud_thresholds via
PolicyRepository. Never itself decides APPROVED/PARTIAL/REJECTED/
MANUAL_REVIEW (DecisionGenerationAgent's job, Phase 2D) — it only flags
signals for that later stage to weigh.

Rules (same as the rest of app/domain/):
- Pure Pydantic models. No database imports. No FastAPI imports. No AI SDK imports.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FraudRiskLevel(str, Enum):
    """Controlled outcome vocabulary for FraudAnalysisResult.risk_level."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FraudFlag(BaseModel):
    """One structured fraud/manual-review signal — e.g.
    code="SAME_DAY_CLAIMS_LIMIT_EXCEEDED", triggered by a deterministic
    threshold from policy_terms.json's fraud_thresholds. `code` is a
    free-form string, same convention as PolicyRuleFinding.rule."""

    code: str
    message: str
    evidence: Dict[str, Any] = Field(default_factory=dict)


class FraudAnalysisResult(BaseModel):
    """
    Output of FraudAnalysisAgent.run() — one per claim. Deterministic
    threshold checks (`deterministic_thresholds_triggered`) are always the
    authoritative signal; `ai_risk_score` (unset in this phase — a
    deliberate, deterministic-only choice) is kept as a distinct,
    clearly-separate field so a future AI-assisted signal can never be
    silently merged into or
    mistaken for a deterministic threshold result.
    """

    risk_level: FraudRiskLevel
    flags: List[FraudFlag] = Field(default_factory=list)
    deterministic_thresholds_triggered: List[str] = Field(default_factory=list)

    same_day_claim_count: int = 0
    monthly_claim_count: int = 0
    is_high_value: bool = False
    requires_manual_review: bool = False

    ai_risk_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Reserved for a future AI-assisted signal — never authoritative, always distinct "
        "from deterministic_thresholds_triggered. None in Phase 2C (deterministic-only).",
    )

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
