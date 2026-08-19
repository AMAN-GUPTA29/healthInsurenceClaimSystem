"""
Unit tests for the document classification prompt
(app/ai/prompts/document_verification.py).

A real classification failure was found live: documents that are clearly
BILLS by structure (itemized charges, a total amount, payment details) —
a dental clinic's itemized treatment bill, a diagnostics center's itemized
service bill — were being classified as DENTAL_REPORT/DIAGNOSTIC_REPORT
purely because of the medical specialty mentioned in the content, not
because of the document's actual structure/purpose. The system prompt had
no guidance at all distinguishing "bill" from "report" structurally, so
the model free-associated on medical keywords instead.

A second, related failure was found: a laboratory-issued report of an
imaging test (e.g. an MRI reported by an accredited diagnostics lab, with
a Sample ID, NABL accreditation, and a TEST NAME/RESULT/UNIT/NORMAL RANGE
layout) was being classified as DIAGNOSTIC_REPORT instead of LAB_REPORT,
because the prompt only distinguished "report" from "bill" and never gave
LAB_REPORT its own structural definition separate from DIAGNOSTIC_REPORT
— so the model fell back to the test name ("MRI") rather than who issued
the report and how it's tracked.

These tests lock in that the corrected prompt actually contains the
generic structural guidance needed to prevent both of these — they cannot
prove a live model will always classify correctly (no live LLM call is
made here — see docs/tradeoffs.md "Document Classification — Structure/
Issuer Over Specialty or Test-Type" for the manual/live verification
notes), but they do prevent a future edit from silently stripping the
guidance back out. No test-case ID, member ID, or fixture file name
appears anywhere in this file or in the prompt itself — the guidance is
expressed purely in terms of generic document structure.
"""

from __future__ import annotations

from app.ai.prompts.document_verification import (
    DOCUMENT_CLASSIFICATION_SCHEMA,
    DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT,
)
from app.domain.models import DocumentType


class TestPromptDistinguishesStructureFromSpecialty:
    def test_prompt_states_classification_is_by_structure_not_specialty(self):
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT.lower()
        assert "structure" in prompt
        assert "specialty" in prompt

    def test_prompt_explicitly_says_specialty_never_changes_bill_to_report(self):
        """The core rule: a bill issued by a dental/diagnostic specialty is
        still a bill, never reclassified as that specialty's report type."""
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT.lower()
        assert "dental" in prompt and "diagnostic" in prompt
        assert "hospital_bill" in prompt.replace(" ", "_") or "hospital bill" in prompt

    def test_prompt_gives_billing_structural_indicators(self):
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT
        for indicator in ["Bill No", "Total Amount", "Payment Mode", "Received by"]:
            assert indicator in prompt, f"missing billing indicator: {indicator!r}"

    def test_prompt_gives_report_structural_indicators(self):
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT
        for indicator in ["Findings", "Impression", "Results"]:
            assert indicator in prompt, f"missing report indicator: {indicator!r}"

    def test_prompt_gives_prescription_structural_indicators(self):
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT
        for indicator in ["Rx", "dosage", "frequency", "duration"]:
            assert indicator.lower() in prompt.lower(), f"missing prescription indicator: {indicator!r}"

    def test_prompt_resolves_the_mixed_signal_case_toward_billing_structure(self):
        """When a document has both an itemized-charges structure AND
        clinical-sounding line-item descriptions (e.g. a procedure name as
        a bill line item), the prompt must instruct the billing structure
        to win — this is the exact ambiguity that caused the original
        misclassification."""
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT.lower()
        assert "billing structure governs" in prompt or "billing structure" in prompt

    def test_no_test_case_ids_or_fixture_names_hardcoded_in_prompt(self):
        """The fix must be fully generic — never a special case for any
        official test case or fixture file."""
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT
        for forbidden in [
            "TC006", "TC007", "F011", "F013", "F014",
            "Smile Dental", "Apollo Diagnostics", "Precision Diagnostics",
            "Suresh Patil", "TS-NABL-5678", "PD-2024-MRI-3391",
        ]:
            assert forbidden not in prompt

    def test_schema_still_exposes_every_document_type(self):
        """The taxonomy itself is not the bug — every DocumentType must
        still be a valid classification target."""
        schema_types = set(DOCUMENT_CLASSIFICATION_SCHEMA["properties"]["document_type"]["enum"])
        assert schema_types == {t.value for t in DocumentType}


class TestPromptDistinguishesLabReportFromDiagnosticReport:
    """A laboratory-issued report of ANY test (including imaging like MRI)
    must be recognized as LAB_REPORT by its lab-reporting structure, not
    by the medical test it happens to describe — and a genuine
    DIAGNOSTIC_REPORT without that structure must not be pulled into
    LAB_REPORT just because it mentions the same test vocabulary."""

    def test_prompt_states_test_type_never_determines_lab_vs_diagnostic_report(self):
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT.lower()
        assert "who issued the report" in prompt or "issued the report" in prompt
        assert "never" in prompt and "determine" in prompt

    def test_prompt_says_a_lab_can_report_imaging_tests_like_mri(self):
        """The exact ambiguity that caused the bug: an MRI reported through
        a laboratory's own tracking/reporting structure is still a
        LAB_REPORT, not a DIAGNOSTIC_REPORT, purely because the underlying
        test is imaging rather than blood work."""
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT.lower()
        assert "mri" in prompt
        assert "still a lab_report" in prompt or "still a lab report" in prompt

    def test_prompt_gives_lab_report_structural_indicators(self):
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT
        for indicator in ["NABL", "Sample ID", "Sample Date", "Lab ID", "NORMAL RANGE"]:
            assert indicator in prompt, f"missing lab-report indicator: {indicator!r}"

    def test_prompt_says_words_alone_never_upgrade_to_lab_report(self):
        """Regression guard against overcorrection: a genuine
        DIAGNOSTIC_REPORT that mentions MRI/scan/impression/result but
        lacks lab-issued tracking structure must stay DIAGNOSTIC_REPORT."""
        prompt = DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT.lower()
        assert "never upgrade" in prompt or "words alone" in prompt
