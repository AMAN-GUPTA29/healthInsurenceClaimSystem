"""
Core synthetic-document builder library used by generate_test_documents.py.

Built on ReportLab (PDF drawing) + Pillow (image degradation for
handwritten/blurry/phone-photo style documents, later embedded into a PDF
page). Nothing here is part of the claims-processing application — this is
throwaway test-data tooling only, run from an isolated venv (see
test_documents/README.md "Regenerating these documents").

Every document gets a small, non-interfering footer:
    "SYNTHETIC TEST DOCUMENT — NOT A REAL MEDICAL RECORD"
placed well below the last content line so it never overlaps a field a
document-extraction model would read.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = A4  # points (72/inch); A4 ~ 595 x 842 pt
MARGIN = 15 * mm
INNER_W = PAGE_W - 2 * MARGIN

FOOTER_TEXT = "SYNTHETIC TEST DOCUMENT — NOT A REAL MEDICAL RECORD"


# ─────────────────────────────────────────────────────────────────────────
#  Manifest collection (populated as documents are built, written once at
#  the end by generate_test_documents.py into TEST_MANIFEST.md)
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ManifestEntry:
    test_case: str
    file_id: str
    filename: str
    document_type: str
    patient: str
    purpose: str
    important_fields: str
    expected_classification: str
    expected_quality: str = "GOOD"
    phase2a_note: str = "—"
    phase2b_note: str = "—"
    phase2c_policy_note: str = "—"
    phase2c_financial_note: str = "—"
    phase2c_fraud_note: str = "—"
    expected_final_decision: str = "N/A (Phase 2D not implemented yet)"


MANIFEST: List[ManifestEntry] = []


def record(entry: ManifestEntry) -> None:
    MANIFEST.append(entry)


# ─────────────────────────────────────────────────────────────────────────
#  PDF drawing helper — a thin, stateful wrapper around reportlab's Canvas
# ─────────────────────────────────────────────────────────────────────────

class Doc:
    """Top-down cursor helper around reportlab.pdfgen.canvas.Canvas.

    `self.y_top` tracks distance-from-top-of-page in points; drawing
    methods convert to reportlab's bottom-left-origin coordinates
    internally (`_y()` helper) so the rest of the code can be written in
    the more intuitive "top-down cursor" style.
    """

    def __init__(self, path: Path):
        self.path = str(path)
        self.c = canvas.Canvas(self.path, pagesize=A4)
        self.y_top = MARGIN

    def _y(self) -> float:
        return PAGE_H - self.y_top

    def ln(self, dy: float = 6) -> None:
        self.y_top += dy

    def hline(self, weight: float = 0.6) -> None:
        self.c.setLineWidth(weight)
        self.c.line(MARGIN, self._y(), PAGE_W - MARGIN, self._y())
        # 10pt clearance, not 4 -- a horizontal rule drawn too close above
        # the next text's baseline visually crosses through the glyphs'
        # ascenders (looks like a strikethrough). Found by rendering a
        # preview PNG of a generated document and visually inspecting it.
        self.ln(10)

    def header_block(self, title: str, subtitle: str = "", address: str = "") -> None:
        self.c.setFont("Helvetica-Bold", 15)
        self.c.drawCentredString(PAGE_W / 2, self._y(), title)
        self.ln(16)
        if subtitle:
            self.c.setFont("Helvetica", 10)
            self.c.drawCentredString(PAGE_W / 2, self._y(), subtitle)
            self.ln(13)
        if address:
            self.c.setFont("Helvetica", 9)
            self.c.drawCentredString(PAGE_W / 2, self._y(), address)
            self.ln(12)
        self.hline(0.9)

    def title_line(self, text: str) -> None:
        self.c.setFont("Helvetica-Bold", 12)
        self.c.drawCentredString(PAGE_W / 2, self._y(), text)
        self.ln(14)
        self.hline(0.6)

    def field_row(self, label: str, value: str, label_w: float = 45 * mm) -> None:
        self.c.setFont("Helvetica-Bold", 9.5)
        self.c.drawString(MARGIN, self._y(), f"{label}:")
        self.c.setFont("Helvetica", 9.5)
        self.c.drawString(MARGIN + label_w, self._y(), value)
        self.ln(14)

    def two_col_row(self, l_label: str, l_val: str, r_label: str, r_val: str) -> None:
        half = INNER_W / 2
        self.c.setFont("Helvetica-Bold", 9.5)
        self.c.drawString(MARGIN, self._y(), f"{l_label}:")
        self.c.setFont("Helvetica", 9.5)
        self.c.drawString(MARGIN + 26 * mm, self._y(), l_val)
        self.c.setFont("Helvetica-Bold", 9.5)
        self.c.drawString(MARGIN + half, self._y(), f"{r_label}:")
        self.c.setFont("Helvetica", 9.5)
        self.c.drawString(MARGIN + half + 26 * mm, self._y(), r_val)
        self.ln(14)

    def section_title(self, text: str) -> None:
        self.c.setFont("Helvetica-Bold", 10)
        self.c.drawString(MARGIN, self._y(), text)
        self.ln(13)

    def body_line(self, text: str, indent: float = 5 * mm, font: str = "Helvetica", size: float = 9.5) -> None:
        self.c.setFont(font, size)
        self.c.drawString(MARGIN + indent, self._y(), text)
        self.ln(13)

    def italic_note(self, text: str) -> None:
        self.c.setFont("Helvetica-Oblique", 8.5)
        self.c.drawString(MARGIN, self._y(), text)
        self.ln(12)

    def _wrap_to_width(self, text: str, font: str, size: float, max_width: float) -> List[str]:
        """Word-wrap `text` into lines that each fit within `max_width`
        (measured with the actual font metrics, not a character-count
        guess) — cell text that's simply too long to fit a fixed row
        height must wrap onto more lines, never silently overflow into
        the neighbouring column. A single word longer than max_width is
        hard-broken as a last resort."""
        words = str(text).split()
        if not words:
            return [""]
        lines: List[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if self.c.stringWidth(candidate, font, size) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        # Hard-break any single word/line still too wide.
        final: List[str] = []
        for line in lines:
            while self.c.stringWidth(line, font, size) > max_width and len(line) > 1:
                lo, hi = 1, len(line)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if self.c.stringWidth(line[:mid], font, size) <= max_width:
                        lo = mid
                    else:
                        hi = mid - 1
                final.append(line[:lo])
                line = line[lo:]
            final.append(line)
        return final

    def table(
        self,
        headers: Sequence[Tuple[str, float]],
        rows: Sequence[Sequence[str]],
        row_h: float = 8 * mm,
        align_last_right: bool = True,
    ) -> None:
        """headers: [(label, width_mm)], rows: list of row cell-text lists.
        Cells whose text doesn't fit the column width wrap onto extra
        lines (the row grows), rather than overflowing into the next
        column — found and fixed after a rendered preview showed a long
        lab-result string bleeding into the adjacent column."""
        widths = [w * mm for _, w in headers]
        x0 = MARGIN
        line_h = 10  # pt, single text line within a cell
        cell_pad = 3

        # header row (never wraps -- header labels are always short)
        self.c.setFillColor(colors.Color(0.86, 0.86, 0.86))
        self.c.rect(x0, self._y() - row_h, sum(widths), row_h, fill=1, stroke=1)
        self.c.setFillColor(colors.black)
        self.c.setFont("Helvetica-Bold", 8.5)
        x = x0
        for (label, _), w in zip(headers, widths):
            self.c.drawCentredString(x + w / 2, self._y() - row_h + row_h / 2 - 3, label)
            self.c.rect(x, self._y() - row_h, w, row_h, fill=0, stroke=1)
            x += w
        self.y_top += row_h

        self.c.setFont("Helvetica", 8.5)
        for row in rows:
            wrapped_cells = [
                self._wrap_to_width(str(cell), "Helvetica", 8.5, w - 2 * cell_pad)
                for cell, w in zip(row, widths)
            ]
            n_lines = max(len(lines) for lines in wrapped_cells)
            this_row_h = max(row_h, n_lines * line_h + 2 * cell_pad)

            x = x0
            for i, (lines, w) in enumerate(zip(wrapped_cells, widths)):
                self.c.rect(x, self._y() - this_row_h, w, this_row_h, fill=0, stroke=1)
                is_last = align_last_right and i == len(row) - 1
                text_y = self._y() - cell_pad - 7
                for line in lines:
                    if is_last:
                        self.c.drawRightString(x + w - cell_pad, text_y, line)
                    else:
                        self.c.drawString(x + cell_pad, text_y, line)
                    text_y -= line_h
                x += w
            self.y_top += this_row_h

    def right_amount_line(self, label: str, value: str, bold: bool = False) -> None:
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", 9.5)
        self.c.drawRightString(PAGE_W - MARGIN - 50 * mm, self._y(), label)
        self.c.drawRightString(PAGE_W - MARGIN, self._y(), value)
        self.ln(13)

    def signature_block(self, name: str, reg: str, role: str = "") -> None:
        self.ln(10)
        self.hline(0.4)
        self.c.setFont("Helvetica-Bold", 9.5)
        self.c.drawString(MARGIN, self._y(), name)
        self.ln(11)
        if role:
            self.c.setFont("Helvetica", 8.5)
            self.c.drawString(MARGIN, self._y(), role)
            self.ln(10)
        self.c.setFont("Helvetica", 8.5)
        self.c.drawString(MARGIN, self._y(), f"Reg. No: {reg}    [Signature & Stamp]")
        self.ln(10)

    def footer(self) -> None:
        self.c.setFont("Helvetica-Oblique", 7)
        self.c.setFillColor(colors.Color(0.55, 0.55, 0.55))
        self.c.drawCentredString(PAGE_W / 2, 8 * mm, FOOTER_TEXT)
        self.c.setFillColor(colors.black)

    def embed_image(self, img: Image.Image, caption: str = "") -> None:
        """Draw a PIL image centred on the current page, below the current
        cursor, scaled to fit within the remaining margin box."""
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        reader = ImageReader(buf)
        avail_w = INNER_W
        avail_h = self._y() - MARGIN - (14 if caption else 0)
        iw, ih = img.size
        scale = min(avail_w / iw, avail_h / ih)
        draw_w, draw_h = iw * scale, ih * scale
        x = MARGIN + (avail_w - draw_w) / 2
        y = MARGIN + (14 if caption else 0)
        self.c.drawImage(reader, x, y, width=draw_w, height=draw_h)
        if caption:
            self.c.setFont("Helvetica-Oblique", 8)
            self.c.drawCentredString(PAGE_W / 2, MARGIN, caption)

    def new_page(self) -> None:
        """Finish the current page (with its footer) and start a fresh one
        — used by multi-page documents (e.g. QUALITY_TESTS' multipage bill)."""
        self.footer()
        self.c.showPage()
        self.y_top = MARGIN

    def save(self) -> None:
        self.footer()
        self.c.showPage()
        self.c.save()


def new_page() -> Doc:
    d = Doc.__new__(Doc)
    return d


# ─────────────────────────────────────────────────────────────────────────
#  High-level document builders (PDF, clean/normal quality)
# ─────────────────────────────────────────────────────────────────────────

def build_prescription(
    path: Path,
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
    medicines: List[str],
    tests: Optional[List[str]] = None,
    notes: str = "",
    followup: str = "After 5 days if no improvement",
) -> None:
    d = Doc(path)
    d.header_block(
        title=f"Dr. {doctor_name}",
        subtitle=f"{doctor_specialization} | Reg. No: {doctor_reg}",
        address=f"{clinic_name}, {clinic_address} | Ph: +91-80-XXXXXXXX",
    )
    d.two_col_row("Patient", patient_name, "Date", date)
    d.two_col_row("Age", f"{patient_age} years", "Gender", "Male" if patient_gender.upper() == "M" else "Female")
    d.ln(4)
    d.hline()

    d.section_title(f"Diagnosis: {diagnosis}")
    d.ln(2)
    d.section_title("Rx:")
    for i, med in enumerate(medicines, 1):
        d.body_line(f"{i}. {med}", font="Courier", size=9)

    if tests:
        d.ln(2)
        d.section_title("Investigations:")
        d.body_line(", ".join(tests))

    if notes:
        d.ln(2)
        d.italic_note(f"Note: {notes}")

    d.ln(2)
    d.italic_note(f"Follow-up: {followup}")
    d.signature_block(name=f"Dr. {doctor_name}", reg=doctor_reg, role=doctor_specialization)
    d.save()


def build_hospital_bill(
    path: Path,
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
    line_items: List[Tuple[str, float]],
    gst_percent: float = 0.0,
    payment_mode: str = "UPI / Cash",
    title: str = "BILL / RECEIPT",
) -> float:
    d = Doc(path)
    d.header_block(title=hospital_name, subtitle=hospital_address, address=f"GSTIN: {gstin} | Ph: 080-XXXXXXXX")
    d.title_line(title)
    d.two_col_row("Bill No", bill_no, "Date", date)
    d.field_row("Patient Name", patient_name)
    d.two_col_row("Age", f"{patient_age} yrs", "Gender", "Male" if patient_gender.upper() == "M" else "Female")
    d.field_row("Referring Doctor", referring_doctor)
    d.ln(3)
    d.hline()

    col_desc = INNER_W / mm - 20 - 20 - 30
    d.table(
        headers=[("DESCRIPTION", col_desc), ("QTY", 20), ("RATE (Rs.)", 20), ("AMOUNT (Rs.)", 30)],
        rows=[[desc, "1", f"{amt:,.2f}", f"{amt:,.2f}"] for desc, amt in line_items],
    )
    subtotal = sum(amt for _, amt in line_items)
    gst_amount = subtotal * gst_percent / 100
    total = subtotal + gst_amount

    d.ln(9)
    d.right_amount_line("Subtotal:", f"Rs. {subtotal:,.2f}")
    if gst_percent:
        d.right_amount_line(f"GST ({gst_percent:.0f}%):", f"Rs. {gst_amount:,.2f}")
    d.right_amount_line("Total Amount:", f"Rs. {total:,.2f}", bold=True)

    d.ln(9)
    d.hline(0.4)
    d.italic_note(f"Payment Mode: {payment_mode}    |    Received by: Cashier  [Cashier Stamp]")
    d.save()
    return total


def build_dental_bill(
    path: Path,
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
    line_items: List[Tuple[str, float, bool]],
) -> float:
    d = Doc(path)
    d.header_block(title=clinic_name, subtitle="Multispeciality Dental Clinic | BDS/MDS Registered", address=address)
    d.title_line("DENTAL TREATMENT BILL")
    d.two_col_row("Bill No", bill_no, "Date", date)
    d.field_row("Patient Name", patient_name)
    d.two_col_row("Age", f"{patient_age} yrs", "Gender", "Male" if patient_gender.upper() == "M" else "Female")
    d.field_row("Treating Dentist", f"Dr. {dentist_name}  |  Reg: {dentist_reg}")
    d.ln(3)
    d.hline()

    col_desc = INNER_W / mm - 35 - 35
    rows = []
    for desc, amt, covered in line_items:
        status = "Covered" if covered else "Excluded (Cosmetic)"
        rows.append([desc, f"{amt:,.2f}", status])
    d.table(headers=[("PROCEDURE", col_desc), ("AMOUNT (Rs.)", 35), ("STATUS", 35)], rows=rows, align_last_right=False)

    subtotal = sum(amt for _, amt, _ in line_items)
    d.ln(9)
    d.right_amount_line("Total Amount:", f"Rs. {subtotal:,.2f}", bold=True)
    d.signature_block(name=f"Dr. {dentist_name}", reg=dentist_reg, role="BDS, MDS (Prosthodontics)")
    d.save()
    return subtotal


def build_lab_report(
    path: Path,
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
    tests: List[Tuple[str, str, str, str]],
    remarks: str,
    pathologist_name: str,
    pathologist_reg: str,
) -> None:
    d = Doc(path)
    d.header_block(
        title=lab_name,
        subtitle=(f"NABL Accredited Lab | " if nabl else "") + f"Lab ID: {lab_id}",
        address=address,
    )
    d.two_col_row("Patient", patient_name, "Sample ID", sample_id)
    d.two_col_row("Age/Sex", f"{patient_age} / {'Male' if patient_gender == 'M' else 'Female'}", "Ref Doctor", referring_doctor)
    d.two_col_row("Sample Date", sample_date, "Report Date", report_date)
    d.ln(3)
    d.hline()

    col_w = [65, 30, 25, 40]
    headers = ["TEST NAME", "RESULT", "UNIT", "NORMAL RANGE"]
    d.table(headers=list(zip(headers, col_w)), rows=[list(t) for t in tests], align_last_right=False)

    d.ln(9)
    d.section_title("Remarks:")
    d.body_line(remarks, font="Helvetica-Oblique", size=8.5)
    d.signature_block(name=pathologist_name, reg=pathologist_reg, role="MD (Pathology)")
    d.save()


def build_hospital_bill_multipage(
    path: Path,
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
    line_items_page1: List[Tuple[str, float]],
    line_items_page2: List[Tuple[str, float]],
) -> float:
    """Same content as build_hospital_bill, but deliberately split across
    two PDF pages — page 1 header/patient-info/first line items, page 2
    continues the line items and carries the total. Exercises multi-page
    document handling (QUALITY_TESTS/multipage_bill.pdf)."""
    d = Doc(path)
    d.header_block(title=hospital_name, subtitle=hospital_address, address=f"GSTIN: {gstin} | Ph: 080-XXXXXXXX")
    d.title_line("BILL / RECEIPT (Page 1 of 2)")
    d.two_col_row("Bill No", bill_no, "Date", date)
    d.field_row("Patient Name", patient_name)
    d.two_col_row("Age", f"{patient_age} yrs", "Gender", "Male" if patient_gender.upper() == "M" else "Female")
    d.field_row("Referring Doctor", referring_doctor)
    d.ln(3)
    d.hline()

    col_desc = INNER_W / mm - 20 - 20 - 30
    d.table(
        headers=[("DESCRIPTION", col_desc), ("QTY", 20), ("RATE (Rs.)", 20), ("AMOUNT (Rs.)", 30)],
        rows=[[desc, "1", f"{amt:,.2f}", f"{amt:,.2f}"] for desc, amt in line_items_page1],
    )
    d.ln(10)
    d.italic_note("(continued on page 2 — see remaining line items and total)")
    d.new_page()

    d.header_block(title=hospital_name, subtitle=hospital_address, address=f"GSTIN: {gstin} | Ph: 080-XXXXXXXX")
    d.title_line("BILL / RECEIPT (Page 2 of 2)")
    d.field_row("Bill No (contd.)", bill_no)
    d.table(
        headers=[("DESCRIPTION", col_desc), ("QTY", 20), ("RATE (Rs.)", 20), ("AMOUNT (Rs.)", 30)],
        rows=[[desc, "1", f"{amt:,.2f}", f"{amt:,.2f}"] for desc, amt in line_items_page2],
    )
    subtotal = sum(amt for _, amt in line_items_page1 + line_items_page2)
    d.ln(9)
    d.right_amount_line("Total Amount (all pages):", f"Rs. {subtotal:,.2f}", bold=True)
    d.ln(9)
    d.hline(0.4)
    d.italic_note("Payment Mode: UPI / Cash    |    Received by: Cashier  [Cashier Stamp]")
    d.save()
    return subtotal


def build_pharmacy_bill(
    path: Path,
    *,
    pharmacy_name: str,
    drug_license: str,
    address: str,
    bill_no: str,
    date: str,
    patient_name: str,
    doctor_name: str,
    medicines: List[Tuple[str, str, str, int, float]],
    discount_percent: float = 0.0,
) -> float:
    d = Doc(path)
    d.header_block(title=pharmacy_name, subtitle=f"Drug Lic. No: {drug_license}", address=address)
    d.two_col_row("Bill No", bill_no, "Date", date)
    d.two_col_row("Patient", patient_name, "Dr.", doctor_name)
    d.ln(3)
    d.hline()

    col_w = [45, 20, 18, 12, 20, 25]
    headers = ["MEDICINE", "BATCH", "EXP", "QTY", "MRP", "AMOUNT (Rs.)"]
    rows = []
    subtotal = 0.0
    for name, batch, exp, qty, mrp in medicines:
        amt = qty * mrp
        subtotal += amt
        rows.append([name, batch, exp, str(qty), f"{mrp:.2f}", f"{amt:.2f}"])
    d.table(headers=list(zip(headers, col_w)), rows=rows)

    discount = subtotal * discount_percent / 100
    net = subtotal - discount
    d.ln(9)
    d.right_amount_line("Subtotal:", f"Rs. {subtotal:.2f}")
    if discount_percent:
        d.right_amount_line(f"Discount ({discount_percent:.0f}%):", f"-Rs. {discount:.2f}")
    d.right_amount_line("Net Amount:", f"Rs. {net:.2f}", bold=True)

    d.ln(9)
    d.hline(0.4)
    d.italic_note("Pharmacist: R. Sharma   [Stamp]   [FSSAI / Drug Inspector Reg.]")
    d.save()
    return net


# ─────────────────────────────────────────────────────────────────────────
#  PIL image builders + degradation helpers (for quality-variation docs)
# ─────────────────────────────────────────────────────────────────────────

def _font(size: int, bold: bool = False):
    names = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "DejaVuSans.ttf"]
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _handwriting_font(size: int):
    """A genuinely script-style font (not just a smaller print font) for
    the QUALITY_TESTS handwritten-style prescription — falls back to the
    regular font if no handwriting-style TTF is available on this machine."""
    for name in ("SCRIPTBL.TTF", "FRSCRIPT.TTF", "LHANDW.TTF", "comic.ttf"):
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        except (OSError, IOError):
            continue
    return _font(size)


def render_prescription_image(
    *,
    patient_name: str,
    doctor_name: str,
    doctor_reg: str,
    diagnosis: str,
    date: str,
    medicines: List[str],
    clinic_name: str = "City Medical Centre",
    handwritten_style: bool = False,
) -> Image.Image:
    """Return a clean-rendered prescription as a PIL Image (before any
    degradation is applied) — a phone-camera photo of a printed/handwritten
    prescription, not the PDF-native vector text path."""
    W, H = 900, 1150
    img = Image.new("RGB", (W, H), color=(253, 253, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(18, 18), (W - 18, H - 18)], outline=(60, 60, 110), width=3)

    fn_title = _font(22, bold=True)
    fn_sub = _font(13)
    fn_body = _font(15)

    draw.rectangle([(18, 18), (W - 18, 130)], fill=(60, 60, 110))
    draw.text((W // 2, 48), f"Dr. {doctor_name}", fill="white", font=fn_title, anchor="mm")
    draw.text((W // 2, 78), f"Reg. No: {doctor_reg}", fill=(210, 210, 230), font=fn_sub, anchor="mm")
    draw.text((W // 2, 100), f"{clinic_name}", fill=(200, 200, 225), font=fn_sub, anchor="mm")
    draw.text((W // 2, 118), "Ph: +91-80-XXXXXXXX", fill=(190, 190, 220), font=fn_sub, anchor="mm")

    y = 155
    # The letterhead (clinic name/reg no above) stays pre-printed/typeset
    # even for the "handwritten" variant -- only the doctor's own entries
    # (patient/date/diagnosis/Rx) switch to a script-style font, matching
    # how a real prescription pad actually looks (printed letterhead,
    # handwritten body).
    body_font = fn_body if not handwritten_style else _handwriting_font(24)
    ink = (25, 25, 35) if not handwritten_style else (20, 20, 130)

    draw.text((45, y), f"Patient: {patient_name}", fill=ink, font=body_font)
    draw.text((W - 260, y), f"Date: {date}", fill=ink, font=body_font)
    y += 45
    draw.line([(35, y), (W - 35, y)], fill=(180, 180, 180))
    y += 20
    draw.text((45, y), "Diagnosis:", fill=(90, 40, 40), font=_font(16, bold=True))
    y += 26
    draw.text((65, y), diagnosis, fill=ink, font=body_font)
    y += 45
    draw.text((45, y), "Rx:", fill=(60, 60, 110), font=_font(19, bold=True))
    y += 32
    for i, med in enumerate(medicines, 1):
        # For handwritten style, add a slight random jitter per line to
        # simulate non-uniform pen strokes rather than perfect print
        # alignment.
        jitter = random.randint(-3, 3) if handwritten_style else 0
        draw.text((70 + jitter, y), f"{i}. {med}", fill=ink, font=body_font)
        y += 40 if handwritten_style else 30

    y += 30
    draw.line([(35, y), (W - 35, y)], fill=(150, 150, 150))
    y = H - 150
    draw.text((W - 280, y), f"Dr. {doctor_name}", fill=ink, font=fn_body)
    y += 26
    draw.text((W - 280, y), f"Reg. No: {doctor_reg}", fill=(90, 90, 90), font=fn_sub)
    y += 22
    draw.text((W - 280, y), "[Signature & Stamp]", fill=(150, 150, 150), font=fn_sub)

    return img


def degrade_blur(img: Image.Image, radius: int = 6) -> Image.Image:
    return img.filter(ImageFilter.GaussianBlur(radius=radius))


def degrade_low_contrast(img: Image.Image, factor: float = 0.35) -> Image.Image:
    from PIL import ImageEnhance
    return ImageEnhance.Contrast(img).enhance(factor)


def degrade_rotate(img: Image.Image, degrees: float = 4.0) -> Image.Image:
    return img.rotate(degrees, expand=True, fillcolor=(245, 245, 240))


def degrade_shadow_noise(img: Image.Image) -> Image.Image:
    """Simulate a phone-camera photo: a soft diagonal shadow gradient plus
    per-pixel luminance noise."""
    import numpy as np

    arr = np.array(img).astype("int16")
    h, w = arr.shape[:2]
    # Diagonal shadow: darker toward one corner.
    yy, xx = np.mgrid[0:h, 0:w]
    shadow = ((xx / w) * 0.35 + (yy / h) * 0.15)
    shadow = 1.0 - shadow
    arr = (arr * shadow[..., None]).clip(0, 255)
    noise = np.random.normal(0, 9, arr.shape)
    arr = (arr + noise).clip(0, 255).astype("uint8")
    return Image.fromarray(arr, mode="RGB")


def phone_photo_effect(img: Image.Image) -> Image.Image:
    img = degrade_rotate(img, degrees=random.uniform(-3, 3))
    img = degrade_shadow_noise(img)
    img = degrade_blur(img, radius=1)
    return img


def embed_image_pdf(path: Path, img: Image.Image, caption: str = "") -> None:
    """Single-page PDF containing just the (already degraded) image, plus
    the standard synthetic-document footer."""
    d = Doc(path)
    d.embed_image(img, caption=caption)
    d.save()


HINDI_FONT_PATH = "C:/Windows/Fonts/Nirmala.ttc"


def _hindi_font(size: int):
    try:
        return ImageFont.truetype(HINDI_FONT_PATH, size, index=0)
    except (OSError, IOError):
        return _font(size)


def render_hospital_bill_image(
    *,
    hospital_name: str,
    patient_name: str,
    date: str,
    line_items: List[Tuple[str, float]],
    bill_no: str = "BILL/2024/0001",
) -> Image.Image:
    """Photograph-style rendering of a hospital bill (used as the base for
    phone-photo / partial / correction / duplicate-stamp quality tests)."""
    W, H = 900, 1100
    img = Image.new("RGB", (W, H), color=(255, 255, 253))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(15, 15), (W - 15, H - 15)], outline=(0, 70, 110), width=3)

    fn_title = _font(20, bold=True)
    fn_body = _font(14)
    fn_small = _font(12)

    draw.rectangle([(15, 15), (W - 15, 120)], fill=(0, 70, 110))
    draw.text((W // 2, 45), hospital_name, fill="white", font=fn_title, anchor="mm")
    draw.text((W // 2, 78), "BILL / RECEIPT", fill=(210, 230, 240), font=fn_body, anchor="mm")
    draw.text((W // 2, 100), "Ph: 080-XXXXXXXX | GSTIN: 29AAACC1234C1ZX", fill=(190, 215, 230), font=fn_small, anchor="mm")

    y = 145
    draw.text((45, y), f"Bill No: {bill_no}", fill=(20, 20, 20), font=fn_body)
    draw.text((W - 260, y), f"Date: {date}", fill=(20, 20, 20), font=fn_body)
    y += 32
    draw.text((45, y), f"Patient: {patient_name}", fill=(20, 20, 20), font=fn_body)
    y += 34
    draw.line([(35, y), (W - 35, y)], fill=(0, 70, 110), width=2)
    y += 20

    for desc, amt in line_items:
        draw.text((55, y), desc, fill=(30, 30, 30), font=fn_small)
        draw.text((W - 220, y), f"Rs. {amt:,.2f}", fill=(30, 30, 30), font=fn_small)
        y += 30

    total = sum(a for _, a in line_items)
    y += 15
    draw.line([(35, y), (W - 35, y)], fill=(0, 70, 110), width=1)
    y += 20
    draw.text((W - 320, y), f"Total: Rs. {total:,.2f}", fill=(0, 70, 110), font=fn_title, anchor="lm")

    y = H - 130
    draw.line([(35, y), (W - 35, y)], fill=(160, 160, 160))
    y += 15
    draw.text((45, y), "Received by: Cashier   [Cashier Stamp]", fill=(90, 90, 90), font=fn_small)

    return img


def render_pharmacy_bill_image(
    *,
    patient_name: str,
    doctor_name: str,
    medicines: List[Tuple[str, str, str, int, float]],
    date: str,
    bill_no: str = "HFP-24-9001",
) -> Image.Image:
    W, H = 900, 950
    img = Image.new("RGB", (W, H), color=(255, 255, 253))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(15, 15), (W - 15, H - 15)], outline=(0, 90, 0), width=3)

    fn_title = _font(20, bold=True)
    fn_body = _font(14)
    fn_small = _font(12)

    draw.rectangle([(15, 15), (W - 15, 120)], fill=(0, 90, 0))
    draw.text((W // 2, 45), "HEALTH FIRST PHARMACY", fill="white", font=fn_title, anchor="mm")
    draw.text((W // 2, 78), "Drug Lic. No: KA-BLR-XXXX", fill=(210, 240, 210), font=fn_body, anchor="mm")
    draw.text((W // 2, 100), "22 Brigade Road, Bengaluru | Ph: 080-XXXXXXXX", fill=(190, 225, 190), font=fn_small, anchor="mm")

    y = 140
    draw.text((45, y), f"Bill No: {bill_no}", fill=(20, 20, 20), font=fn_body)
    draw.text((W - 260, y), f"Date: {date}", fill=(20, 20, 20), font=fn_body)
    y += 30
    draw.text((45, y), f"Patient: {patient_name}    Dr: {doctor_name}", fill=(20, 20, 20), font=fn_body)
    y += 30
    draw.line([(30, y), (W - 30, y)], fill=(0, 90, 0), width=2)
    y += 15

    cols = [220, 100, 80, 60, 90, 110]
    headers = ["MEDICINE", "BATCH", "EXP", "QTY", "MRP", "AMOUNT"]
    x = 40
    draw.rectangle([(30, y), (W - 30, y + 28)], fill=(220, 240, 220))
    for h, cw in zip(headers, cols):
        draw.text((x + cw // 2, y + 14), h, fill=(0, 70, 0), font=fn_small, anchor="mm")
        x += cw
    y += 30

    subtotal = 0.0
    for name, batch, exp, qty, mrp in medicines:
        amt = qty * mrp
        x = 40
        for val, cw in zip([name, batch, exp, str(qty), f"{mrp:.2f}", f"{amt:.2f}"], cols):
            draw.text((x + 5, y + 8), val, fill=(20, 20, 20), font=fn_small)
            x += cw
        y += 28
        subtotal += amt

    y += 15
    draw.line([(30, y), (W - 30, y)], fill=(0, 90, 0), width=1)
    y += 15
    draw.text((W - 260, y), f"Net Amount: Rs. {subtotal:.2f}", fill=(0, 90, 0), font=fn_title)

    y += 60
    draw.line([(30, y), (W - 30, y)], fill=(180, 180, 180))
    y += 12
    draw.text((45, y), "Pharmacist: R. Sharma   [Stamp]", fill=(90, 90, 90), font=fn_small)

    return img


def draw_stamp(img: Image.Image, text: str, center: Tuple[int, int], color=(190, 30, 30), angle: float = -18) -> Image.Image:
    """Draw a rubber-stamp-style rotated bordered oval over the image,
    simulating a stamp physically applied over printed text (partially
    obscuring it) — used by QUALITY_TESTS/stamped_prescription.pdf."""
    stamp = Image.new("RGBA", (340, 140), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(stamp)
    sdraw.ellipse([(4, 4), (336, 136)], outline=color + (230,), width=5)
    sdraw.ellipse([(14, 14), (326, 126)], outline=color + (180,), width=2)
    f = _font(28, bold=True)
    sdraw.text((170, 55), text, fill=color + (220,), font=f, anchor="mm")
    sdraw.text((170, 90), "VERIFIED COPY", fill=color + (200,), font=_font(14), anchor="mm")
    stamp = stamp.rotate(angle, expand=True, resample=Image.BICUBIC)
    base = img.convert("RGBA")
    x = center[0] - stamp.width // 2
    y = center[1] - stamp.height // 2
    base.alpha_composite(stamp, (x, y))
    return base.convert("RGB")


def draw_duplicate_watermark(img: Image.Image, text: str = "DUPLICATE") -> Image.Image:
    """Large diagonal translucent watermark across the whole page —
    Indian hospital bills routinely mark photocopies ORIGINAL/DUPLICATE."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    f = _font(64, bold=True)
    odraw.text((img.width // 2, img.height // 2), text, fill=(200, 30, 30, 90), font=f, anchor="mm")
    rotated = overlay.rotate(35, expand=False, resample=Image.BICUBIC)
    base = img.convert("RGBA")
    base.alpha_composite(rotated)
    return base.convert("RGB")


def apply_correction(img: Image.Image, original_text: str, corrected_text: str, at: Tuple[int, int]) -> Image.Image:
    """Simulate a manual correction on a bill: strike through the original
    printed value and hand-annotate the corrected one beside it, plus a
    small initialing mark — a common real-world document-robustness case."""
    draw = ImageDraw.Draw(img)
    x, y = at
    f = _font(14)
    draw.text((x, y), original_text, fill=(30, 30, 30), font=f)
    tw = draw.textlength(original_text, font=f)
    draw.line([(x - 2, y + 8), (x + tw + 2, y + 8)], fill=(190, 30, 30), width=2)
    draw.text((x + tw + 15, y - 2), corrected_text, fill=(20, 60, 150), font=_font(15, bold=True))
    draw.text((x + tw + 15, y + 18), "(corrected — Cashier)", fill=(20, 60, 150), font=_font(10))
    return img


def crop_partial(img: Image.Image, keep_fraction: float = 0.55) -> Image.Image:
    """Simulate an incomplete/cut-off scan: keep only the top
    `keep_fraction` of the page, pasted onto a blank page of the original
    size (so the missing bottom is genuinely blank, not resized away)."""
    w, h = img.size
    cropped = img.crop((0, 0, w, int(h * keep_fraction)))
    canvas_img = Image.new("RGB", (w, h), color=(255, 255, 255))
    canvas_img.paste(cropped, (0, 0))
    draw = ImageDraw.Draw(canvas_img)
    draw.text((30, int(h * keep_fraction) + 20), "[ ... rest of page missing from scan ... ]",
              fill=(180, 180, 180), font=_font(14))
    return canvas_img


def render_multilingual_prescription_image(
    *,
    patient_name_en: str,
    patient_name_hi: str,
    doctor_name: str,
    doctor_reg: str,
    diagnosis_en: str,
    diagnosis_hi: str,
    date: str,
    medicines: List[str],
) -> Image.Image:
    """Prescription with mixed Hindi/English text (Devanagari via the
    system's Nirmala UI font) — a real-world variation the assignment's
    sample_documents_guide.md calls out explicitly."""
    W, H = 900, 1150
    img = Image.new("RGB", (W, H), color=(253, 253, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(18, 18), (W - 18, H - 18)], outline=(60, 60, 110), width=3)

    fn_title = _font(22, bold=True)
    fn_body = _font(15)
    fn_hi = _hindi_font(16)
    fn_hi_bold = _hindi_font(18)

    draw.rectangle([(18, 18), (W - 18, 130)], fill=(60, 60, 110))
    draw.text((W // 2, 45), f"Dr. {doctor_name}", fill="white", font=fn_title, anchor="mm")
    draw.text((W // 2, 75), f"Reg. No: {doctor_reg}", fill=(210, 210, 230), font=_font(13), anchor="mm")
    draw.text((W // 2, 100), "City Medical Centre, Bengaluru", fill=(200, 200, 225), font=_font(13), anchor="mm")

    y = 155
    draw.text((45, y), f"Patient / रोगी: {patient_name_en} / {patient_name_hi}", fill=(25, 25, 35), font=fn_hi)
    y += 30
    draw.text((45, y), f"Date / दिनांक: {date}", fill=(25, 25, 35), font=fn_hi)
    y += 40
    draw.line([(35, y), (W - 35, y)], fill=(180, 180, 180))
    y += 20
    draw.text((45, y), "Diagnosis / निदान:", fill=(90, 40, 40), font=fn_hi_bold)
    y += 28
    draw.text((65, y), diagnosis_en, fill=(25, 25, 35), font=fn_body)
    y += 24
    draw.text((65, y), diagnosis_hi, fill=(25, 25, 35), font=fn_hi)
    y += 45
    draw.text((45, y), "Rx / दवाइयाँ:", fill=(60, 60, 110), font=fn_hi_bold)
    y += 32
    for i, med in enumerate(medicines, 1):
        # Nirmala UI covers both Devanagari and Latin glyphs, so lines
        # mixing English and Hindi (e.g. a dosage note in Hindi) render
        # correctly with one font -- using the English-only fn_body here
        # left embedded Hindi text as tofu boxes.
        draw.text((70, y), f"{i}. {med}", fill=(25, 25, 35), font=fn_hi)
        y += 30

    y = H - 150
    draw.line([(35, y), (W - 35, y)], fill=(150, 150, 150))
    y += 15
    draw.text((W - 280, y), f"Dr. {doctor_name}", fill=(25, 25, 35), font=fn_body)
    y += 26
    draw.text((W - 280, y), f"Reg. No: {doctor_reg}", fill=(90, 90, 90), font=_font(12))
    y += 22
    draw.text((W - 280, y), "[Signature & Stamp]", fill=(150, 150, 150), font=_font(12))

    return img
