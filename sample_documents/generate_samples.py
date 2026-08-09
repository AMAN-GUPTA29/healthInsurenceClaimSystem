"""
Sample Document Generator -- Health Insurance Claims Processing System
======================================================================
Generates realistic Indian medical PDFs and JPG images for all 12 test
cases described in test_cases.json, following the layouts in
sample_documents_guide.md.

Run from the repo root:
    python sample_documents/generate_samples.py

Output layout:
    sample_documents/
        TC001_wrong_document/
            F001_prescription_rajesh.pdf
            F002_prescription_duplicate.pdf   <- both are prescriptions (wrong)
        TC002_unreadable_document/
            F003_prescription_sneha.pdf
            F004_blurry_pharmacy_bill.jpg     <- deliberately degraded
        TC003_patient_mismatch/
            F005_prescription_rajesh.pdf
            F006_bill_arjun.pdf               <- different patient name
        TC004_clean_consultation/
            F007_prescription_rajesh.pdf
            F008_hospital_bill_rajesh.pdf
        TC005_waiting_period_diabetes/
            F009_prescription_vikram.pdf
            F010_hospital_bill_vikram.pdf
        TC006_dental_partial_cosmetic/
            F011_dental_bill_priya.pdf
        TC007_mri_no_preauth/
            F012_prescription_suresh.pdf
            F013_mri_lab_report.pdf
            F014_mri_hospital_bill.pdf
        TC008_per_claim_limit/
            F015_prescription_amit.pdf
            F016_hospital_bill_amit.pdf
        TC009_fraud_same_day/
            F017_prescription_ravi.pdf
            F018_hospital_bill_ravi.pdf
        TC010_network_discount/
            F019_prescription_deepak.pdf
            F020_hospital_bill_apollo.pdf
        TC011_graceful_degradation/
            F021_prescription_ayurveda.pdf
            F022_hospital_bill_ayurwellness.pdf
        TC012_excluded_treatment/
            F023_prescription_bariatric.pdf
            F024_hospital_bill_bariatric.pdf
        EXTRAS/
            lab_report_dengue.pdf             <- generic lab report example
            pharmacy_bill_standard.pdf        <- standard pharmacy bill example
            handwritten_style_prescription.jpg<- simulates handwritten Rx
            multilingual_prescription.pdf     <- Hindi/English mix
"""

from __future__ import annotations

import os
import random
import textwrap
from pathlib import Path
from typing import Optional

# -- Graceful import ----------------------------------------------------------
try:
    from fpdf import FPDF, XPos, YPos
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False
    print("[WARN] fpdf2 not found. Install with: pip install fpdf2")

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("[WARN] Pillow not found. Install with: pip install Pillow")

# -- Output root ---------------------------------------------------------------
ROOT = Path(__file__).parent
OUT_DIR = ROOT  # same directory as this script


# ???????????????????????????????????????????????????????????????????????????????
#  FPDF helper -- Indian medical document style
# ???????????????????????????????????????????????????????????????????????????????

class MedicalPDF(FPDF):
    """Thin wrapper around FPDF with helpers for Indian medical document layout."""

    PAGE_W = 210   # A4 mm
    MARGIN = 15
    INNER_W = PAGE_W - 2 * MARGIN

    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(self.MARGIN, self.MARGIN, self.MARGIN)
        self.set_auto_page_break(auto=True, margin=15)

    # -- Low-level drawing helpers ---------------------------------------------

    def hline(self, lw: float = 0.3):
        self.set_line_width(lw)
        x = self.get_x()
        y = self.get_y()
        self.line(self.MARGIN, y, self.PAGE_W - self.MARGIN, y)
        self.ln(2)

    def section_box(self, content_lines: list[str], border: bool = True):
        """Draw a box with lines inside."""
        if border:
            self.rect(self.MARGIN, self.get_y(), self.INNER_W, len(content_lines) * 6 + 4)
        self.ln(2)
        for line in content_lines:
            self.set_font("Courier", size=9)
            self.cell(self.INNER_W, 6, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)

    def header_block(self, title: str, subtitle: str = "", address: str = ""):
        """Print a standard Indian clinic/hospital header."""
        self.add_page()
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 8, title, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if subtitle:
            self.set_font("Helvetica", "", 10)
            self.cell(0, 5, subtitle, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if address:
            self.set_font("Helvetica", "", 9)
            self.cell(0, 5, address, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.hline(0.5)
        self.ln(1)

    def field_row(self, label: str, value: str, bold_label: bool = True):
        self.set_font("Helvetica", "B" if bold_label else "", 9)
        lw = 45
        self.cell(lw, 6, label + ":")
        self.set_font("Helvetica", "", 9)
        self.cell(self.INNER_W - lw, 6, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def two_col_row(self, left_label: str, left_val: str,
                    right_label: str, right_val: str):
        half = self.INNER_W / 2
        self.set_font("Helvetica", "B", 9)
        self.cell(22, 6, left_label + ":")
        self.set_font("Helvetica", "", 9)
        self.cell(half - 22, 6, left_val)
        self.set_font("Helvetica", "B", 9)
        self.cell(25, 6, right_label + ":")
        self.set_font("Helvetica", "", 9)
        self.cell(half - 25, 6, right_val, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def table_header(self, cols: list[tuple[str, float]], fill: bool = True):
        if fill:
            self.set_fill_color(220, 220, 220)
        self.set_font("Helvetica", "B", 9)
        for label, width in cols:
            self.cell(width, 7, label, border=1, fill=fill, align="C")
        self.ln()

    def table_row(self, cols: list[tuple[str, float]], align: str = "L"):
        self.set_font("Helvetica", "", 9)
        for text, width in cols:
            self.cell(width, 6, text, border=1, align=align)
        self.ln()

    def signature_block(self, name: str, reg: str, role: str = ""):
        self.ln(8)
        self.hline(0.2)
        self.set_font("Helvetica", "B", 9)
        self.cell(0, 5, name, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 8)
        if role:
            self.cell(0, 4, role, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.cell(0, 4, f"Reg. No: {reg}    [Signature & Stamp]",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ???????????????????????????????????????????????????????????????????????????????
#  Document builders
# ???????????????????????????????????????????????????????????????????????????????

def build_prescription(
    *,
    doctor_name: str,
    doctor_reg: str,
    doctor_specialization: str,
    clinic_name: str,
    clinic_address: str,
    patient_name: str,
    patient_age: int,
    patient_gender: str,
    date: str,
    diagnosis: str,
    medicines: list[str],
    tests: list[str] | None = None,
    notes: str = "",
    followup: str = "After 5 days if no improvement",
) -> MedicalPDF:
    pdf = MedicalPDF()
    pdf.header_block(
        title=f"Dr. {doctor_name}",
        subtitle=f"{doctor_specialization} | Reg. No: {doctor_reg}",
        address=f"{clinic_name}, {clinic_address} | Ph: +91-80-XXXXXXXX",
    )

    pdf.two_col_row("Patient", patient_name, "Date", date)
    pdf.two_col_row(
        "Age", f"{patient_age} years",
        "Gender", "Male" if patient_gender.upper() == "M" else "Female",
    )
    pdf.ln(2)
    pdf.hline()

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, f"Diagnosis: {diagnosis}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Rx:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Courier", "", 9)
    for i, med in enumerate(medicines, 1):
        pdf.cell(0, 6, f"  {i}. {med}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if tests:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Investigations:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, "  " + ", ".join(tests), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if notes:
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 5, f"Note: {notes}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(2)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, f"Follow-up: {followup}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.signature_block(
        name=f"Dr. {doctor_name}",
        reg=doctor_reg,
        role=doctor_specialization,
    )
    return pdf


def build_hospital_bill(
    *,
    hospital_name: str,
    hospital_address: str,
    gstin: str,
    bill_no: str,
    date: str,
    patient_name: str,
    patient_age: int,
    patient_gender: str,
    referring_doctor: str,
    line_items: list[tuple[str, float]],
    gst_percent: float = 0.0,
    payment_mode: str = "UPI / Cash",
) -> MedicalPDF:
    pdf = MedicalPDF()
    pdf.header_block(
        title=hospital_name,
        subtitle=hospital_address,
        address=f"GSTIN: {gstin} | Ph: 080-XXXXXXXX",
    )

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "BILL / RECEIPT", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.hline()

    pdf.two_col_row("Bill No", bill_no, "Date", date)
    pdf.field_row("Patient Name", patient_name)
    pdf.two_col_row(
        "Age", f"{patient_age} yrs",
        "Gender", "Male" if patient_gender.upper() == "M" else "Female",
    )
    pdf.field_row("Referring Doctor", referring_doctor)
    pdf.ln(3)
    pdf.hline()

    # Table
    col_desc = pdf.INNER_W - 20 - 20 - 30
    pdf.table_header([
        ("DESCRIPTION", col_desc), ("QTY", 20), ("RATE", 20), ("AMOUNT (Rs.)", 30),
    ])
    subtotal = 0.0
    for desc, amount in line_items:
        pdf.table_row([
            (desc, col_desc), ("1", 20), (f"{amount:,.2f}", 20), (f"{amount:,.2f}", 30),
        ])
        subtotal += amount

    gst_amount = subtotal * gst_percent / 100
    total = subtotal + gst_amount

    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    col_right = 50
    pdf.cell(pdf.INNER_W - col_right, 6, "Subtotal:", align="R")
    pdf.cell(col_right, 6, f"Rs. {subtotal:,.2f}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(pdf.INNER_W - col_right, 6, f"GST ({gst_percent:.0f}% on medical):", align="R")
    pdf.cell(col_right, 6, f"Rs. {gst_amount:,.2f}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(pdf.INNER_W - col_right, 6, "Total Amount:", align="R")
    pdf.cell(col_right, 6, f"Rs. {total:,.2f}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(4)
    pdf.hline()
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, f"Payment Mode: {payment_mode}    |    Received by: Cashier  [Cashier Stamp]",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return pdf


def build_pharmacy_bill(
    *,
    pharmacy_name: str,
    drug_license: str,
    address: str,
    bill_no: str,
    date: str,
    patient_name: str,
    doctor_name: str,
    medicines: list[tuple[str, str, str, int, float]],  # (name, batch, exp, qty, mrp)
    discount_percent: float = 0.0,
) -> MedicalPDF:
    pdf = MedicalPDF()
    pdf.header_block(
        title=pharmacy_name,
        subtitle=f"Drug Lic. No: {drug_license}",
        address=address,
    )

    pdf.two_col_row("Bill No", bill_no, "Date", date)
    pdf.two_col_row("Patient", patient_name, "Dr.", doctor_name)
    pdf.ln(3)
    pdf.hline()

    col_widths = [55, 20, 20, 12, 20, 25]
    headers = ["MEDICINE", "BATCH", "EXP", "QTY", "MRP", "AMOUNT (Rs.)"]
    for h, w in zip(headers, col_widths):
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(w, 7, h, border=1, fill=True, align="C")
    pdf.set_fill_color(220, 220, 220)
    pdf.ln()

    subtotal = 0.0
    for name, batch, exp, qty, mrp in medicines:
        amt = qty * mrp
        pdf.set_font("Courier", "", 8)
        vals = [name, batch, exp, str(qty), f"{mrp:.2f}", f"{amt:.2f}"]
        for v, w in zip(vals, col_widths):
            pdf.cell(w, 6, v, border=1)
        pdf.ln()
        subtotal += amt

    discount = subtotal * discount_percent / 100
    net = subtotal - discount

    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    cr = 45
    pdf.cell(pdf.INNER_W - cr, 6, "Subtotal:", align="R")
    pdf.cell(cr, 6, f"Rs. {subtotal:.2f}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if discount_percent > 0:
        pdf.cell(pdf.INNER_W - cr, 6, f"Discount ({discount_percent:.0f}%):", align="R")
        pdf.cell(cr, 6, f"-Rs. {discount:.2f}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(pdf.INNER_W - cr, 6, "Net Amount:", align="R")
    pdf.cell(cr, 6, f"Rs. {net:.2f}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(4)
    pdf.hline()
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, "Pharmacist: R. Sharma   [Stamp]   [FSSAI / Drug Inspector Reg.]",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return pdf


def build_lab_report(
    *,
    lab_name: str,
    lab_id: str,
    nabl: bool,
    address: str,
    patient_name: str,
    patient_age: int,
    patient_gender: str,
    referring_doctor: str,
    sample_date: str,
    report_date: str,
    sample_id: str,
    tests: list[tuple[str, str, str, str]],  # (name, result, unit, normal_range)
    remarks: str,
    pathologist_name: str,
    pathologist_reg: str,
) -> MedicalPDF:
    pdf = MedicalPDF()
    pdf.header_block(
        title=lab_name,
        subtitle=f"{'NABL Accredited Lab | ' if nabl else ''}Lab ID: {lab_id}",
        address=address,
    )

    pdf.two_col_row("Patient", patient_name, "Sample ID", sample_id)
    pdf.two_col_row(
        "Age/Sex", f"{patient_age} / {'Male' if patient_gender == 'M' else 'Female'}",
        "Ref Doctor", referring_doctor,
    )
    pdf.two_col_row("Sample Date", sample_date, "Report Date", report_date)
    pdf.ln(3)
    pdf.hline()

    col_w = [65, 30, 25, 40]
    headers = ["TEST NAME", "RESULT", "UNIT", "NORMAL RANGE"]
    for h, w in zip(headers, col_w):
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(220, 220, 220)
        pdf.cell(w, 7, h, border=1, fill=True, align="C")
    pdf.ln()

    for name, result, unit, normal in tests:
        pdf.set_font("Helvetica", "", 8)
        for val, w in zip([name, result, unit, normal], col_w):
            pdf.cell(w, 6, val, border=1)
        pdf.ln()

    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 5, f"Remarks: {remarks}")

    pdf.signature_block(
        name=pathologist_name,
        reg=pathologist_reg,
        role="MD (Pathology)",
    )
    return pdf


def build_dental_bill(
    *,
    clinic_name: str,
    address: str,
    bill_no: str,
    date: str,
    patient_name: str,
    patient_age: int,
    patient_gender: str,
    dentist_name: str,
    dentist_reg: str,
    line_items: list[tuple[str, float, bool]],  # (procedure, amount, covered)
) -> MedicalPDF:
    pdf = MedicalPDF()
    pdf.header_block(
        title=clinic_name,
        subtitle="Multispeciality Dental Clinic | BDS/MDS Registered",
        address=address,
    )

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "DENTAL TREATMENT BILL", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.hline()

    pdf.two_col_row("Bill No", bill_no, "Date", date)
    pdf.field_row("Patient Name", patient_name)
    pdf.two_col_row(
        "Age", f"{patient_age} yrs",
        "Gender", "Male" if patient_gender.upper() == "M" else "Female",
    )
    pdf.field_row("Treating Dentist", f"Dr. {dentist_name}  |  Reg: {dentist_reg}")
    pdf.ln(3)
    pdf.hline()

    col_desc = pdf.INNER_W - 30 - 30
    pdf.table_header([("PROCEDURE", col_desc), ("AMOUNT (Rs.)", 30), ("STATUS", 30)])
    subtotal = 0.0
    for desc, amount, covered in line_items:
        status = "Covered" if covered else "Excluded (Cosmetic)"
        pdf.table_row([(desc, col_desc), (f"{amount:,.2f}", 30), (status, 30)])
        subtotal += amount

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(pdf.INNER_W - 30, 6, "Total Amount:", align="R")
    pdf.cell(30, 6, f"Rs. {subtotal:,.2f}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.signature_block(
        name=f"Dr. {dentist_name}",
        reg=dentist_reg,
        role="BDS, MDS (Prosthodontics)",
    )
    return pdf


# ???????????????????????????????????????????????????????????????????????????????
#  Image helpers (Pillow)
# ???????????????????????????????????????????????????????????????????????????????

def _get_font(size: int):
    """Return a PIL font -- tries to get a real font, falls back to default."""
    try:
        from PIL import ImageFont
        # Try common system fonts
        for name in ("arial.ttf", "DejaVuSans.ttf", "FreeSans.ttf", "LiberationSans-Regular.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except (OSError, IOError):
                continue
    except Exception:
        pass
    return ImageFont.load_default()


def build_image_prescription(
    *,
    patient_name: str,
    doctor_name: str,
    doctor_reg: str,
    diagnosis: str,
    date: str,
    medicines: list[str],
    blur_factor: int = 0,  # 0 = sharp, >0 = apply GaussianBlur radius
) -> "Image.Image":
    """Return a Pillow Image of a prescription -- optionally blurred."""
    W, H = 800, 1050
    img = Image.new("RGB", (W, H), color=(252, 252, 250))
    draw = ImageDraw.Draw(img)

    # Border
    draw.rectangle([(20, 20), (W - 20, H - 20)], outline=(50, 50, 100), width=3)

    # Header
    draw.rectangle([(20, 20), (W - 20, 140)], fill=(50, 50, 100))
    fn_title = _get_font(20)
    fn_sub = _get_font(13)
    fn_body = _get_font(14)

    draw.text((W // 2, 50), f"Dr. {doctor_name}", fill="white", font=fn_title, anchor="mm")
    draw.text((W // 2, 80), "MBBS, MD (Internal Medicine)", fill=(200, 200, 220),
              font=fn_sub, anchor="mm")
    draw.text((W // 2, 105), f"Reg. No: {doctor_reg}", fill=(180, 180, 200),
              font=fn_sub, anchor="mm")
    draw.text((W // 2, 125), "City Medical Centre, 12 MG Road, Bengaluru | Ph: +91-80-XXXXXXXX",
              fill=(180, 180, 200), font=fn_sub, anchor="mm")

    # Divider
    draw.line([(40, 145), (W - 40, 145)], fill=(50, 50, 100), width=2)

    # Patient block
    y = 160
    draw.text((50, y), f"Patient: {patient_name}", fill=(30, 30, 30), font=fn_body)
    draw.text((W - 200, y), f"Date: {date}", fill=(30, 30, 30), font=fn_body)
    y += 30
    draw.text((50, y), "Age: 39 years    Gender: Male    Chief Complaint: As per diagnosis",
              fill=(80, 80, 80), font=fn_sub)
    y += 30
    draw.line([(40, y), (W - 40, y)], fill=(180, 180, 180))

    # Diagnosis
    y += 15
    draw.text((50, y), "Diagnosis:", fill=(80, 40, 40), font=_get_font(15))
    y += 25
    draw.text((70, y), diagnosis, fill=(20, 20, 20), font=fn_body)

    # Rx
    y += 40
    draw.text((50, y), "Rx:", fill=(50, 50, 100), font=_get_font(18))
    y += 30
    for i, med in enumerate(medicines, 1):
        draw.text((70, y), f"{i}. {med}", fill=(30, 30, 30), font=fn_body)
        y += 28

    # Investigations
    y += 10
    draw.line([(40, y), (W - 40, y)], fill=(180, 180, 180))
    y += 15
    draw.text((50, y), "Investigations: CBC, Dengue NS1 Antigen", fill=(60, 60, 60), font=fn_sub)

    # Follow-up
    y += 30
    draw.text((50, y), "Follow-up: After 5 days if no improvement", fill=(60, 60, 60), font=fn_sub)

    # Signature block
    y = H - 140
    draw.line([(40, y), (W - 40, y)], fill=(120, 120, 120))
    y += 15
    draw.text((W - 250, y), f"Dr. {doctor_name}", fill=(30, 30, 30), font=fn_body)
    y += 25
    draw.text((W - 250, y), f"Reg. No: {doctor_reg}", fill=(80, 80, 80), font=fn_sub)
    y += 20
    draw.text((W - 250, y), "[Signature & Stamp]", fill=(140, 140, 140), font=fn_sub)

    if blur_factor > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_factor))

    return img


def build_image_pharmacy_bill(
    *,
    patient_name: str,
    doctor_name: str,
    medicines: list[tuple[str, str, str, int, float]],
    date: str,
    blur_factor: int = 0,
) -> "Image.Image":
    W, H = 800, 900
    img = Image.new("RGB", (W, H), color=(255, 255, 253))
    draw = ImageDraw.Draw(img)

    draw.rectangle([(15, 15), (W - 15, H - 15)], outline=(0, 80, 0), width=3)

    fn_title = _get_font(20)
    fn_body = _get_font(13)
    fn_small = _get_font(11)

    # Header
    draw.rectangle([(15, 15), (W - 15, 120)], fill=(0, 80, 0))
    draw.text((W // 2, 45), "HEALTH FIRST PHARMACY", fill="white", font=fn_title, anchor="mm")
    draw.text((W // 2, 75), "Drug Lic. No: KA-BLR-XXXX", fill=(200, 240, 200),
              font=fn_body, anchor="mm")
    draw.text((W // 2, 100), "22 Brigade Road, Bengaluru | Ph: 080-XXXXXXXX",
              fill=(180, 220, 180), font=fn_small, anchor="mm")

    y = 135
    draw.text((50, y), f"Bill No: HFP-24-0{random.randint(9000,9999)}",
              fill=(30, 30, 30), font=fn_body)
    draw.text((W - 250, y), f"Date: {date}", fill=(30, 30, 30), font=fn_body)
    y += 28
    draw.text((50, y), f"Patient: {patient_name}    Dr: Dr. {doctor_name}",
              fill=(30, 30, 30), font=fn_body)
    y += 20
    draw.line([(30, y), (W - 30, y)], fill=(0, 80, 0), width=2)
    y += 12

    # Table header
    cols = [200, 100, 80, 60, 90, 110]
    headers = ["MEDICINE", "BATCH", "EXP", "QTY", "MRP", "AMOUNT"]
    x = 40
    draw.rectangle([(30, y), (W - 30, y + 28)], fill=(220, 240, 220))
    for h, cw in zip(headers, cols):
        draw.text((x + cw // 2, y + 14), h, fill=(0, 60, 0), font=fn_small, anchor="mm")
        x += cw
    y += 30

    subtotal = 0.0
    for name, batch, exp, qty, mrp in medicines:
        amt = qty * mrp
        x = 40
        vals = [name, batch, exp, str(qty), f"{mrp:.2f}", f"{amt:.2f}"]
        for val, cw in zip(vals, cols):
            draw.text((x + 5, y + 8), val, fill=(20, 20, 20), font=fn_small)
            draw.line([(x, y + 26), (x + cw, y + 26)], fill=(200, 200, 200))
            x += cw
        y += 28
        subtotal += amt

    y += 10
    draw.line([(30, y), (W - 30, y)], fill=(0, 80, 0), width=1)
    y += 12
    draw.text((W - 200, y), f"Subtotal:    Rs. {subtotal:.2f}", fill=(30, 30, 30), font=fn_body)
    y += 26
    draw.text((W - 200, y), f"Discount (5%): -Rs. {subtotal * 0.05:.2f}", fill=(30, 30, 30), font=fn_body)
    y += 26
    draw.text((W - 200, y), f"Net Amount:   Rs. {subtotal * 0.95:.2f}", fill=(0, 80, 0),
              font=_get_font(15))

    y += 50
    draw.line([(30, y), (W - 30, y)], fill=(180, 180, 180))
    y += 12
    draw.text((50, y), "Pharmacist: R. Sharma   [Stamp]", fill=(80, 80, 80), font=fn_small)

    if blur_factor > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_factor))

    return img


# ???????????????????????????????????????????????????????????????????????????????
#  Per-test-case generators
# ???????????????????????????????????????????????????????????????????????????????

def gen_tc001(out: Path):
    """TC001 -- Wrong Document: two prescriptions instead of prescription + bill."""
    d = out / "TC001_wrong_document"
    d.mkdir(parents=True, exist_ok=True)

    # F001 -- Prescription 1 (correct prescription)
    pdf = build_prescription(
        doctor_name="Arun Sharma",
        doctor_reg="KA/45678/2015",
        doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre",
        clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar",
        patient_age=39,
        patient_gender="M",
        date="01-Nov-2024",
        diagnosis="Viral Fever with body ache",
        medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days",
                   "Tab Vitamin C 500mg -- 0-0-1 x 7 days",
                   "Tab Cetirizine 10mg -- 0-0-1 x 3 days (if sneezing)"],
        tests=["CBC", "Dengue NS1 Antigen"],
        followup="After 5 days if no improvement",
    )
    pdf.output(str(d / "F001_prescription_rajesh.pdf"))
    print(f"  [OK] {d.name}/F001_prescription_rajesh.pdf")

    # F002 -- Second prescription (wrong! should be a hospital bill)
    pdf = build_prescription(
        doctor_name="Meera Krishnan",
        doctor_reg="KA/78901/2019",
        doctor_specialization="MBBS (General Physician)",
        clinic_name="MediCare Clinic",
        clinic_address="45 Residency Road, Bengaluru - 560025",
        patient_name="Rajesh Kumar",
        patient_age=39,
        patient_gender="M",
        date="01-Nov-2024",
        diagnosis="Viral Fever -- second opinion",
        medicines=["Tab Dolo 650 -- 1-1-1 x 5 days",
                   "Tab Allegra 120mg -- 0-0-1 x 5 days"],
        notes="This is a duplicate prescription (wrong document -- no hospital bill provided)",
    )
    pdf.output(str(d / "F002_prescription_duplicate.pdf"))
    print(f"  [OK] {d.name}/F002_prescription_duplicate.pdf")


def gen_tc002(out: Path):
    """TC002 -- Unreadable pharmacy bill."""
    d = out / "TC002_unreadable_document"
    d.mkdir(parents=True, exist_ok=True)

    # F003 -- Good quality prescription (JPG)
    if HAS_PIL:
        img = build_image_prescription(
            patient_name="Sneha Reddy",
            doctor_name="Shalini Iyer",
            doctor_reg="KA/33201/2017",
            diagnosis="Acute Pharyngitis (Sore Throat)",
            date="25-Oct-2024",
            medicines=["Tab Amoxicillin 500mg -- 1-0-1 x 7 days",
                       "Tab Ibuprofen 400mg -- 1-0-1 x 3 days",
                       "Lozenges Strepsils -- SOS x 5 days"],
            blur_factor=0,
        )
        img.save(str(d / "F003_prescription_sneha.jpg"), quality=92)
        print(f"  [OK] {d.name}/F003_prescription_sneha.jpg  (GOOD quality)")
    else:
        # Fallback PDF
        pdf = build_prescription(
            doctor_name="Shalini Iyer",
            doctor_reg="KA/33201/2017",
            doctor_specialization="MBBS (General Physician)",
            clinic_name="Apollo Clinic",
            clinic_address="Jayanagar, Bengaluru",
            patient_name="Sneha Reddy",
            patient_age=32,
            patient_gender="F",
            date="25-Oct-2024",
            diagnosis="Acute Pharyngitis",
            medicines=["Tab Amoxicillin 500mg", "Tab Ibuprofen 400mg"],
        )
        pdf.output(str(d / "F003_prescription_sneha.pdf"))
        print(f"  [OK] {d.name}/F003_prescription_sneha.pdf  (GOOD quality)")

    # F004 -- Blurry pharmacy bill (JPG, deliberately blurred)
    if HAS_PIL:
        img = build_image_pharmacy_bill(
            patient_name="Sneha Reddy",
            doctor_name="Shalini Iyer",
            medicines=[
                ("Amoxicillin 500mg", "AMX221", "08/26", 14, 12.50),
                ("Ibuprofen 400mg", "IBU891", "04/26", 6, 6.00),
                ("Strepsils Lozenges", "STR003", "12/25", 12, 8.00),
            ],
            date="25-Oct-2024",
            blur_factor=6,   # strong blur -> UNREADABLE
        )
        img.save(str(d / "F004_blurry_pharmacy_bill.jpg"), quality=40)
        print(f"  [OK] {d.name}/F004_blurry_pharmacy_bill.jpg  (UNREADABLE -- blurred)")
    else:
        # Minimal PDF with a note
        pdf = MedicalPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "HEALTH FIRST PHARMACY", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, "[SIMULATED UNREADABLE DOCUMENT -- image would be blurred]",
                 align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.output(str(d / "F004_blurry_pharmacy_bill.pdf"))
        print(f"  [OK] {d.name}/F004_blurry_pharmacy_bill.pdf  (UNREADABLE placeholder)")


def gen_tc003(out: Path):
    """TC003 -- Patient mismatch: prescription for Rajesh, bill for Arjun Mehta."""
    d = out / "TC003_patient_mismatch"
    d.mkdir(parents=True, exist_ok=True)

    # F005 -- Prescription for RAJESH KUMAR
    pdf = build_prescription(
        doctor_name="Arun Sharma",
        doctor_reg="KA/45678/2015",
        doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre",
        clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar",
        patient_age=39,
        patient_gender="M",
        date="01-Nov-2024",
        diagnosis="Viral Fever",
        medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days",
                   "Tab Vitamin C 500mg -- 0-0-1 x 7 days"],
        tests=["CBC"],
    )
    pdf.output(str(d / "F005_prescription_rajesh.pdf"))
    print(f"  [OK] {d.name}/F005_prescription_rajesh.pdf  (Patient: RAJESH KUMAR)")

    # F006 -- Hospital bill for ARJUN MEHTA (different patient!)
    pdf = build_hospital_bill(
        hospital_name="City Medical Centre",
        hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX",
        bill_no="CMC/2024/08321",
        date="01-Nov-2024",
        patient_name="Arjun Mehta",     # <- different patient
        patient_age=35,
        patient_gender="M",
        referring_doctor="Dr. Prashant Gupta",
        line_items=[
            ("Consultation Fee (OPD)", 1000.0),
            ("CBC (Complete Blood Count)", 200.0),
            ("Dengue NS1 Antigen Test", 300.0),
        ],
    )
    pdf.output(str(d / "F006_bill_arjun.pdf"))
    print(f"  [OK] {d.name}/F006_bill_arjun.pdf  (Patient: ARJUN MEHTA -- MISMATCH)")


def gen_tc004(out: Path):
    """TC004 -- Clean consultation, full approval."""
    d = out / "TC004_clean_consultation"
    d.mkdir(parents=True, exist_ok=True)

    pdf = build_prescription(
        doctor_name="Arun Sharma",
        doctor_reg="KA/45678/2015",
        doctor_specialization="MBBS, MD (Internal Medicine)",
        clinic_name="City Medical Centre",
        clinic_address="12 MG Road, Bengaluru - 560001",
        patient_name="Rajesh Kumar",
        patient_age=39,
        patient_gender="M",
        date="01-Nov-2024",
        diagnosis="Viral Fever",
        medicines=["Tab Paracetamol 650mg -- 1-1-1 x 5 days",
                   "Tab Vitamin C 500mg -- 0-0-1 x 7 days"],
        tests=["CBC", "Dengue NS1"],
    )
    pdf.output(str(d / "F007_prescription_rajesh.pdf"))
    print(f"  [OK] {d.name}/F007_prescription_rajesh.pdf")

    pdf = build_hospital_bill(
        hospital_name="City Clinic, Bengaluru",
        hospital_address="12 MG Road, Bengaluru - 560001",
        gstin="29AAACC1234C1ZX",
        bill_no="CMC/2024/08322",
        date="01-Nov-2024",
        patient_name="Rajesh Kumar",
        patient_age=39,
        patient_gender="M",
        referring_doctor="Dr. Arun Sharma",
        line_items=[
            ("Consultation Fee (OPD)", 1000.0),
            ("CBC (Complete Blood Count)", 300.0),
            ("Dengue NS1 Antigen Test", 200.0),
        ],
    )
    pdf.output(str(d / "F008_hospital_bill_rajesh.pdf"))
    print(f"  [OK] {d.name}/F008_hospital_bill_rajesh.pdf")


def gen_tc005(out: Path):
    """TC005 -- Waiting period for diabetes."""
    d = out / "TC005_waiting_period_diabetes"
    d.mkdir(parents=True, exist_ok=True)

    pdf = build_prescription(
        doctor_name="Sunil Mehta",
        doctor_reg="GJ/56789/2014",
        doctor_specialization="MBBS, MD (Endocrinology)",
        clinic_name="Mehta Endocrine Clinic",
        clinic_address="Navrangpura, Ahmedabad - 380009",
        patient_name="Vikram Joshi",
        patient_age=45,
        patient_gender="M",
        date="15-Oct-2024",
        diagnosis="Type 2 Diabetes Mellitus (newly diagnosed)",
        medicines=["Tab Metformin 500mg -- 0-0-1 x 30 days",
                   "Tab Glimepiride 1mg -- 1-0-0 x 30 days",
                   "Acarbose 25mg -- 1-1-1 x 30 days"],
        notes="Lifestyle modification advised. Low glycemic index diet.",
    )
    pdf.output(str(d / "F009_prescription_vikram.pdf"))
    print(f"  [OK] {d.name}/F009_prescription_vikram.pdf  (Diagnosis: T2DM)")

    pdf = build_hospital_bill(
        hospital_name="Mehta Endocrine Clinic",
        hospital_address="Navrangpura, Ahmedabad - 380009",
        gstin="24AAACM1234A1ZX",
        bill_no="MEC/2024/01234",
        date="15-Oct-2024",
        patient_name="Vikram Joshi",
        patient_age=45,
        patient_gender="M",
        referring_doctor="Dr. Sunil Mehta",
        line_items=[
            ("Consultation Fee", 1000.0),
            ("HbA1c Blood Test", 800.0),
            ("Fasting / PP Blood Sugar", 400.0),
            ("Lipid Profile", 600.0),
            ("Medicines (Metformin / Glimepiride)", 200.0),
        ],
    )
    pdf.output(str(d / "F010_hospital_bill_vikram.pdf"))
    print(f"  [OK] {d.name}/F010_hospital_bill_vikram.pdf")


def gen_tc006(out: Path):
    """TC006 -- Dental partial: root canal (covered) + whitening (excluded)."""
    d = out / "TC006_dental_partial_cosmetic"
    d.mkdir(parents=True, exist_ok=True)

    pdf = build_dental_bill(
        clinic_name="Smile Dental Clinic",
        address="HSR Layout, Bengaluru - 560102",
        bill_no="SDC/2024/04521",
        date="15-Oct-2024",
        patient_name="Priya Singh",
        patient_age=34,
        patient_gender="F",
        dentist_name="Kavitha Rao",
        dentist_reg="KA/44123/2016",
        line_items=[
            ("Root Canal Treatment (Molar #36)", 8000.0, True),
            ("Teeth Whitening -- Laser (Cosmetic)", 4000.0, False),
        ],
    )
    pdf.output(str(d / "F011_dental_bill_priya.pdf"))
    print(f"  [OK] {d.name}/F011_dental_bill_priya.pdf  (Mixed: covered + cosmetic)")


def gen_tc007(out: Path):
    """TC007 -- MRI without pre-authorization."""
    d = out / "TC007_mri_no_preauth"
    d.mkdir(parents=True, exist_ok=True)

    pdf = build_prescription(
        doctor_name="Venkat Rao",
        doctor_reg="AP/67890/2017",
        doctor_specialization="MBBS, MS (Orthopaedics)",
        clinic_name="Orthopedic Specialty Centre",
        clinic_address="Banjara Hills, Hyderabad - 500034",
        patient_name="Suresh Patil",
        patient_age=49,
        patient_gender="M",
        date="02-Nov-2024",
        diagnosis="Suspected Lumbar Disc Herniation (L4-L5) with radiculopathy",
        medicines=["Tab Etoricoxib 90mg -- 0-0-1 x 7 days",
                   "Tab Pregabalin 75mg -- 0-0-1 x 7 days",
                   "Physio: Hot fomentation x 10 days"],
        tests=["MRI Lumbar Spine (with contrast)", "X-Ray Lumbar AP & Lateral"],
        notes="MRI REQUIRED for surgical planning. Pre-auth NOT obtained -- patient emergency.",
    )
    pdf.output(str(d / "F012_prescription_suresh.pdf"))
    print(f"  [OK] {d.name}/F012_prescription_suresh.pdf")

    pdf = build_lab_report(
        lab_name="Precision Diagnostics Pvt Ltd",
        lab_id="TS-NABL-5678",
        nabl=True,
        address="Banjara Hills, Hyderabad | Ph: 040-XXXXXXXX",
        patient_name="Suresh Patil",
        patient_age=49,
        patient_gender="M",
        referring_doctor="Dr. Venkat Rao",
        sample_date="02-Nov-2024",
        report_date="02-Nov-2024",
        sample_id="PD-2024-MRI-3391",
        tests=[
            ("MRI Lumbar Spine", "L4-L5 disc herniation with nerve root compression",
             "--", "Normal disc height"),
            ("Impression", "Grade 2 disc prolapse at L4-L5 level", "--", "--"),
        ],
        remarks="Clinical correlation advised. Surgical consultation recommended.",
        pathologist_name="Dr. Rajesh Gowda",
        pathologist_reg="TS/90123/2014",
    )
    pdf.output(str(d / "F013_mri_lab_report.pdf"))
    print(f"  [OK] {d.name}/F013_mri_lab_report.pdf")

    pdf = build_hospital_bill(
        hospital_name="Apollo Diagnostics",
        hospital_address="Banjara Hills, Hyderabad - 500034",
        gstin="36AAACC9876B1ZX",
        bill_no="APD/2024/11102",
        date="02-Nov-2024",
        patient_name="Suresh Patil",
        patient_age=49,
        patient_gender="M",
        referring_doctor="Dr. Venkat Rao",
        line_items=[
            ("MRI Lumbar Spine (with contrast)", 15000.0),
        ],
    )
    pdf.output(str(d / "F014_mri_hospital_bill.pdf"))
    print(f"  [OK] {d.name}/F014_mri_hospital_bill.pdf  (Rs.15,000 > pre-auth threshold)")


def gen_tc008(out: Path):
    """TC008 -- Per-claim limit exceeded."""
    d = out / "TC008_per_claim_limit"
    d.mkdir(parents=True, exist_ok=True)

    pdf = build_prescription(
        doctor_name="R. Gupta",
        doctor_reg="DL/34567/2016",
        doctor_specialization="MBBS (General Physician)",
        clinic_name="Capital Medical Clinic",
        clinic_address="Connaught Place, New Delhi - 110001",
        patient_name="Amit Verma",
        patient_age=36,
        patient_gender="M",
        date="20-Oct-2024",
        diagnosis="Acute Gastroenteritis with dehydration",
        medicines=["Inj. Metronidazole 400mg IV -- 1-0-1 x 3 days",
                   "Tab Norfloxacin 400mg -- 1-0-1 x 5 days",
                   "ORS 1L -- Q6H x 3 days",
                   "Tab Probiotics -- 0-0-1 x 7 days"],
    )
    pdf.output(str(d / "F015_prescription_amit.pdf"))
    print(f"  [OK] {d.name}/F015_prescription_amit.pdf")

    pdf = build_hospital_bill(
        hospital_name="Capital Medical Clinic",
        hospital_address="Connaught Place, New Delhi - 110001",
        gstin="07AAACC7654D1ZX",
        bill_no="CMC-DL/2024/05511",
        date="20-Oct-2024",
        patient_name="Amit Verma",
        patient_age=36,
        patient_gender="M",
        referring_doctor="Dr. R. Gupta",
        line_items=[
            ("Consultation Fee", 2000.0),
            ("Medicines (Metronidazole / Norfloxacin / ORS / Probiotics)", 5500.0),
        ],
    )
    pdf.output(str(d / "F016_hospital_bill_amit.pdf"))
    print(f"  [OK] {d.name}/F016_hospital_bill_amit.pdf  (Total Rs.7,500 > per-claim Rs.5,000)")


def gen_tc009(out: Path):
    """TC009 -- Fraud: multiple same-day claims (4th claim)."""
    d = out / "TC009_fraud_same_day"
    d.mkdir(parents=True, exist_ok=True)

    pdf = build_prescription(
        doctor_name="S. Khan",
        doctor_reg="MH/23456/2018",
        doctor_specialization="MBBS (General Physician)",
        clinic_name="Khan Medical Clinic",
        clinic_address="Bandra, Mumbai - 400050",
        patient_name="Ravi Menon",
        patient_age=37,
        patient_gender="M",
        date="30-Oct-2024",
        diagnosis="Migraine with aura (acute attack)",
        medicines=["Tab Sumatriptan 50mg -- STAT (1 tablet now)",
                   "Tab Domperidone 10mg -- STAT",
                   "Lie in a dark quiet room"],
        notes="4th consultation today. Patient visited multiple clinics. POSSIBLE FRAUD SIGNAL.",
    )
    pdf.output(str(d / "F017_prescription_ravi.pdf"))
    print(f"  [OK] {d.name}/F017_prescription_ravi.pdf  (4th same-day claim)")

    pdf = build_hospital_bill(
        hospital_name="Khan Medical Clinic",
        hospital_address="Bandra, Mumbai - 400050",
        gstin="27AAACK4321F1ZX",
        bill_no="KMC/2024/00491",
        date="30-Oct-2024",
        patient_name="Ravi Menon",
        patient_age=37,
        patient_gender="M",
        referring_doctor="Dr. S. Khan",
        line_items=[
            ("Consultation Fee", 1500.0),
            ("Sumatriptan 50mg", 200.0),
            ("Domperidone 10mg", 80.0),
            ("Nursing charges", 3020.0),
        ],
    )
    pdf.output(str(d / "F018_hospital_bill_ravi.pdf"))
    print(f"  [OK] {d.name}/F018_hospital_bill_ravi.pdf")


def gen_tc010(out: Path):
    """TC010 -- Network hospital: Apollo (20% discount then 10% copay)."""
    d = out / "TC010_network_hospital_discount"
    d.mkdir(parents=True, exist_ok=True)

    pdf = build_prescription(
        doctor_name="S. Iyer",
        doctor_reg="TN/56789/2013",
        doctor_specialization="MBBS, MD (Pulmonology)",
        clinic_name="Apollo Hospitals",
        clinic_address="Greams Road, Chennai - 600006",
        patient_name="Deepak Shah",
        patient_age=44,
        patient_gender="M",
        date="03-Nov-2024",
        diagnosis="Acute Bronchitis with wheeze",
        medicines=["Tab Amoxicillin 500mg -- 1-0-1 x 7 days",
                   "Salbutamol Inhaler -- 2 puffs Q6H x 5 days",
                   "Tab Montelukast 10mg -- 0-0-1 x 7 days"],
    )
    pdf.output(str(d / "F019_prescription_deepak.pdf"))
    print(f"  [OK] {d.name}/F019_prescription_deepak.pdf")

    pdf = build_hospital_bill(
        hospital_name="Apollo Hospitals",
        hospital_address="Greams Road, Chennai - 600006",
        gstin="33AAACC5555G1ZX",
        bill_no="APL/2024/88123",
        date="03-Nov-2024",
        patient_name="Deepak Shah",
        patient_age=44,
        patient_gender="M",
        referring_doctor="Dr. S. Iyer",
        line_items=[
            ("Consultation Fee (Specialist)", 1500.0),
            ("Medicines (Amoxicillin / Salbutamol / Montelukast)", 3000.0),
        ],
    )
    pdf.output(str(d / "F020_hospital_bill_apollo.pdf"))
    print(f"  [OK] {d.name}/F020_hospital_bill_apollo.pdf  (Apollo = network: 20% disc then 10% copay)")


def gen_tc011(out: Path):
    """TC011 -- Component failure (graceful degradation), Ayurvedic claim."""
    d = out / "TC011_graceful_degradation"
    d.mkdir(parents=True, exist_ok=True)

    pdf = build_prescription(
        doctor_name="Vaidya T. Krishnan",
        doctor_reg="AYUR/KL/2345/2019",
        doctor_specialization="BAMS (Ayurveda), MD (Kayachikitsa)",
        clinic_name="Ayur Wellness Centre",
        clinic_address="Thrissur, Kerala - 680001",
        patient_name="Kavita Nair",
        patient_age=41,
        patient_gender="F",
        date="28-Oct-2024",
        diagnosis="Chronic Joint Pain -- Amavata (Rheumatoid Arthritis equivalent)",
        medicines=["Panchakarma Therapy -- 5 sessions",
                   "Maharasnadi Kashayam -- 15ml twice daily",
                   "Rasnadi Guggulu -- 2 tabs twice daily",
                   "Abhyanga (medicated oil massage) -- daily x 7 days"],
        notes="AYUSH registered practitioner. Kerala Ayurveda Council registered.",
        followup="After 5 sessions of Panchakarma",
    )
    pdf.output(str(d / "F021_prescription_ayurveda.pdf"))
    print(f"  [OK] {d.name}/F021_prescription_ayurveda.pdf  (AYUR reg, Panchakarma)")

    pdf = build_hospital_bill(
        hospital_name="Ayur Wellness Centre",
        hospital_address="Thrissur, Kerala - 680001",
        gstin="32AAACC8765K1ZX",
        bill_no="AWC/2024/03312",
        date="28-Oct-2024",
        patient_name="Kavita Nair",
        patient_age=41,
        patient_gender="F",
        referring_doctor="Vaidya T. Krishnan",
        line_items=[
            ("Panchakarma Therapy -- 5 sessions", 3000.0),
            ("Consultation (Vaidya)", 1000.0),
        ],
    )
    pdf.output(str(d / "F022_hospital_bill_ayurwellness.pdf"))
    print(f"  [OK] {d.name}/F022_hospital_bill_ayurwellness.pdf  (simulate_component_failure=true)")


def gen_tc012(out: Path):
    """TC012 -- Excluded treatment: obesity / bariatric."""
    d = out / "TC012_excluded_treatment"
    d.mkdir(parents=True, exist_ok=True)

    pdf = build_prescription(
        doctor_name="P. Banerjee",
        doctor_reg="WB/34567/2015",
        doctor_specialization="MBBS, MS (General Surgery)",
        clinic_name="AMRI Hospitals",
        clinic_address="Dhakuria, Kolkata - 700029",
        patient_name="Anita Desai",
        patient_age=31,
        patient_gender="F",
        date="18-Oct-2024",
        diagnosis="Morbid Obesity -- BMI 37 kg/m²",
        medicines=["Bariatric Consultation -- pre-surgical evaluation",
                   "Customised Diet and Nutrition Program (6 months)"],
        notes="Patient referred for bariatric surgery evaluation. EXCLUDED under group policy.",
    )
    pdf.output(str(d / "F023_prescription_bariatric.pdf"))
    print(f"  [OK] {d.name}/F023_prescription_bariatric.pdf  (Excluded: obesity treatment)")

    pdf = build_hospital_bill(
        hospital_name="AMRI Hospitals",
        hospital_address="Dhakuria, Kolkata - 700029",
        gstin="19AAACC3210W1ZX",
        bill_no="AMRI/2024/07891",
        date="18-Oct-2024",
        patient_name="Anita Desai",
        patient_age=31,
        patient_gender="F",
        referring_doctor="Dr. P. Banerjee",
        line_items=[
            ("Bariatric Consultation (Pre-surgical Evaluation)", 3000.0),
            ("Personalised Diet and Nutrition Program", 5000.0),
        ],
    )
    pdf.output(str(d / "F024_hospital_bill_bariatric.pdf"))
    print(f"  [OK] {d.name}/F024_hospital_bill_bariatric.pdf  (Excluded: obesity/bariatric)")


def gen_extras(out: Path):
    """EXTRAS -- Generic example documents illustrating the sample_documents_guide layouts."""
    d = out / "EXTRAS"
    d.mkdir(parents=True, exist_ok=True)

    # Generic lab report (dengue / CBC -- exactly matches sample guide)
    pdf = build_lab_report(
        lab_name="Precision Diagnostics Pvt Ltd",
        lab_id="KA-NABL-1234",
        nabl=True,
        address="45 Jayanagar, Bengaluru | Ph: 080-XXXXXXXX",
        patient_name="Rajesh Kumar",
        patient_age=39,
        patient_gender="M",
        referring_doctor="Dr. Arun Sharma",
        sample_date="01-Nov-2024",
        report_date="01-Nov-2024",
        sample_id="PD-2024-18723",
        tests=[
            ("CBC -- Hemoglobin", "13.2", "g/dL", "13.0 - 17.0"),
            ("CBC -- WBC Count", "9,800", "/uL", "4,500 - 11,000"),
            ("CBC -- Platelet Count", "185,000", "/uL", "150,000 - 450,000"),
            ("Dengue NS1 Antigen", "NEGATIVE", "--", "--"),
        ],
        remarks=(
            "WBC count is towards upper normal limit. Clinical correlation advised. "
            "No dengue antigen detected -- dengue unlikely at this stage. Repeat if symptoms persist."
        ),
        pathologist_name="Dr. Meena Pillai",
        pathologist_reg="KA/89012/2018",
    )
    pdf.output(str(d / "lab_report_dengue_cbc.pdf"))
    print(f"  [OK] {d.name}/lab_report_dengue_cbc.pdf  (Generic lab report from guide)")

    # Generic pharmacy bill (exactly matches sample guide)
    pdf = build_pharmacy_bill(
        pharmacy_name="Health First Pharmacy",
        drug_license="KA-BLR-XXXX",
        address="22 Brigade Road, Bengaluru | Ph: 080-XXXXXXXX",
        bill_no="HFP-24-09821",
        date="01-Nov-2024",
        patient_name="Rajesh Kumar",
        doctor_name="Dr. Arun Sharma",
        medicines=[
            ("Paracetamol 650mg", "A2341", "03/26", 15, 2.50),
            ("Vitamin C 500mg", "B7821", "06/26", 10, 4.00),
        ],
        discount_percent=5.0,
    )
    pdf.output(str(d / "pharmacy_bill_standard.pdf"))
    print(f"  [OK] {d.name}/pharmacy_bill_standard.pdf  (Standard pharmacy bill from guide)")

    # Handwritten-style prescription (JPG with rough styling)
    if HAS_PIL:
        img = build_image_prescription(
            patient_name="Ramesh Nair",
            doctor_name="V. Krishnaswamy",
            doctor_reg="KL/78901/2012",
            diagnosis="HTN -- Hypertension, Grade 1",
            date="15-Oct-2024",
            medicines=["Tab Amlodipine 5mg -- 0-0-1 x 30 days",
                       "Tab Telmisartan 40mg -- 1-0-0 x 30 days"],
            blur_factor=0,
        )
        img.save(str(d / "handwritten_style_prescription.jpg"), quality=85)
        print(f"  [OK] {d.name}/handwritten_style_prescription.jpg  (Clear JPG Rx)")
    else:
        print(f"  [SKIP] Skipping JPG extras -- Pillow not available")

    # Multilingual prescription (Hindi/English mix -- PDF)
    pdf = MedicalPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "Dr. Ramesh Chandra Mishra", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "MBBS, MD | Reg. No: UP/45678/2016", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 5, "Shri Ram Medical Centre, Lucknow, UP | Ph: +91-522-XXXXXXXX",
             align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.hline(0.5)

    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, "Patient (Rogi): Ramesh Nair     Date (Tarikh): 15-Oct-2024",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, "Age (Aayu): 45 yrs   Gender (Ling): Male (Purush)",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.hline()

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Diagnosis / Niraan: Ucch Raktachap (Hypertension - Grade 1)",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    pdf.cell(0, 6, "Rx (Davai):", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Courier", "", 9)
    for line in ["1. Tab Amlodipine 5mg -- Raat ko ek (0-0-1) x 30 din",
                 "2. Tab Telmisartan 40mg -- Subah ek (1-0-0) x 30 din",
                 "3. Namak kam karen (Low salt diet)",
                 "4. Paidal chalna -- 30 min daily"]:
        pdf.cell(0, 6, f"   {line}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 5, "Note: Field names shown in Hindi (transliterated) -- common in UP/Bihar clinics.",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 5, "English medicine names kept as-is (standard practice).",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.signature_block("Dr. Ramesh Chandra Mishra", "UP/45678/2016", "MBBS, MD")
    pdf.output(str(d / "multilingual_prescription_hi_en.pdf"))
    print(f"  [OK] {d.name}/multilingual_prescription_hi_en.pdf  (Hindi/English mix)")


# ???????????????????????????????????????????????????????????????????????????????
#  README inside sample_documents/
# ???????????????????????????????????????????????????????????????????????????????

README_CONTENT = """\
# Sample Documents for Claims Testing

Generated by `generate_samples.py` from `sample_documents_guide.md` and `test_cases.json`.

## Structure

| Folder | Test Case | Purpose |
|--------|-----------|---------|
| TC001_wrong_document/ | TC001 | Two prescriptions submitted for a CONSULTATION claim (missing hospital bill) |
| TC002_unreadable_document/ | TC002 | Good prescription + deliberately blurred/unreadable pharmacy bill |
| TC003_patient_mismatch/ | TC003 | Prescription for Rajesh Kumar + bill for Arjun Mehta (different patients) |
| TC004_clean_consultation/ | TC004 | Correct docs, valid member, within all limits -> APPROVED (Rs.1,350 after 10% copay) |
| TC005_waiting_period_diabetes/ | TC005 | Diabetes claim within 90-day waiting period -> REJECTED |
| TC006_dental_partial_cosmetic/ | TC006 | Root canal (covered) + teeth whitening (excluded) -> PARTIAL |
| TC007_mri_no_preauth/ | TC007 | MRI Rs.15,000 without pre-authorization -> REJECTED |
| TC008_per_claim_limit/ | TC008 | Rs.7,500 claim exceeds per-claim limit of Rs.5,000 -> REJECTED |
| TC009_fraud_same_day/ | TC009 | 4th claim on the same day from same member -> MANUAL_REVIEW |
| TC010_network_hospital_discount/ | TC010 | Apollo (network): 20% discount then 10% copay -> APPROVED Rs.3,240 |
| TC011_graceful_degradation/ | TC011 | Ayurvedic claim with simulated component failure -> APPROVED (lower confidence) |
| TC012_excluded_treatment/ | TC012 | Bariatric/obesity consultation (excluded) -> REJECTED |
| EXTRAS/ | -- | Generic examples matching sample_documents_guide.md layouts |

## Key Documents

### TC001 -- Wrong Document
- `F001_prescription_rajesh.pdf` -- correct prescription
- `F002_prescription_duplicate.pdf` -- **wrong**: second prescription instead of a hospital bill

### TC002 -- Unreadable Document
- `F003_prescription_sneha.jpg` -- GOOD quality, readable
- `F004_blurry_pharmacy_bill.jpg` -- **UNREADABLE**: intentionally blurred (GaussianBlur r=6, JPEG quality=40)

### TC003 -- Patient Mismatch
- `F005_prescription_rajesh.pdf` -- patient: **Rajesh Kumar**
- `F006_bill_arjun.pdf` -- patient: **Arjun Mehta** (different person -> mismatch)

### TC006 -- Dental Partial
- `F011_dental_bill_priya.pdf` -- Root Canal (Rs.8,000, covered) + Teeth Whitening (Rs.4,000, cosmetic/excluded)

### TC007 -- MRI pre-auth
- `F014_mri_hospital_bill.pdf` -- MRI Lumbar Spine Rs.15,000 (above Rs.10,000 pre-auth threshold)

### TC010 -- Network Discount
- `F020_hospital_bill_apollo.pdf` -- hospital name is "Apollo Hospitals" (listed in policy network_hospitals)
- Calculation: Rs.4,500 * 80% (network) = Rs.3,600 -> Rs.3,600 * 90% (copay) = **Rs.3,240 approved**

## Regenerating

```bash
# From repo root:
pip install fpdf2 Pillow
python sample_documents/generate_samples.py
```

## Notes
- All patient data is fictional
- Doctor registration numbers follow real state-specific formats (KA, GJ, AP, etc.)
- GSTINs are plausible but not real
- The blurred `F004` JPG simulates a real "phone photo of bill" scenario
- `EXTRAS/multilingual_prescription_hi_en.pdf` simulates a common UP/Bihar-clinic style
"""


# ???????????????????????????????????????????????????????????????????????????????
#  Main
# ???????????????????????????????????????????????????????????????????????????????

def main():
    if not HAS_FPDF:
        print("ERROR: fpdf2 is required. Run: pip install fpdf2")
        return
    if not HAS_PIL:
        print("WARNING: Pillow not found. JPG images will be skipped or replaced with PDFs.")
        print("Install with: pip install Pillow")

    print(f"\nGenerating sample documents -> {OUT_DIR.resolve()}\n")

    generators = [
        ("TC001", gen_tc001),
        ("TC002", gen_tc002),
        ("TC003", gen_tc003),
        ("TC004", gen_tc004),
        ("TC005", gen_tc005),
        ("TC006", gen_tc006),
        ("TC007", gen_tc007),
        ("TC008", gen_tc008),
        ("TC009", gen_tc009),
        ("TC010", gen_tc010),
        ("TC011", gen_tc011),
        ("TC012", gen_tc012),
        ("EXTRAS", gen_extras),
    ]

    for name, fn in generators:
        print(f"\n-- {name} --------------------------------------")
        try:
            fn(OUT_DIR)
        except Exception as exc:
            print(f"  [FAIL] {name} FAILED: {exc}")
            import traceback
            traceback.print_exc()

    # Write README
    readme_path = OUT_DIR / "README.md"
    readme_path.write_text(README_CONTENT, encoding="utf-8")
    print(f"\n-- README --------------------------------------")
    print(f"  [OK] README.md")

    total = sum(1 for _ in OUT_DIR.rglob("*.pdf")) + sum(1 for _ in OUT_DIR.rglob("*.jpg"))
    print(f"\n{'-'*55}")
    print(f"  [DONE] Done -- {total} files generated in {OUT_DIR.resolve()}")
    print(f"{'-'*55}\n")


if __name__ == "__main__":
    main()
