"""
Generates the full synthetic test-document set for manual end-to-end UI
testing of the Plum Health Insurance Claims Processing System.

ALL content here is fictitious test data — see the footer on every
generated PDF ("SYNTHETIC TEST DOCUMENT — NOT A REAL MEDICAL RECORD").
This script does not import or touch anything under
multi_agent_claims_pipeline/ — it is pure document-generation tooling,
run from its own isolated venv (see test_documents/README.md
"Regenerating these documents").

Run from the repo root or from test_documents/:
    python generate_test_documents.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from multi_agent_claims_pipeline.test_documents.lib_docbuilder import (  # noqa: E402
    MANIFEST,
    ManifestEntry,
    apply_correction,
    build_dental_bill,
    build_hospital_bill,
    build_hospital_bill_multipage,
    build_lab_report,
    build_prescription,
    crop_partial,
    degrade_blur,
    degrade_low_contrast,
    degrade_rotate,
    degrade_shadow_noise,
    draw_duplicate_watermark,
    draw_stamp,
    embed_image_pdf,
    phone_photo_effect,
    record,
    render_hospital_bill_image,
    render_multilingual_prescription_image,
    render_pharmacy_bill_image,
    render_prescription_image,
)

OUT = Path(__file__).resolve().parent


# ═════════════════════════════════════════════════════════════════════════
#  TC001 — WRONG DOCUMENT UPLOADED
# ═════════════════════════════════════════════════════════════════════════

def gen_tc001() -> None:
    d = OUT / "TC001_wrong_document"

    build_prescription(
        d / "F001_dr_sharma_prescription.pdf",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015",
        doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre", clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="01-Nov-2024",
        diagnosis="Viral Fever", medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days",
                                             "Tab Vitamin C 500mg -- 0-0-1 x 7 days"],
        tests=["CBC", "Dengue NS1"],
    )
    record(ManifestEntry(
        test_case="TC001", file_id="F001", filename="F001_dr_sharma_prescription.pdf",
        document_type="PRESCRIPTION", patient="Rajesh Kumar",
        purpose="First of two PRESCRIPTIONs uploaded — HOSPITAL_BILL is never provided.",
        important_fields="Doctor: Dr. Arun Sharma; Diagnosis: Viral Fever; Date: 01-Nov-2024",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
        phase2a_note="Document verification detects PRESCRIPTION present, HOSPITAL_BILL missing.",
        phase2c_policy_note="Not reached — pipeline stops at DOCUMENT_VERIFICATION.",
        phase2c_financial_note="Not reached.", phase2c_fraud_note="Not reached.",
    ))

    build_prescription(
        d / "F002_another_prescription.pdf",
        doctor_name="Meera Krishnan", doctor_reg="KA/78901/2019",
        doctor_specialization="MBBS (General Physician)",
        clinic_name="MediCare Clinic", clinic_address="45 Residency Road, Bengaluru - 560025",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="01-Nov-2024",
        diagnosis="Viral Fever -- second opinion",
        medicines=["Tab Dolo 650 -- 1-1-1 x 5 days", "Tab Allegra 120mg -- 0-0-1 x 5 days"],
        notes="Second prescription — this claim has NO hospital bill (intentional test case).",
    )
    record(ManifestEntry(
        test_case="TC001", file_id="F002", filename="F002_another_prescription.pdf",
        document_type="PRESCRIPTION", patient="Rajesh Kumar",
        purpose="Second PRESCRIPTION (different doctor/content) — unmistakably still a "
                "prescription, never a hospital bill, so the required HOSPITAL_BILL stays missing.",
        important_fields="Doctor: Dr. Meera Krishnan (different from F001); Date: 01-Nov-2024",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
        phase2a_note="Pipeline stops at DOCUMENT_VERIFICATION: BLOCKED — missing HOSPITAL_BILL, "
                     "message names the two uploaded PRESCRIPTIONs and the missing type.",
        phase2c_policy_note="Not reached.", phase2c_financial_note="Not reached.", phase2c_fraud_note="Not reached.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC002 — UNREADABLE PHARMACY BILL
# ═════════════════════════════════════════════════════════════════════════

def gen_tc002() -> None:
    d = OUT / "TC002_unreadable_document"

    build_prescription(
        d / "F003_prescription.pdf",
        doctor_name="Meera Nair", doctor_reg="KA/33201/2017",
        doctor_specialization="MBBS (General Physician)",
        clinic_name="Apollo Clinic", clinic_address="Jayanagar, Bengaluru - 560011",
        patient_name="Sneha Reddy", patient_age=32, patient_gender="F", date="25-Oct-2024",
        diagnosis="Acute Gastritis", medicines=["Tab Pantoprazole 40mg -- 1-0-0 x 14 days"],
    )
    record(ManifestEntry(
        test_case="TC002", file_id="F003", filename="F003_prescription.pdf",
        document_type="PRESCRIPTION", patient="Sneha Reddy",
        purpose="Clean, readable prescription — establishes the PHARMACY_BILL as the only "
                "document with a quality problem.",
        important_fields="Doctor: Dr. Meera Nair; Diagnosis: Acute Gastritis; Medicine: Pantoprazole 40mg",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
        phase2a_note="Detected normally.",
    ))

    img = render_pharmacy_bill_image(
        patient_name="Sneha Reddy", doctor_name="Dr. Meera Nair",
        medicines=[("Pantoprazole 40mg", "PAN220", "09/26", 14, 8.00)],
        date="25-Oct-2024",
    )
    # Intentionally unreadable: severe blur + low contrast + slight
    # rotation + phone-camera shadow/noise, in that order.
    img = degrade_rotate(img, degrees=3.5)
    img = degrade_shadow_noise(img)
    img = degrade_low_contrast(img, factor=0.4)
    img = degrade_blur(img, radius=9)
    embed_image_pdf(d / "F004_blurry_pharmacy_bill.pdf", img,
                     caption="(pharmacy bill — image intentionally degraded for quality-detection testing)")
    record(ManifestEntry(
        test_case="TC002", file_id="F004", filename="F004_blurry_pharmacy_bill.pdf",
        document_type="PHARMACY_BILL", patient="Sneha Reddy",
        purpose="Deliberately unreadable (severe Gaussian blur + low contrast + rotation + "
                "shadow/noise) — content technically present but not legible.",
        important_fields="Amount: Rs. 800 (present in the image but not reliably readable)",
        expected_classification="PHARMACY_BILL", expected_quality="UNREADABLE (or equivalent)",
        phase2a_note="Document verification flags quality=UNREADABLE; status=DOCUMENTS_PENDING "
                     "(re-upload requested), claim NOT rejected outright.",
        phase2c_policy_note="Not reached while pending re-upload.",
        phase2c_financial_note="Not reached.", phase2c_fraud_note="Not reached.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC003 — DOCUMENTS BELONG TO DIFFERENT PATIENTS
# ═════════════════════════════════════════════════════════════════════════

def gen_tc003() -> None:
    d = OUT / "TC003_different_patients"

    build_prescription(
        d / "F005_prescription_rajesh.pdf",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015",
        doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre", clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="01-Nov-2024",
        diagnosis="Viral Fever", medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days",
                                             "Tab Vitamin C 500mg -- 0-0-1 x 7 days"],
        tests=["CBC"],
    )
    record(ManifestEntry(
        test_case="TC003", file_id="F005", filename="F005_prescription_rajesh.pdf",
        document_type="PRESCRIPTION", patient="Rajesh Kumar",
        purpose="Patient name clearly Rajesh Kumar — the claimed member.",
        important_fields="Patient: Rajesh Kumar; Doctor: Dr. Arun Sharma; Diagnosis: Viral Fever",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
    ))

    build_hospital_bill(
        d / "F006_bill_arjun.pdf",
        hospital_name="City Clinic, Bengaluru", hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX", bill_no="CMC/2024/08321", date="01-Nov-2024",
        patient_name="Arjun Mehta", patient_age=35, patient_gender="M",
        referring_doctor="Dr. Prashant Gupta",
        line_items=[("Consultation Fee (OPD)", 1000.0), ("CBC Test", 200.0), ("Dengue NS1 Antigen Test", 300.0)],
    )
    record(ManifestEntry(
        test_case="TC003", file_id="F006", filename="F006_bill_arjun.pdf",
        document_type="HOSPITAL_BILL", patient="Arjun Mehta",
        purpose="Patient name clearly Arjun Mehta — deliberately NOT Rajesh Kumar, to trigger "
                "document ↔ document patient mismatch.",
        important_fields="Patient: Arjun Mehta; Hospital: City Clinic, Bengaluru; Total: Rs. 1500",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2a_note="CROSS_DOCUMENT_VALIDATION: BLOCKED — message explicitly names both "
                     "'Rajesh Kumar' and 'Arjun Mehta'.",
        phase2c_policy_note="Not reached.", phase2c_financial_note="Not reached.", phase2c_fraud_note="Not reached.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC004 — CLEAN CONSULTATION (full pipeline pass)
# ═════════════════════════════════════════════════════════════════════════

def gen_tc004() -> None:
    d = OUT / "TC004_clean_consultation"

    build_prescription(
        d / "F007_prescription_rajesh.pdf",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015",
        doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre", clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="01-Nov-2024",
        diagnosis="Viral Fever", medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days",
                                             "Tab Vitamin C 500mg -- 0-0-1 x 7 days"],
        tests=["CBC", "Dengue NS1"],
    )
    record(ManifestEntry(
        test_case="TC004", file_id="F007", filename="F007_prescription_rajesh.pdf",
        document_type="PRESCRIPTION", patient="Rajesh Kumar",
        purpose="Clean prescription for a fully-approvable consultation claim.",
        important_fields="Doctor: Dr. Arun Sharma (KA/45678/2015); Diagnosis: Viral Fever; "
                          "Medicines: Paracetamol 650mg, Vitamin C 500mg; Investigations: CBC, Dengue NS1",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
        phase2b_note="Extracted: patient, doctor+reg, diagnosis, both medicines, both investigations.",
    ))

    build_hospital_bill(
        d / "F008_hospital_bill_rajesh.pdf",
        hospital_name="City Clinic, Bengaluru", hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX", bill_no="CMC/2024/08322", date="01-Nov-2024",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", referring_doctor="Dr. Arun Sharma",
        line_items=[("Consultation Fee", 1000.0), ("CBC Test", 300.0), ("Dengue NS1 Antigen Test", 200.0)],
    )
    record(ManifestEntry(
        test_case="TC004", file_id="F008", filename="F008_hospital_bill_rajesh.pdf",
        document_type="HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="Matching hospital bill — total exactly Rs. 1500, no policy issues.",
        important_fields="Line items: Consultation Fee 1000, CBC 300, Dengue NS1 200; Total: Rs. 1500",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2b_note="Extracted: hospital name, 3 line items, total Rs. 1500.00.",
        phase2c_policy_note="Covered=True; no waiting period/exclusion; sub-limit (Rs. 2000) and "
                             "per-claim limit (Rs. 5000) not exceeded.",
        phase2c_financial_note="Rs. 1500 -> no network discount (not a network hospital) -> "
                                "10% consultation copay (-Rs. 150) -> payable = Rs. 1350.",
        phase2c_fraud_note="LOW risk, no flags.",
        expected_final_decision="APPROVED, Rs. 1350 payable (per assignment's official expectation; "
                                 "final decision synthesis is Phase 2D, not yet implemented).",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC005 — DIABETES WAITING PERIOD
# ═════════════════════════════════════════════════════════════════════════

def gen_tc005() -> None:
    d = OUT / "TC005_diabetes_waiting_period"

    build_prescription(
        d / "F009_prescription_vikram_diabetes.pdf",
        doctor_name="Sunil Mehta", doctor_reg="GJ/56789/2014",
        doctor_specialization="MBBS, MD (Endocrinology)",
        clinic_name="Mehta Endocrine Clinic", clinic_address="Navrangpura, Ahmedabad - 380009",
        patient_name="Vikram Joshi", patient_age=45, patient_gender="M", date="15-Oct-2024",
        diagnosis="Type 2 Diabetes Mellitus (newly diagnosed)",
        medicines=["Tab Metformin 500mg -- 0-0-1 x 30 days", "Tab Glimepiride 1mg -- 1-0-0 x 30 days"],
        notes="Lifestyle modification advised. Low glycemic index diet.",
    )
    record(ManifestEntry(
        test_case="TC005", file_id="F009", filename="F009_prescription_vikram_diabetes.pdf",
        document_type="PRESCRIPTION", patient="Vikram Joshi",
        purpose="Diagnosis is specifically diabetes — member EMP005 joined 2024-09-01, treatment "
                "date 2024-10-15 is only 44 days later (< 90-day diabetes-specific waiting period).",
        important_fields="Doctor: Dr. Sunil Mehta (GJ/56789/2014); Diagnosis: Type 2 Diabetes Mellitus; "
                          "Medicines: Metformin 500mg, Glimepiride 1mg",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
    ))

    build_hospital_bill(
        d / "F010_hospital_bill_vikram_diabetes.pdf",
        hospital_name="Mehta Endocrine Clinic", hospital_address="Navrangpura, Ahmedabad - 380009",
        gstin="24AAACM1234A1ZX", bill_no="MEC/2024/01234", date="15-Oct-2024",
        patient_name="Vikram Joshi", patient_age=45, patient_gender="M", referring_doctor="Dr. Sunil Mehta",
        line_items=[("Consultation Fee", 1000.0), ("HbA1c Blood Test", 800.0),
                    ("Fasting / PP Blood Sugar", 400.0), ("Lipid Profile", 600.0), ("Medicines", 200.0)],
    )
    record(ManifestEntry(
        test_case="TC005", file_id="F010", filename="F010_hospital_bill_vikram_diabetes.pdf",
        document_type="HOSPITAL_BILL", patient="Vikram Joshi",
        purpose="Total exactly Rs. 3000 as specified.",
        important_fields="Total: Rs. 3000 (Consultation 1000 + HbA1c 800 + Blood Sugar 400 + Lipid 600 + Meds 200)",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2c_policy_note="WAITING_PERIOD_DIABETES: FAILED — diabetes-specific 90-day waiting "
                             "period (policy_terms.json waiting_periods.specific_conditions.diabetes) "
                             "not yet elapsed since join_date 2024-09-01.",
        phase2c_financial_note="Sub-limit (Rs. 2000) applies regardless; payable computed but the "
                                "waiting-period finding is the blocking issue for the decision stage.",
        phase2c_fraud_note="LOW risk, no flags.",
        expected_final_decision="REJECTED — explanation should state when the member becomes "
                                 "eligible (90 days after 2024-09-01, i.e. ~2024-11-30).",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC006 — DENTAL PARTIAL / COSMETIC EXCLUSION
# ═════════════════════════════════════════════════════════════════════════

def gen_tc006() -> None:
    d = OUT / "TC006_dental_partial"

    build_dental_bill(
        d / "F011_dental_bill_priya.pdf",
        clinic_name="Smile Dental Clinic", address="HSR Layout, Bengaluru - 560102",
        bill_no="SDC/2024/04521", date="15-Oct-2024",
        patient_name="Priya Singh", patient_age=34, patient_gender="F",
        dentist_name="Kavitha Rao", dentist_reg="KA/44123/2016",
        line_items=[("Root Canal Treatment (Molar #36)", 8000.0, True),
                    ("Teeth Whitening -- Laser (Cosmetic)", 4000.0, False)],
    )
    record(ManifestEntry(
        test_case="TC006", file_id="F011", filename="F011_dental_bill_priya.pdf",
        document_type="HOSPITAL_BILL", patient="Priya Singh",
        purpose="Two clearly separated line items: one medically necessary (covered), one cosmetic "
                "(policy-excluded) — tests PolicyEngine's per-line-item exclusion matching.",
        important_fields="Root Canal Treatment: Rs. 8000 (covered); Teeth Whitening: Rs. 4000 "
                          "(excluded — cosmetic); Total: Rs. 12000",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2b_note="Both line items extracted with description + amount.",
        phase2c_policy_note="Root Canal Treatment -> covered; Teeth Whitening -> EXCLUSION_DENTAL "
                             "(cosmetic procedure) FAILED for that line item only.",
        phase2c_financial_note="Eligible base = Rs. 8000 (only the non-excluded line item) — "
                                "per-claim limit (Rs. 5000) then applies as a real cap; see "
                                "docs/tradeoffs.md for the disclosed TC006 discrepancy against the "
                                "assignment's own worked example (which implies Rs. 8000 survives "
                                "uncapped to payable — this implementation caps it at Rs. 5000).",
        phase2c_fraud_note="LOW risk, no flags.",
        expected_final_decision="PARTIAL — official assignment expected approved amount Rs. 8000 "
                                 "(root canal only); this implementation's FinancialCalculationService "
                                 "additionally applies the Rs. 5000 per-claim limit — a disclosed, "
                                 "deliberate discrepancy, not a bug.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC007 — MRI WITHOUT PRE-AUTHORIZATION
# ═════════════════════════════════════════════════════════════════════════

def gen_tc007() -> None:
    d = OUT / "TC007_mri_pre_auth"

    build_prescription(
        d / "F012_mri_prescription.pdf",
        doctor_name="Venkat Rao", doctor_reg="AP/67890/2017",
        doctor_specialization="MBBS, MS (Orthopaedics)",
        clinic_name="Orthopedic Specialty Centre", clinic_address="Banjara Hills, Hyderabad - 500034",
        patient_name="Suresh Patil", patient_age=49, patient_gender="M", date="02-Nov-2024",
        diagnosis="Suspected Lumbar Disc Herniation (L4-L5) with radiculopathy",
        medicines=["Tab Etoricoxib 90mg -- 0-0-1 x 7 days", "Tab Pregabalin 75mg -- 0-0-1 x 7 days"],
        tests=["MRI Lumbar Spine (with contrast)"],
        notes="MRI required for surgical planning. Pre-authorization NOT obtained.",
    )
    record(ManifestEntry(
        test_case="TC007", file_id="F012", filename="F012_mri_prescription.pdf",
        document_type="PRESCRIPTION", patient="Suresh Patil",
        purpose="Diagnosis is 'Herniation' (a spinal-disc condition), not standalone 'Hernia' — "
                "regression check that PolicyEngine's word-boundary matching does not false-positive "
                "the unrelated hernia waiting period (see EXTRA09 for a dedicated isolated repro).",
        important_fields="Doctor: Dr. Venkat Rao (AP/67890/2017); Diagnosis: Suspected Lumbar Disc "
                          "Herniation; Test ordered: MRI Lumbar Spine",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
    ))

    build_lab_report(
        d / "F013_mri_report.pdf",
        lab_name="Precision Diagnostics Pvt Ltd", lab_id="TS-NABL-5678", nabl=True,
        address="Banjara Hills, Hyderabad | Ph: 040-XXXXXXXX",
        patient_name="Suresh Patil", patient_age=49, patient_gender="M", referring_doctor="Dr. Venkat Rao",
        sample_date="02-Nov-2024", report_date="02-Nov-2024", sample_id="PD-2024-MRI-3391",
        tests=[("MRI Lumbar Spine", "L4-L5 disc herniation with nerve root compression", "--", "Normal disc height"),
               ("Impression", "Grade 2 disc prolapse at L4-L5 level", "--", "--")],
        remarks="Clinical correlation advised. Surgical consultation recommended.",
        pathologist_name="Dr. Rajesh Gowda", pathologist_reg="TS/90123/2014",
    )
    record(ManifestEntry(
        test_case="TC007", file_id="F013", filename="F013_mri_report.pdf",
        document_type="LAB_REPORT", patient="Suresh Patil",
        purpose="MRI Lumbar Spine report — no pre-authorization letter accompanies this claim "
                "(intentionally omitted).",
        important_fields="Test: MRI Lumbar Spine; Patient: Suresh Patil",
        expected_classification="LAB_REPORT / DIAGNOSTIC_REPORT", expected_quality="GOOD",
    ))

    build_hospital_bill(
        d / "F014_mri_hospital_bill.pdf",
        hospital_name="Apollo Diagnostics", hospital_address="Banjara Hills, Hyderabad - 500034",
        gstin="36AAACC9876B1ZX", bill_no="APD/2024/11102", date="02-Nov-2024",
        patient_name="Suresh Patil", patient_age=49, patient_gender="M", referring_doctor="Dr. Venkat Rao",
        line_items=[("MRI Lumbar Spine (with contrast)", 15000.0)],
    )
    record(ManifestEntry(
        test_case="TC007", file_id="F014", filename="F014_mri_hospital_bill.pdf",
        document_type="HOSPITAL_BILL", patient="Suresh Patil",
        purpose="Total Rs. 15,000 — above the diagnostic category's pre_auth_threshold (Rs. 10,000) "
                "and MRI is on high_value_tests_requiring_pre_auth; NO pre-auth letter uploaded.",
        important_fields="Line item: MRI Lumbar Spine (with contrast), Rs. 15000; Total: Rs. 15000",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2c_policy_note="PRE_AUTHORIZATION: FAILED (threshold exceeded, MRI in high-value test "
                             "list, no PRE_AUTH_LETTER document present). WAITING_PERIOD_HERNIA must "
                             "NOT fire (word-boundary regression check — 'Herniation' != 'Hernia').",
        phase2c_financial_note="Sub-limit (diagnostic, Rs. 10000) and per-claim limit (Rs. 5000) both "
                                "apply as caps regardless of the pre-auth failure.",
        phase2c_fraud_note="LOW risk, no flags (below high-value fraud threshold of Rs. 25000).",
        expected_final_decision="REJECTED — explanation should state pre-authorization was required "
                                 "and missing.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC008 — PER-CLAIM LIMIT EXCEEDED
# ═════════════════════════════════════════════════════════════════════════

def gen_tc008() -> None:
    d = OUT / "TC008_per_claim_limit"

    build_prescription(
        d / "F015_prescription_amit.pdf",
        doctor_name="R. Gupta", doctor_reg="DL/34567/2016",
        doctor_specialization="MBBS (General Physician)",
        clinic_name="Capital Medical Clinic", clinic_address="Connaught Place, New Delhi - 110001",
        patient_name="Amit Verma", patient_age=36, patient_gender="M", date="20-Oct-2024",
        diagnosis="Acute Gastroenteritis with dehydration",
        medicines=["Antibiotics -- 1-0-1 x 5 days", "Probiotics -- 0-0-1 x 7 days", "ORS 1L -- Q6H x 3 days"],
    )
    record(ManifestEntry(
        test_case="TC008", file_id="F015", filename="F015_prescription_amit.pdf",
        document_type="PRESCRIPTION", patient="Amit Verma",
        purpose="Supports a consultation claim whose bill total (Rs. 7500) exceeds the policy's "
                "global per-claim limit (Rs. 5000).",
        important_fields="Doctor: Dr. R. Gupta (DL/34567/2016); Diagnosis: Gastroenteritis; "
                          "Medicines: Antibiotics, Probiotics, ORS",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
    ))

    build_hospital_bill(
        d / "F016_hospital_bill_amit.pdf",
        hospital_name="Capital Medical Clinic", hospital_address="Connaught Place, New Delhi - 110001",
        gstin="07AAACC7654D1ZX", bill_no="CMC-DL/2024/05511", date="20-Oct-2024",
        patient_name="Amit Verma", patient_age=36, patient_gender="M", referring_doctor="Dr. R. Gupta",
        line_items=[("Consultation Fee", 2000.0), ("Medicines", 5500.0)],
    )
    record(ManifestEntry(
        test_case="TC008", file_id="F016", filename="F016_hospital_bill_amit.pdf",
        document_type="HOSPITAL_BILL", patient="Amit Verma",
        purpose="Total exactly Rs. 7500 (2000 + 5500), above the global per-claim limit of Rs. 5000.",
        important_fields="Line items: Consultation Fee 2000, Medicines 5500; Total: Rs. 7500",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2c_policy_note="PER_CLAIM_LIMIT: FAILED (Rs. 7500 > Rs. 5000 global per-claim limit).",
        phase2c_financial_note="Payable capped at Rs. 5000 by the per-claim-limit cap (before copay).",
        phase2c_fraud_note="LOW risk, no flags.",
        expected_final_decision="REJECTED, per the official test case.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC009 — SAME-DAY FRAUD SIGNAL
# ═════════════════════════════════════════════════════════════════════════

def gen_tc009() -> None:
    d = OUT / "TC009_same_day_fraud"

    build_prescription(
        d / "F017_prescription_ravi.pdf",
        doctor_name="S. Khan", doctor_reg="MH/23456/2018",
        doctor_specialization="MBBS (General Physician)",
        clinic_name="Khan Medical Clinic", clinic_address="Bandra, Mumbai - 400050",
        patient_name="Ravi Menon", patient_age=37, patient_gender="M", date="30-Oct-2024",
        diagnosis="Migraine with aura (acute attack)",
        medicines=["Tab Sumatriptan 50mg -- STAT", "Tab Domperidone 10mg -- STAT"],
        notes="4th consultation claim today for this member — see manifest for prior same-day history.",
    )
    record(ManifestEntry(
        test_case="TC009", file_id="F017", filename="F017_prescription_ravi.pdf",
        document_type="PRESCRIPTION", patient="Ravi Menon",
        purpose="Current (4th same-day) claim's prescription. Historical same-day claims are "
                "supplied as claim-history INPUT DATA for the fraud check (see below), not as PDFs — "
                "test_cases.json is not modified.",
        important_fields="Doctor: Dr. S. Khan; Diagnosis: Migraine with aura",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
    ))

    build_hospital_bill(
        d / "F018_hospital_bill_ravi.pdf",
        hospital_name="Khan Medical Clinic", hospital_address="Bandra, Mumbai - 400050",
        gstin="27AAACK4321F1ZX", bill_no="KMC/2024/00491", date="30-Oct-2024",
        patient_name="Ravi Menon", patient_age=37, patient_gender="M", referring_doctor="Dr. S. Khan",
        line_items=[("Consultation Fee", 1500.0), ("Sumatriptan 50mg", 200.0),
                    ("Domperidone 10mg", 80.0), ("Nursing charges", 3020.0)],
    )
    record(ManifestEntry(
        test_case="TC009", file_id="F018", filename="F018_hospital_bill_ravi.pdf",
        document_type="HOSPITAL_BILL", patient="Ravi Menon",
        purpose="Total exactly Rs. 4800.",
        important_fields="Total: Rs. 4800. Same-day history (input to FraudAnalysisAgent via "
                          "claims_history, same_day_claims_limit=2): CLM_0081 2024-10-30 Rs.1200 "
                          "City Clinic A; CLM_0082 2024-10-30 Rs.1800 City Clinic B; CLM_0083 "
                          "2024-10-30 Rs.2100 Wellness Center. This is the 4th same-day claim.",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2c_policy_note="Covered, no waiting period/exclusion issues.",
        phase2c_financial_note="No network discount; 10% consultation copay applies to the eligible base.",
        phase2c_fraud_note="SAME_DAY_CLAIMS_LIMIT_EXCEEDED (same_day_claim_count=4 > limit=2); "
                            "requires_manual_review=True; risk_level=HIGH.",
        expected_final_decision="MANUAL_REVIEW.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC010 — NETWORK HOSPITAL
# ═════════════════════════════════════════════════════════════════════════

def gen_tc010() -> None:
    d = OUT / "TC010_network_hospital"

    build_prescription(
        d / "F019_prescription_deepak.pdf",
        doctor_name="S. Iyer", doctor_reg="TN/56789/2013",
        doctor_specialization="MBBS, MD (Pulmonology)",
        clinic_name="Apollo Hospitals", clinic_address="Greams Road, Chennai - 600006",
        patient_name="Deepak Shah", patient_age=44, patient_gender="M", date="03-Nov-2024",
        diagnosis="Acute Bronchitis with wheeze",
        medicines=["Tab Amoxicillin 500mg -- 1-0-1 x 7 days", "Salbutamol Inhaler -- 2 puffs Q6H x 5 days"],
    )
    record(ManifestEntry(
        test_case="TC010", file_id="F019", filename="F019_prescription_deepak.pdf",
        document_type="PRESCRIPTION", patient="Deepak Shah",
        purpose="Supports a network-hospital consultation claim.",
        important_fields="Doctor: Dr. S. Iyer (TN/56789/2013); Diagnosis: Acute Bronchitis",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
    ))

    build_hospital_bill(
        d / "F020_apollo_hospital_bill.pdf",
        hospital_name="Apollo Hospitals", hospital_address="Greams Road, Chennai - 600006",
        gstin="33AAACC5555G1ZX", bill_no="APL/2024/88123", date="03-Nov-2024",
        patient_name="Deepak Shah", patient_age=44, patient_gender="M", referring_doctor="Dr. S. Iyer",
        line_items=[("Consultation Fee (Specialist)", 1500.0), ("Medicines", 3000.0)],
    )
    record(ManifestEntry(
        test_case="TC010", file_id="F020", filename="F020_apollo_hospital_bill.pdf",
        document_type="HOSPITAL_BILL", patient="Deepak Shah",
        purpose="'Apollo Hospitals' stated clearly and exactly (matches policy_terms.json's "
                "network_hospitals list) — total Rs. 4500.",
        important_fields="Hospital: Apollo Hospitals; Total: Rs. 4500",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2c_policy_note="is_network_hospital=True (exact normalized match); covered, no other issues.",
        phase2c_financial_note="Official worked example: Rs.4500 -> 20% network discount -> Rs.3600 "
                                "-> 10% copay (-Rs.360) -> Rs.3240 payable. NOTE: this implementation "
                                "additionally applies the consultation sub-limit (Rs. 2000) as a real "
                                "cap after the discount, producing Rs.1800 payable instead — a "
                                "disclosed, deliberate discrepancy documented in docs/tradeoffs.md. "
                                "Do not treat Rs.1800 as a bug to fix by editing policy_terms.json.",
        phase2c_fraud_note="LOW risk, no flags.",
        expected_final_decision="APPROVED per the official worked example (Rs. 3240); see the "
                                 "financial-calculation note above for this implementation's disclosed variance.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC011 — COMPONENT FAILURE (graceful degradation)
# ═════════════════════════════════════════════════════════════════════════

def gen_tc011() -> None:
    d = OUT / "TC011_component_failure"

    build_prescription(
        d / "F021_ayurvedic_prescription.pdf",
        doctor_name="Vaidya T. Krishnan", doctor_reg="AYUR/KL/2345/2019",
        doctor_specialization="BAMS (Ayurveda), MD (Kayachikitsa)",
        clinic_name="Ayur Wellness Centre", clinic_address="Thrissur, Kerala - 680001",
        patient_name="Kavita Nair", patient_age=41, patient_gender="F", date="28-Oct-2024",
        diagnosis="Chronic Joint Pain -- Amavata (Rheumatoid Arthritis equivalent)",
        medicines=["Panchakarma Therapy -- 5 sessions", "Maharasnadi Kashayam -- 15ml twice daily"],
        notes="AYUSH registered practitioner. simulate_component_failure=true for this claim "
              "(submit via the API/fixture flag, not encoded in the document itself).",
    )
    record(ManifestEntry(
        test_case="TC011", file_id="F021", filename="F021_ayurvedic_prescription.pdf",
        document_type="PRESCRIPTION", patient="Kavita Nair",
        purpose="Supports an ALTERNATIVE_MEDICINE claim submitted with simulate_component_failure=true "
                "— this flag lives on the claim submission, not the document, so it must be set when "
                "submitting via the UI/API test tooling if the UI exposes it, or noted for API-level testing.",
        important_fields="Doctor: Vaidya T. Krishnan (AYUR/KL/2345/2019); Treatment: Panchakarma Therapy",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
    ))

    build_hospital_bill(
        d / "F022_ayurvedic_hospital_bill.pdf",
        hospital_name="Ayur Wellness Centre", hospital_address="Thrissur, Kerala - 680001",
        gstin="32AAACC8765K1ZX", bill_no="AWC/2024/03312", date="28-Oct-2024",
        patient_name="Kavita Nair", patient_age=41, patient_gender="F", referring_doctor="Vaidya T. Krishnan",
        line_items=[("Panchakarma Therapy (5 sessions)", 3000.0), ("Consultation", 1000.0)],
    )
    record(ManifestEntry(
        test_case="TC011", file_id="F022", filename="F022_ayurvedic_hospital_bill.pdf",
        document_type="HOSPITAL_BILL", patient="Kavita Nair",
        purpose="Total exactly Rs. 4000.",
        important_fields="Line items: Panchakarma Therapy (5 sessions) 3000, Consultation 1000; Total: Rs. 4000",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2c_policy_note="Session-limit check is always WARNING (no cross-claim session tracking "
                             "yet — documented limitation).",
        phase2c_financial_note="If FraudAnalysisAgent raises ComponentFailureError "
                                "(simulate_component_failure=true), pipeline must NOT crash: trace shows "
                                "FAILED for FRAUD_ANALYSIS, fraud_analysis_result stays null, confidence "
                                "on other results should reflect the gap, and the claim still reaches the "
                                "end of the pipeline with status=PROCESSING.",
        phase2c_fraud_note="Component failure simulated — see financial note. A degraded/failed "
                            "FRAUD_ANALYSIS trace event, not a crash, is the expected behavior.",
        expected_final_decision="Official expected final decision: APPROVED (once Phase 2D exists) "
                                 "despite the simulated fraud-component failure — the claim itself has "
                                 "no other issues.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TC012 — EXCLUDED OBESITY/BARIATRIC TREATMENT
# ═════════════════════════════════════════════════════════════════════════

def gen_tc012() -> None:
    d = OUT / "TC012_excluded_treatment"

    build_prescription(
        d / "F023_bariatric_prescription.pdf",
        doctor_name="P. Banerjee", doctor_reg="WB/34567/2015",
        doctor_specialization="MBBS, MD (Bariatric Medicine)",
        clinic_name="Kolkata Wellness Clinic", clinic_address="Park Street, Kolkata - 700016",
        patient_name="Anita Desai", patient_age=33, patient_gender="F", date="18-Oct-2024",
        diagnosis="Morbid Obesity -- BMI 37",
        medicines=["Customised Diet Plan -- ongoing", "Behavioural counselling -- weekly"],
        notes="Treatment: Bariatric Consultation and Customised Diet Plan.",
    )
    record(ManifestEntry(
        test_case="TC012", file_id="F023", filename="F023_bariatric_prescription.pdf",
        document_type="PRESCRIPTION", patient="Anita Desai",
        purpose="Diagnosis and treatment both clearly obesity/bariatric-related — tests general "
                "exclusion keyword matching (obesity) and the obesity_treatment specific waiting period.",
        important_fields="Doctor: Dr. P. Banerjee (WB/34567/2015); Diagnosis: Morbid Obesity - BMI 37; "
                          "Treatment: Bariatric Consultation and Customised Diet Plan",
        expected_classification="PRESCRIPTION", expected_quality="GOOD",
    ))

    build_hospital_bill(
        d / "F024_bariatric_hospital_bill.pdf",
        hospital_name="Kolkata Wellness Clinic", hospital_address="Park Street, Kolkata - 700016",
        gstin="19AAACC6543E1ZX", bill_no="KWC/2024/07711", date="18-Oct-2024",
        patient_name="Anita Desai", patient_age=33, patient_gender="F", referring_doctor="Dr. P. Banerjee",
        line_items=[("Bariatric Consultation", 3000.0), ("Personalised Diet and Nutrition Program", 5000.0)],
    )
    record(ManifestEntry(
        test_case="TC012", file_id="F024", filename="F024_bariatric_hospital_bill.pdf",
        document_type="HOSPITAL_BILL", patient="Anita Desai",
        purpose="Total exactly Rs. 8000 (Bariatric Consultation 3000 + Diet Program 5000).",
        important_fields="Total: Rs. 8000",
        expected_classification="HOSPITAL_BILL", expected_quality="GOOD",
        phase2c_policy_note="EXCLUSION_CONDITIONS: FAILED ('Obesity and weight loss programs' keyword "
                             "match); WAITING_PERIOD_OBESITY_TREATMENT may also fire (365-day specific "
                             "condition waiting period).",
        phase2c_financial_note="Not meaningfully payable — excluded condition.",
        phase2c_fraud_note="LOW risk, no flags.",
        expected_final_decision="REJECTED, rejection reason EXCLUDED_CONDITION.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  EXTRA_PHASE2C — additional manual PolicyEngine/FinancialCalculation/
#  FraudAnalysis verification scenarios (not part of the official 12)
# ═════════════════════════════════════════════════════════════════════════

def gen_extra() -> None:
    d = OUT / "EXTRA_PHASE2C"

    # ── EXTRA01 — network vs non-network ────────────────────────────────
    build_prescription(
        d / "EXTRA01_prescription_network.pdf",
        doctor_name="S. Iyer", doctor_reg="TN/56789/2013", doctor_specialization="MBBS, MD (Pulmonology)",
        clinic_name="Apollo Hospitals", clinic_address="Greams Road, Chennai - 600006",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="05-Nov-2024",
        diagnosis="Seasonal Allergic Rhinitis", medicines=["Tab Cetirizine 10mg -- 0-0-1 x 10 days"],
    )
    build_hospital_bill(
        d / "EXTRA01_hospital_bill_network_apollo.pdf",
        hospital_name="Apollo Hospitals", hospital_address="Greams Road, Chennai - 600006",
        gstin="33AAACC5555G1ZX", bill_no="APL/2024/90011", date="05-Nov-2024",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", referring_doctor="Dr. S. Iyer",
        line_items=[("Consultation Fee", 1500.0), ("Medicines", 2500.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA01", file_id="EXTRA01-NET", filename="EXTRA01_prescription_network.pdf / "
        "EXTRA01_hospital_bill_network_apollo.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="Network-hospital variant: 'Apollo Hospitals' (in policy_terms.json network_hospitals) "
                "-- identical Rs.4000 claim to EXTRA01_NON_NETWORK except hospital name.",
        important_fields="Hospital: Apollo Hospitals; Total: Rs. 4000",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2c_policy_note="is_network_hospital=True.",
        phase2c_financial_note="20% network discount applied: 4000 -> 3200 -> 10% copay -> 2880 payable "
                                "(before any sub-limit cap; consultation sub-limit Rs.2000 will still cap "
                                "this further in the current implementation — compare against EXTRA01_NON_NETWORK).",
    ))

    build_prescription(
        d / "EXTRA01_prescription_nonnetwork.pdf",
        doctor_name="S. Iyer", doctor_reg="TN/56789/2013", doctor_specialization="MBBS, MD (Pulmonology)",
        clinic_name="ABC Family Clinic", clinic_address="Anna Nagar, Chennai - 600040",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="05-Nov-2024",
        diagnosis="Seasonal Allergic Rhinitis", medicines=["Tab Cetirizine 10mg -- 0-0-1 x 10 days"],
    )
    build_hospital_bill(
        d / "EXTRA01_hospital_bill_nonnetwork_abc.pdf",
        hospital_name="ABC Family Clinic, Bengaluru", hospital_address="Anna Nagar, Chennai - 600040",
        gstin="33AAACC6666H1ZX", bill_no="ABC/2024/00551", date="05-Nov-2024",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", referring_doctor="Dr. S. Iyer",
        line_items=[("Consultation Fee", 1500.0), ("Medicines", 2500.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA01", file_id="EXTRA01-NONNET", filename="EXTRA01_prescription_nonnetwork.pdf / "
        "EXTRA01_hospital_bill_nonnetwork_abc.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="Non-network variant: 'ABC Family Clinic' (NOT in network_hospitals) -- otherwise "
                "identical to EXTRA01_NETWORK.",
        important_fields="Hospital: ABC Family Clinic, Bengaluru; Total: Rs. 4000",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2c_policy_note="is_network_hospital=False (known non-network -> NOT_APPLICABLE finding, "
                             "not a WARNING, since the hospital name IS resolvable).",
        phase2c_financial_note="No network discount: 4000 -> 10% copay -> 3600 payable (before sub-limit "
                                "cap). Compare eligible-before-copay amount against EXTRA01_NETWORK to "
                                "isolate the discount's effect.",
    ))

    # ── EXTRA02 — sub-limit ─────────────────────────────────────────────
    build_prescription(
        d / "EXTRA02_prescription_sublimit.pdf",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015", doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Clinic", clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="06-Nov-2024",
        diagnosis="Chronic Sinusitis", medicines=["Tab Azithromycin 500mg -- 1-0-0 x 3 days"],
    )
    build_hospital_bill(
        d / "EXTRA02_hospital_bill_sublimit.pdf",
        hospital_name="City Clinic, Bengaluru", hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX", bill_no="CMC/2024/09901", date="06-Nov-2024",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", referring_doctor="Dr. Arun Sharma",
        line_items=[("Consultation", 3000.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA02", file_id="EXTRA02", filename="EXTRA02_prescription_sublimit.pdf / "
        "EXTRA02_hospital_bill_sublimit.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="Isolated sub-limit test: single line item Rs.3000, consultation category sub-limit "
                "is Rs.2000 (policy_terms.json opd_categories.consultation.sub_limit).",
        important_fields="Line item: Consultation, Rs. 3000; Total: Rs. 3000",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2c_policy_note="SUB_LIMIT: FAILED (Rs. 3000 claimed > Rs. 2000 category sub-limit).",
        phase2c_financial_note="Eligible Rs.3000 -> no network discount -> capped at sub-limit Rs.2000 "
                                "-> 10% copay (-Rs.200) -> Rs.1800 payable.",
    ))

    # ── EXTRA03 — minimum claim amount ──────────────────────────────────
    build_prescription(
        d / "EXTRA03_prescription_minclaim.pdf",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015", doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Clinic", clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="07-Nov-2024",
        diagnosis="Minor Cold Symptoms", medicines=["Tab Cetirizine 10mg -- 0-0-1 x 3 days"],
    )
    build_hospital_bill(
        d / "EXTRA03_hospital_bill_minclaim.pdf",
        hospital_name="City Clinic, Bengaluru", hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX", bill_no="CMC/2024/09902", date="07-Nov-2024",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", referring_doctor="Dr. Arun Sharma",
        line_items=[("Consultation Fee", 400.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA03", file_id="EXTRA03", filename="EXTRA03_prescription_minclaim.pdf / "
        "EXTRA03_hospital_bill_minclaim.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="Claim total Rs.400, below the policy's minimum_claim_amount (submission_rules."
                "minimum_claim_amount = Rs.500 in policy_terms.json).",
        important_fields="Total: Rs. 400",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2a_note="ClaimValidationAgent should flag the claim as below minimum at Stage 1 "
                     "(claim validation), before document verification even runs.",
        phase2c_policy_note="If validation still allows it through, PolicyEngine's MINIMUM_CLAIM_AMOUNT "
                             "check should also FAIL.",
    ))

    # ── EXTRA04 — high value claim ───────────────────────────────────────
    build_prescription(
        d / "EXTRA04_prescription_highvalue.pdf",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015", doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre", clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="08-Nov-2024",
        diagnosis="Multi-specialty Comprehensive Health Evaluation",
        medicines=["As advised per specialist consultations"],
        tests=["Cardiology Consultation", "Comprehensive Metabolic Panel", "Executive Health Package"],
        notes="Bundled multi-specialist consultation package (high-value test fixture).",
    )
    build_hospital_bill(
        d / "EXTRA04_hospital_bill_highvalue.pdf",
        hospital_name="City Medical Centre", hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX", bill_no="CMC/2024/09903", date="08-Nov-2024",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", referring_doctor="Dr. Arun Sharma",
        line_items=[("Cardiology Consultation", 5000.0), ("Comprehensive Metabolic Panel", 10000.0),
                    ("Executive Health Package", 15000.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA04", file_id="EXTRA04", filename="EXTRA04_prescription_highvalue.pdf / "
        "EXTRA04_hospital_bill_highvalue.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="Total Rs.30000, above fraud_thresholds.high_value_claim_threshold (Rs.25000) AND "
                "auto_manual_review_above (Rs.25000).",
        important_fields="Total: Rs. 30000",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2c_fraud_note="HIGH_VALUE_CLAIM and AUTO_MANUAL_REVIEW_THRESHOLD_EXCEEDED both triggered; "
                            "risk_level=HIGH; requires_manual_review=True. No fabricated fraud evidence "
                            "-- these are the only two deterministic threshold triggers expected.",
    ))

    # ── EXTRA05 — multiple monthly claims ────────────────────────────────
    build_prescription(
        d / "EXTRA05_prescription_monthly.pdf",
        doctor_name="S. Khan", doctor_reg="MH/23456/2018", doctor_specialization="MBBS (General Physician)",
        clinic_name="Khan Medical Clinic", clinic_address="Bandra, Mumbai - 400050",
        patient_name="Ravi Menon", patient_age=37, patient_gender="M", date="26-Oct-2024",
        diagnosis="Seasonal Cough and Cold", medicines=["Tab Cetirizine 10mg -- 0-0-1 x 5 days",
                                                          "Cough Syrup -- 10ml TDS x 5 days"],
        notes="Current (7th) claim within October 2024 for this member -- see manifest for the "
              "monthly-claim-history input data (monthly_claims_limit=6).",
    )
    build_hospital_bill(
        d / "EXTRA05_hospital_bill_monthly.pdf",
        hospital_name="Khan Medical Clinic", hospital_address="Bandra, Mumbai - 400050",
        gstin="27AAACK4321F1ZX", bill_no="KMC/2024/00512", date="26-Oct-2024",
        patient_name="Ravi Menon", patient_age=37, patient_gender="M", referring_doctor="Dr. S. Khan",
        line_items=[("Consultation Fee", 1000.0), ("Medicines", 500.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA05", file_id="EXTRA05", filename="EXTRA05_prescription_monthly.pdf / "
        "EXTRA05_hospital_bill_monthly.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Ravi Menon",
        purpose="Current claim's total is Rs.1500 (unremarkable on its own) -- the fraud signal comes "
                "entirely from claim-HISTORY input data, supplied the same way as TC009 "
                "(submission.claims_history), not from these PDFs. 6 prior claims in October 2024 "
                "(different dates, none same-day) + this one = 7th claim, exceeding "
                "monthly_claims_limit=6.",
        important_fields="Total: Rs. 1500. Monthly history (input data): 6 prior CONSULTATION claims "
                          "for EMP008 dated across October 2024, none on 2024-10-26.",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2c_fraud_note="MONTHLY_CLAIMS_LIMIT_EXCEEDED (monthly_claim_count=7 > limit=6); "
                            "requires_manual_review=True; SAME_DAY_CLAIMS_LIMIT_EXCEEDED must NOT also "
                            "fire (no other claim shares 2024-10-26) -- isolates the monthly check from "
                            "the same-day check TC009 already exercises.",
    ))

    # ── EXTRA06 — correct member identity (positive control) ────────────
    build_prescription(
        d / "EXTRA06_prescription_correct_identity.pdf",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015", doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre", clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="09-Nov-2024",
        diagnosis="Routine Follow-up", medicines=["Tab Paracetamol 650mg -- SOS"],
    )
    build_hospital_bill(
        d / "EXTRA06_hospital_bill_correct_identity.pdf",
        hospital_name="City Medical Centre", hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX", bill_no="CMC/2024/09904", date="09-Nov-2024",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", referring_doctor="Dr. Arun Sharma",
        line_items=[("Consultation Fee", 1000.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA06", file_id="EXTRA06", filename="EXTRA06_prescription_correct_identity.pdf / "
        "EXTRA06_hospital_bill_correct_identity.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="Positive identity control: both documents say 'Rajesh Kumar', claim member is "
                "EMP001/Rajesh Kumar -- should pass cross-document AND document<->member checks cleanly.",
        important_fields="Both documents: Patient = Rajesh Kumar",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2a_note="CROSS_DOCUMENT_VALIDATION: PASS (doc<->doc and doc<->member both match).",
    ))

    # ── EXTRA07 — both documents wrong person (Phase 2A regression) ─────
    build_prescription(
        d / "EXTRA07_prescription_wrong_person.pdf",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015", doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre", clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Vikram Joshi", patient_age=45, patient_gender="M", date="09-Nov-2024",
        diagnosis="Viral Fever", medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days"],
    )
    build_hospital_bill(
        d / "EXTRA07_hospital_bill_wrong_person.pdf",
        hospital_name="City Medical Centre", hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX", bill_no="CMC/2024/09905", date="09-Nov-2024",
        patient_name="Vikram Joshi", patient_age=45, patient_gender="M", referring_doctor="Dr. Arun Sharma",
        line_items=[("Consultation Fee", 1000.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA07", file_id="EXTRA07", filename="EXTRA07_prescription_wrong_person.pdf / "
        "EXTRA07_hospital_bill_wrong_person.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Vikram Joshi (on documents)",
        purpose="REGRESSION for the Phase 2A member-identity bug: claim submitted for member "
                "EMP001/Rajesh Kumar, but BOTH documents say 'Vikram Joshi' -- documents agree with "
                "EACH OTHER but disagree with the claim's actual member. Before the fix, this "
                "incorrectly PASSED cross-document validation.",
        important_fields="Both documents: Patient = Vikram Joshi. Claim member_id = EMP001 (Rajesh Kumar).",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2a_note="CROSS_DOCUMENT_VALIDATION: BLOCKED -- message names both 'Vikram Joshi' "
                     "(uploaded documents) and 'Rajesh Kumar (EMP001)' (the claim's actual member).",
        phase2c_policy_note="Not reached.", phase2c_financial_note="Not reached.", phase2c_fraud_note="Not reached.",
    ))

    # ── EXTRA08 — one correct, one wrong person ──────────────────────────
    build_prescription(
        d / "EXTRA08_prescription_correct.pdf",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015", doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre", clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", date="09-Nov-2024",
        diagnosis="Viral Fever", medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days"],
    )
    build_hospital_bill(
        d / "EXTRA08_hospital_bill_wrong.pdf",
        hospital_name="City Medical Centre", hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX", bill_no="CMC/2024/09906", date="09-Nov-2024",
        patient_name="Vikram Joshi", patient_age=45, patient_gender="M", referring_doctor="Dr. Arun Sharma",
        line_items=[("Consultation Fee", 1000.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA08", file_id="EXTRA08", filename="EXTRA08_prescription_correct.pdf / "
        "EXTRA08_hospital_bill_wrong.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Rajesh Kumar (Rx) / Vikram Joshi (Bill)",
        purpose="Mixed mismatch: prescription correctly says 'Rajesh Kumar' (the claim member), but "
                "the hospital bill says 'Vikram Joshi' -- both a document<->document mismatch AND a "
                "document<->member mismatch simultaneously.",
        important_fields="Prescription: Patient = Rajesh Kumar. Hospital Bill: Patient = Vikram Joshi.",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2a_note="CROSS_DOCUMENT_VALIDATION: BLOCKED -- the document<->document check (checked "
                     "first, per Decision 31) already fails here, since the two documents disagree "
                     "with each other before member identity is even compared.",
        phase2c_policy_note="Not reached.", phase2c_financial_note="Not reached.", phase2c_fraud_note="Not reached.",
    ))

    # ── EXTRA09 — hernia false-positive regression, isolated ────────────
    build_prescription(
        d / "EXTRA09_prescription_herniation.pdf",
        doctor_name="Venkat Rao", doctor_reg="AP/67890/2017", doctor_specialization="MBBS, MS (Orthopaedics)",
        clinic_name="Orthopedic Specialty Centre", clinic_address="Banjara Hills, Hyderabad - 500034",
        patient_name="Suresh Patil", patient_age=49, patient_gender="M", date="10-Nov-2024",
        diagnosis="Suspected Lumbar Disc Herniation with mild radiculopathy",
        medicines=["Tab Etoricoxib 90mg -- 0-0-1 x 7 days"],
        # No `notes=` here deliberately: any text visible on the document
        # itself is what a real extraction model would read, so the
        # regression-test explanation belongs only in the manifest/code
        # comments below, never inside the rendered PDF content -- an
        # earlier draft of this fixture accidentally put the word "Hernia"
        # into a visible `notes` line explaining the test's own purpose,
        # which would have defeated the point of an isolated repro.
    )
    build_hospital_bill(
        d / "EXTRA09_hospital_bill_herniation.pdf",
        hospital_name="Orthopedic Specialty Centre", hospital_address="Banjara Hills, Hyderabad - 500034",
        gstin="36AAACC1111J1ZX", bill_no="OSC/2024/00721", date="10-Nov-2024",
        patient_name="Suresh Patil", patient_age=49, patient_gender="M", referring_doctor="Dr. Venkat Rao",
        line_items=[("Consultation Fee (Specialist)", 2000.0)],
    )
    record(ManifestEntry(
        test_case="EXTRA09", file_id="EXTRA09", filename="EXTRA09_prescription_herniation.pdf / "
        "EXTRA09_hospital_bill_herniation.pdf",
        document_type="PRESCRIPTION + HOSPITAL_BILL", patient="Suresh Patil",
        purpose="Isolated regression check for the PolicyEngine word-boundary fix (docs/tradeoffs.md "
                "'Diagnosis/Exclusion Normalization', AI_HANDOFF.md Decision 34): diagnosis text "
                "contains 'Herniation' but never the standalone word 'Hernia'. Deliberately simpler "
                "than TC007 (plain CONSULTATION, no MRI/pre-auth) to isolate JUST the text-matching "
                "behavior from TC007's other policy findings.",
        important_fields="Diagnosis: 'Suspected Lumbar Disc Herniation with mild radiculopathy' -- "
                          "note: NOT 'Hernia' standalone anywhere.",
        expected_classification="PRESCRIPTION / HOSPITAL_BILL",
        phase2c_policy_note="WAITING_PERIOD_HERNIA must NOT appear in failed_rules or findings at all "
                             "-- if it does, the word-boundary fix has regressed.",
        phase2c_financial_note="Rs. 2000 -> 10% copay -> Rs. 1800 payable (within consultation sub-limit).",
        phase2c_fraud_note="LOW risk, no flags.",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  QUALITY_TESTS — document-robustness variations (not tied to a specific
#  policy scenario; same semantic content throughout, only presentation
#  quality varies)
# ═════════════════════════════════════════════════════════════════════════

def gen_quality_tests() -> None:
    d = OUT / "QUALITY_TESTS"

    # 1. handwritten_prescription.pdf
    img = render_prescription_image(
        patient_name="Rajesh Kumar", doctor_name="Arun Sharma", doctor_reg="KA/45678/2015",
        diagnosis="Viral Fever", date="01-Nov-2024",
        medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days", "Tab Vitamin C 500mg -- 0-0-1 x 7 days"],
        handwritten_style=True,
    )
    embed_image_pdf(d / "handwritten_prescription.pdf", img,
                     caption="(prescription rendered in a handwritten-style layout)")
    record(ManifestEntry(
        test_case="QUALITY_TESTS", file_id="Q1", filename="handwritten_prescription.pdf",
        document_type="PRESCRIPTION", patient="Rajesh Kumar",
        purpose="Handwritten-style prescription -- same content as TC004's F007, styled to simulate "
                "non-uniform handwriting rather than printed text (per sample_documents_guide.md's "
                "recommended real-world variations).",
        important_fields="Same as TC004 F007 (Rajesh Kumar, Viral Fever, Paracetamol + Vitamin C).",
        expected_classification="PRESCRIPTION",
        expected_quality="GOOD or MODERATE (content fully present and legible, just styled).",
    ))

    # 2. phone_photo_bill.pdf
    img = render_hospital_bill_image(
        hospital_name="City Clinic, Bengaluru", patient_name="Rajesh Kumar", date="01-Nov-2024",
        line_items=[("Consultation Fee", 1000.0), ("CBC Test", 300.0), ("Dengue NS1 Antigen Test", 200.0)],
        bill_no="CMC/2024/08322",
    )
    img = phone_photo_effect(img)
    embed_image_pdf(d / "phone_photo_bill.pdf", img,
                     caption="(hospital bill simulated as a phone-camera photo: rotation + shadow + slight blur)")
    record(ManifestEntry(
        test_case="QUALITY_TESTS", file_id="Q2", filename="phone_photo_bill.pdf",
        document_type="HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="Same content as TC004's F008, photographed-style (slight rotation, soft diagonal "
                "shadow, mild noise, gentle blur) rather than perfectly scanned.",
        important_fields="Same as TC004 F008 (Total Rs. 1500).",
        expected_classification="HOSPITAL_BILL",
        expected_quality="GOOD or MODERATE (readable but visibly a photo, not a clean scan).",
    ))

    # 3. stamped_prescription.pdf
    img = render_prescription_image(
        patient_name="Priya Singh", doctor_name="Kavitha Rao", doctor_reg="KA/44123/2016",
        diagnosis="Upper Respiratory Tract Infection", date="12-Nov-2024",
        medicines=["Tab Azithromycin 500mg -- 1-0-0 x 3 days", "Tab Cetirizine 10mg -- 0-0-1 x 5 days"],
    )
    img = draw_stamp(img, "VERIFIED", center=(int(img.width * 0.60), int(img.height * 0.275)))
    embed_image_pdf(d / "stamped_prescription.pdf", img,
                     caption="(prescription with a rubber stamp physically overlapping part of the text)")
    record(ManifestEntry(
        test_case="QUALITY_TESTS", file_id="Q3", filename="stamped_prescription.pdf",
        document_type="PRESCRIPTION", patient="Priya Singh",
        purpose="A rubber-stamp graphic is drawn over part of the diagnosis/Rx area, simulating a "
                "clinic verification stamp physically obscuring some printed text underneath it.",
        important_fields="Patient: Priya Singh; Diagnosis: Upper Respiratory Tract Infection "
                          "(partially covered by the stamp graphic near the diagnosis line).",
        expected_classification="PRESCRIPTION",
        expected_quality="MODERATE (some fields may be partially obscured by the stamp).",
    ))

    # 4. multilingual_prescription.pdf
    img = render_multilingual_prescription_image(
        patient_name_en="Rajesh Kumar", patient_name_hi="राजेश कुमार",
        doctor_name="Arun Sharma", doctor_reg="KA/45678/2015",
        diagnosis_en="Viral Fever", diagnosis_hi="वायरल बुखार", date="01-Nov-2024",
        medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days (दिन में तीन बार)",
                   "Tab Vitamin C 500mg -- 0-0-1 x 7 days"],
    )
    embed_image_pdf(d / "multilingual_prescription.pdf", img,
                     caption="(prescription mixing Hindi (Devanagari) and English)")
    record(ManifestEntry(
        test_case="QUALITY_TESTS", file_id="Q4", filename="multilingual_prescription.pdf",
        document_type="PRESCRIPTION", patient="Rajesh Kumar (राजेश कुमार)",
        purpose="Patient name, diagnosis, and one medicine instruction are given in both English and "
                "Hindi (Devanagari script) -- a real-world variation sample_documents_guide.md calls out.",
        important_fields="Patient: Rajesh Kumar / राजेश कुमार; Diagnosis: Viral Fever / वायरल बुखार",
        expected_classification="PRESCRIPTION",
        expected_quality="GOOD (fully legible, bilingual).",
    ))

    # 5. partial_bill.pdf
    img = render_hospital_bill_image(
        hospital_name="City Clinic, Bengaluru", patient_name="Rajesh Kumar", date="01-Nov-2024",
        line_items=[("Consultation Fee", 1000.0), ("CBC Test", 300.0), ("Dengue NS1 Antigen Test", 200.0)],
        bill_no="CMC/2024/08322",
    )
    img = crop_partial(img, keep_fraction=0.32)  # cuts before the total-amount line
    embed_image_pdf(d / "partial_bill.pdf", img,
                     caption="(hospital bill scan cut off partway down the page -- bottom half missing)")
    record(ManifestEntry(
        test_case="QUALITY_TESTS", file_id="Q5", filename="partial_bill.pdf",
        document_type="HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="Only the top ~32% of the bill was 'scanned' -- header, patient info, and all three "
                "line items are visible, but the Total line is cut off mid-glyph (only a sliver of it "
                "survives) and everything below is genuinely blank.",
        important_fields="Patient: Rajesh Kumar and all 3 line items visible; Total amount NOT "
                          "reliably readable (cut off).",
        expected_classification="HOSPITAL_BILL",
        expected_quality="POOR / PARTIAL (line items present but total not reliably extractable "
                          "from this document alone).",
    ))

    # 6. corrected_bill.pdf
    # The line item is rendered at its ORIGINAL printed amount (Rs.250) so
    # the correction overlay strikes through real printed text; the
    # printed Total therefore intentionally still reflects the
    # pre-correction figure (Rs.1450, not Rs.1500) -- a realistic "the
    # cashier corrected the line item by hand but never reprinted the
    # total" scenario, which doubles as a case for FinancialCalculation
    # Service's bill-amount-reconciliation warning (not a generation bug).
    img = render_hospital_bill_image(
        hospital_name="City Clinic, Bengaluru", patient_name="Rajesh Kumar", date="01-Nov-2024",
        line_items=[("Consultation Fee", 1000.0), ("CBC Test", 250.0), ("Dengue NS1 Antigen Test", 200.0)],
        bill_no="CMC/2024/08322",
    )
    img = apply_correction(img, "Rs. 250.00", "Rs. 300.00", at=(680, 261))  # aligns with the CBC Test row
    embed_image_pdf(d / "corrected_bill.pdf", img,
                     caption="(hospital bill with one line item manually struck through and corrected)")
    record(ManifestEntry(
        test_case="QUALITY_TESTS", file_id="Q6", filename="corrected_bill.pdf",
        document_type="HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="The CBC Test amount was printed as Rs.250, then manually struck through and "
                "hand-corrected to Rs.300 (with a 'corrected — Cashier' annotation) -- simulates a "
                "real-world manual correction that was never reflected in the printed Total.",
        important_fields="CBC Test: printed Rs.250, struck through, hand-corrected to Rs.300. Printed "
                          "Total: Rs.1450 (1000+250+200) -- intentionally NOT updated to Rs.1500, so "
                          "this also doubles as a bill-amount-reconciliation test case (sum of the "
                          "as-corrected line items vs. the stale printed total).",
        expected_classification="HOSPITAL_BILL",
        expected_quality="MODERATE (correction may reduce extraction confidence for that line item; "
                          "FinancialCalculationService should raise a reconciliation warning, never "
                          "silently pick one figure over the other).",
    ))

    # 7. duplicate_stamp_bill.pdf
    img = render_hospital_bill_image(
        hospital_name="City Clinic, Bengaluru", patient_name="Rajesh Kumar", date="01-Nov-2024",
        line_items=[("Consultation Fee", 1000.0), ("CBC Test", 300.0), ("Dengue NS1 Antigen Test", 200.0)],
        bill_no="CMC/2024/08322",
    )
    img = draw_duplicate_watermark(img, "DUPLICATE")
    embed_image_pdf(d / "duplicate_stamp_bill.pdf", img,
                     caption="(hospital bill marked with a diagonal 'DUPLICATE' watermark, as for a photocopy)")
    record(ManifestEntry(
        test_case="QUALITY_TESTS", file_id="Q7", filename="duplicate_stamp_bill.pdf",
        document_type="HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="A large diagonal translucent 'DUPLICATE' watermark overlays the whole bill, as Indian "
                "hospitals commonly mark photocopies (vs. an 'ORIGINAL' stamp on the first copy).",
        important_fields="Same content as TC004 F008 (Total Rs. 1500), with a DUPLICATE watermark overlay.",
        expected_classification="HOSPITAL_BILL",
        expected_quality="GOOD or MODERATE (watermark should not block extraction of the underlying fields).",
    ))

    # 8. multipage_bill.pdf
    build_hospital_bill_multipage(
        d / "multipage_bill.pdf",
        hospital_name="City Medical Centre", hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX", bill_no="CMC/2024/11220", date="12-Nov-2024",
        patient_name="Rajesh Kumar", patient_age=39, patient_gender="M", referring_doctor="Dr. Arun Sharma",
        line_items_page1=[("Consultation Fee", 1000.0), ("CBC Test", 300.0)],
        line_items_page2=[("Dengue NS1 Antigen Test", 200.0), ("Nursing Charges", 500.0)],
    )
    record(ManifestEntry(
        test_case="QUALITY_TESTS", file_id="Q8", filename="multipage_bill.pdf",
        document_type="HOSPITAL_BILL", patient="Rajesh Kumar",
        purpose="A genuinely multi-page (2-page) PDF -- page 1 has patient info + first 2 line items, "
                "page 2 continues with the remaining 2 line items and the grand total.",
        important_fields="Total across both pages: Rs. 2000 (1000+300+200+500).",
        expected_classification="HOSPITAL_BILL",
        expected_quality="GOOD (multi-page document; extraction must consider both pages, not just page 1).",
    ))


# ═════════════════════════════════════════════════════════════════════════
#  TEST_MANIFEST.md — generated from the MANIFEST list recorded above, so
#  the manifest can never drift out of sync with what was actually built.
# ═════════════════════════════════════════════════════════════════════════

def write_manifest() -> None:
    lines = [
        "# Test Document Manifest",
        "",
        "Auto-generated by `generate_test_documents.py` from the same data used to build every "
        "PDF/image below — this file cannot drift out of sync with the actual documents. "
        "Every document is synthetic test data; see each PDF's own footer.",
        "",
        "See `README.md` in this directory for how to actually run each case through the UI, "
        "and which cases are expected to stop early.",
        "",
    ]
    current_case = None
    for e in MANIFEST:
        if e.test_case != current_case:
            current_case = e.test_case
            lines.append(f"## {current_case}")
            lines.append("")
        lines.append(f"### {e.file_id} — `{e.filename}`")
        lines.append("")
        lines.append(f"- **Document type:** {e.document_type}")
        lines.append(f"- **Patient:** {e.patient}")
        lines.append(f"- **Purpose:** {e.purpose}")
        lines.append(f"- **Important fields:** {e.important_fields}")
        lines.append(f"- **Expected classification:** {e.expected_classification}")
        lines.append(f"- **Expected quality:** {e.expected_quality}")
        lines.append(f"- **Expected Phase 2A behavior:** {e.phase2a_note}")
        lines.append(f"- **Expected Phase 2B extraction:** {e.phase2b_note}")
        lines.append(f"- **Expected Phase 2C policy behavior:** {e.phase2c_policy_note}")
        lines.append(f"- **Expected Phase 2C financial behavior:** {e.phase2c_financial_note}")
        lines.append(f"- **Expected Phase 2C fraud behavior:** {e.phase2c_fraud_note}")
        lines.append(f"- **Expected final decision:** {e.expected_final_decision}")
        lines.append("")
    (OUT / "TEST_MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote TEST_MANIFEST.md ({len(MANIFEST)} entries).")


if __name__ == "__main__":
    gen_tc001()
    gen_tc002()
    gen_tc003()
    gen_tc004()
    gen_tc005()
    gen_tc006()
    gen_tc007()
    gen_tc008()
    gen_tc009()
    gen_tc010()
    gen_tc011()
    gen_tc012()
    print(f"TC001-TC012 generated: {len(MANIFEST)} manifest entries so far.")
    gen_extra()
    print(f"EXTRA_PHASE2C generated: {len(MANIFEST)} manifest entries so far.")
    gen_quality_tests()
    print(f"QUALITY_TESTS generated: {len(MANIFEST)} manifest entries total.")
    write_manifest()
