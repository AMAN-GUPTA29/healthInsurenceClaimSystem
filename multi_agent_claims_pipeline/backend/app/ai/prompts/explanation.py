"""
Prompt + structured-output schema for ExplanationAgent (Phase 2D).

Text-only structured generation — no document images here, `AIProvider.
generate_structured()` (the same method the document-classification
fallback path in document_verification.py uses), never `analyze_document()`.
The LLM is given a compact, already-verified JSON summary of the decision
and its evidence and asked to write it up in plain language; it never sees
raw documents, never recalculates anything, and is explicitly instructed
not to invent, override, or contradict what it's given — see
EXPLANATION_SYSTEM_PROMPT below for the exact constraints.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from app.ai.schemas.ai_schemas import AIStructuredRequest, ContentRole, Message, TextContent

EXPLANATION_SYSTEM_PROMPT = """\
You are a claims-explanation assistant for a health insurance claims system \
in India. You will be given a JSON summary of a claim decision that has \
ALREADY been made by deterministic policy, financial, and fraud-analysis \
components. Your only job is to explain that decision clearly — never to \
make, change, or second-guess it.

Rules — follow all of these exactly:
1. Do NOT calculate, recalculate, or adjust any amount. Use only the \
approved_amount and financial figures given to you, exactly as given.
2. Do NOT invent policy rules, exclusions, waiting periods, or limits that \
are not present in the evidence you were given.
3. Do NOT override, contradict, or second-guess the `decision` field — your \
explanation must be consistent with it, not an alternative opinion.
4. Do NOT change or restate the approved amount as a different number.
5. Do NOT invent facts, dates, names, or document contents not present in \
the evidence.
6. Do NOT claim a document was verified, extracted, or readable if the \
evidence shows it failed or was flagged as a problem.
7. Do NOT claim fraud or suspicious activity was detected unless the fraud \
evidence explicitly says so — if fraud flags are empty, do not mention fraud.
8. Do NOT expose internal implementation details (component names, code, \
internal rule identifiers, confidence-scoring mechanics, trace ids) to the \
member-facing summary — plain language only. The operations summary may \
reference specific rule/finding names since it is for internal staff.
9. Base every sentence only on the evidence provided. If something is not \
present in the evidence, do not mention it.
10. Return structured output matching the given schema exactly."""

EXPLANATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "member_summary": {
            "type": "string",
            "description": "2-4 sentences, plain language, no jargon, addressed to the member.",
        },
        "operations_summary": {
            "type": "string",
            "description": "A more technical 2-5 sentence summary for an internal reviewer, may "
            "reference specific rule/finding names from the evidence.",
        },
        "key_reasons": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The main factors behind the decision, short phrases, from the evidence only.",
        },
        "deductions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Human-readable financial deductions (copay/discount/caps), from the "
            "financial evidence only — do not compute new figures.",
        },
        "policy_findings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The specific policy findings that affected this decision, from the "
            "policy evidence only.",
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Document or processing issues the member/ops should know about, from "
            "the evidence only — empty if none.",
        },
        "next_action": {
            "type": "string",
            "description": "What the member should do next, if anything, based only on the "
            "decision/rejection reasons given — empty string if nothing is needed.",
        },
    },
    "required": [
        "member_summary", "operations_summary", "key_reasons",
        "deductions", "policy_findings", "warnings", "next_action",
    ],
}


def build_explanation_request(evidence: Dict[str, Any]) -> AIStructuredRequest:
    """
    `evidence` is the already-serialised, already-safe JSON summary built
    by ExplanationAgent._build_evidence() — never raw documents, never the
    full Claim object, never an API key. See that method for exactly what
    it contains.
    """
    prompt = (
        "Here is the verified claim decision and its supporting evidence, as JSON:\n\n"
        f"{json.dumps(evidence, indent=2, default=str)}\n\n"
        "Write the member_summary and operations_summary, and fill in key_reasons, "
        "deductions, policy_findings, warnings, and next_action, using ONLY the "
        "information above. Follow every rule in the system prompt exactly."
    )
    return AIStructuredRequest(
        messages=[Message(role=ContentRole.USER, content=[TextContent(text=prompt)])],
        output_schema=EXPLANATION_SCHEMA,
        schema_name="claim_explanation",
        system_prompt=EXPLANATION_SYSTEM_PROMPT,
        metadata={"prompt_version": "2d.1"},
    )
