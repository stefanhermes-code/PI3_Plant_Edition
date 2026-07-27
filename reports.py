"""Report generation: data assembly + PDF/Excel rendering.

"Report" is one of PI3 Plant Edition's own standard, always-included
capabilities (see pages/10_PI3_AI_Connectivity.py's docstring: "Standard
version (always included): Search, Compare, Retrieve, Structure, Report,
Review and Approval.") - this module is what had been missing. It is not
gated behind PI3 connectivity; every logged-in user can generate reports.

Three report types, each with a data-assembly function (plain dict, no
Streamlit import, easy to unit test) and a PDF + Excel renderer pair:

- build_run_report_data() / render_run_report_pdf() / render_run_report_excel()
  One production run: recipe, process settings, quality results/issues,
  adjustments, approvals - the "hand this to a customer or auditor for
  this batch" document.
- build_period_summary_data() / render_period_summary_pdf() / render_period_summary_excel()
  One plant/product family/date range: KPIs, pass rate, recurring issues,
  the run list, and a breakdown by foam grade.
- build_trial_report_data() / render_trial_report_pdf() / render_trial_report_excel()
  One trial/experiment: objective, what changed, results, conclusion,
  approvals - a formal closeout writeup.

pages/21_Report.py wires these to selectors, an in-app preview, and
st.download_button for both file formats.

A fourth, narrower report type lives here too:

- build_pi3_qa_report_data() / render_pi3_qa_report_docx()
  A single "Ask PI3" question-and-answer exchange (see
  helpers.render_ask_pi3_section) - the question, PI3's answer, and an
  appendix of the exact data PI3 checked to produce it (SQL + rows
  returned, or the verified-analysis arguments and result). Unlike the
  three reports above, this is DOCX only (no PDF/Excel - there's no
  tabular data here that benefits from a spreadsheet), and it is always
  built from this same code path, so every export has identical
  formatting regardless of who generates it or what was asked - a
  hand-maintained Word template would drift over time; this can't.
"""

import datetime as dt
import io
import os

import pandas as pd
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from analytics import PHASE_SETTING_FIELDS, PHASE_SETTING_LABELS
from db import (
    AdjustmentConclusion,
    ApprovalRecord,
    FoamGrade,
    Plant,
    PhysicalPropertyResult,
    ProductFamily,
    ProductionPhase,
    ProductionRun,
    QualityObservation,
    RecipeComponent,
    TrialRecord,
)

STYLES = getSampleStyleSheet()


# ---------------------------------------------------------------------------
# Shared rendering helpers
# ---------------------------------------------------------------------------

def _pdf_bytes(build_story):
    """build_story(story: list) appends reportlab flowables to story."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = []
    build_story(story)
    doc.build(story)
    return buf.getvalue()


def _excel_bytes(sheets):
    """sheets: dict of sheet_name -> list-of-dicts or DataFrame. Empty
    sections still get a sheet (with a placeholder row) so the workbook
    structure is predictable regardless of what data exists."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
            if df.empty:
                df = pd.DataFrame({"—": ["No data recorded"]})
            # Excel sheet names are capped at 31 characters.
            df.to_excel(writer, sheet_name=name[:31], index=False)
    return buf.getvalue()


def _p(text, style="Normal"):
    """Paragraph with basic XML-escaping, since free-text fields (notes,
    hypotheses, ...) may contain '&', '<', '>' which reportlab's
    Paragraph would otherwise try to interpret as markup."""
    if text is None:
        text = "—"
    escaped = (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return Paragraph(escaped, STYLES[style])


def _key_value_table(pairs, col_widths=(35 * mm, 55 * mm, 35 * mm, 55 * mm)):
    """pairs: list of (label, value) - rendered two-per-row as a compact
    header block (label/value/label/value)."""
    rows = []
    for i in range(0, len(pairs), 2):
        row = []
        for label, value in pairs[i:i + 2]:
            row.extend([label, "—" if value in (None, "") else str(value)])
        while len(row) < 4:
            row.append("")
        rows.append(row)
    t = Table(rows, colWidths=list(col_widths))
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _section(story, title, rows, col_widths=None):
    """A heading followed by a table built from a list of dicts (all
    sharing the same keys), or a plain "no data" note if rows is empty."""
    story.append(Spacer(1, 8))
    story.append(Paragraph(title, STYLES["Heading3"]))
    if not rows:
        story.append(_p("No data recorded."))
        return
    headers = list(rows[0].keys())
    table_rows = [headers]
    for row in rows:
        table_rows.append(["—" if row.get(h) in (None, "") else str(row.get(h)) for h in headers])
    kwargs = {"colWidths": col_widths} if col_widths else {}
    t = Table(table_rows, repeatRows=1, **kwargs)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EC")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B0BEC5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)


def _title_block(story, title, subtitle=None):
    story.append(Paragraph(title, STYLES["Title"]))
    if subtitle:
        story.append(Paragraph(subtitle, STYLES["Normal"]))
    story.append(Spacer(1, 6))


def plant_label(session, plant_id):
    if plant_id is None:
        return "All plants"
    p = session.get(Plant, plant_id)
    return p.name if p else "—"


def product_family_label(session, product_family_id):
    if product_family_id is None:
        return "All product families"
    f = session.get(ProductFamily, product_family_id)
    return f.name if f else "—"


# ---------------------------------------------------------------------------
# 1. Production Run Report
# ---------------------------------------------------------------------------

def build_run_report_data(session, run_id):
    run = session.get(ProductionRun, run_id)
    if run is None:
        return None
    grade = run.foam_grade
    family = grade.product_family if grade else None
    recipe = run.recipe_version

    components = [
        {
            "Material": c.raw_material_name,
            "Supplier": c.supplier or "—",
            "PHP": c.php,
            "Role": c.role_in_formulation or "—",
        }
        for c in (
            session.query(RecipeComponent).filter(RecipeComponent.recipe_version_id == recipe.id).all()
            if recipe else []
        )
    ]

    phases = session.query(ProductionPhase).filter(ProductionPhase.production_run_id == run_id).all()
    phase_settings = []
    for phase in phases:
        row = {"Phase": phase.phase_name}
        for field in PHASE_SETTING_FIELDS:
            row[PHASE_SETTING_LABELS[field]] = getattr(phase, field)
        phase_settings.append(row)

    quality_results = [
        {
            "Property": r.property_name,
            "Target": r.target_value,
            "Actual": r.actual_value,
            "Unit": r.unit or "",
            "Pass/Fail": r.pass_fail or "—",
            "Tested": r.tested_at,
        }
        for r in session.query(PhysicalPropertyResult)
        .filter(PhysicalPropertyResult.production_run_id == run_id).all()
    ]

    quality_issues = [
        {
            "Issue type": o.observation_type,
            "Severity": o.severity or "—",
            "Frequency": o.frequency or "—",
            "Confidence": o.confidence_level or "—",
            "Suspected cause": o.suspected_cause or "—",
        }
        for o in session.query(QualityObservation)
        .filter(QualityObservation.production_run_id == run_id).all()
    ]

    adjustments = [
        {
            "Parameter/material changed": a.parameter_changed or a.material_changed or "—",
            "Result": a.result or "—",
            "Reuse recommendation": a.reuse_recommendation or "—",
            "Confidence": a.confidence_level or "—",
        }
        for a in session.query(AdjustmentConclusion)
        .filter(AdjustmentConclusion.production_run_id == run_id).all()
    ]

    approvals = [
        {
            "Status": a.approval_status or "—",
            "Reviewed by": a.reviewed_by or "—",
            "Approved by": a.approved_by or "—",
            "Date reviewed": a.date_reviewed,
            "Date approved": a.date_approved,
        }
        for a in session.query(ApprovalRecord)
        .filter(ApprovalRecord.production_run_id == run_id).all()
    ]

    return {
        "run_id": run.id,
        "plant": run.plant.name if run.plant else "—",
        "product_family": family.name if family else "—",
        "foam_grade": grade.grade_name if grade else "—",
        "recipe_version": recipe.version_label if recipe else "—",
        "machine": run.machine.name if run.machine else "—",
        "run_date": run.run_date,
        "batch_reference": run.batch_reference or "—",
        "block_reference": run.block_reference or "—",
        "operator": run.operator_or_team_reference or "—",
        "notes": run.notes or "",
        "components": components,
        "phase_settings": phase_settings,
        "quality_results": quality_results,
        "quality_issues": quality_issues,
        "adjustments": adjustments,
        "approvals": approvals,
    }


def render_run_report_pdf(data):
    def build(story):
        _title_block(
            story, f"Production Run Report — Run #{data['run_id']}",
            f"{data['plant']} · {data['foam_grade']} · {data['run_date'] or '—'}",
        )
        story.append(_key_value_table([
            ("Plant", data["plant"]), ("Product family", data["product_family"]),
            ("Foam grade", data["foam_grade"]), ("Recipe version", data["recipe_version"]),
            ("Machine", data["machine"]), ("Run date", data["run_date"]),
            ("Batch reference", data["batch_reference"]), ("Block reference", data["block_reference"]),
            ("Operator/team", data["operator"]), ("", ""),
        ]))
        if data["notes"]:
            story.append(Spacer(1, 6))
            story.append(_p(f"Notes: {data['notes']}"))
        _section(story, "Recipe components", data["components"])
        _section(story, "Process settings (by phase)", data["phase_settings"])
        _section(story, "Quality test results", data["quality_results"])
        _section(story, "Quality issues", data["quality_issues"])
        _section(story, "Adjustments & conclusions", data["adjustments"])
        _section(story, "Approvals", data["approvals"])
    return _pdf_bytes(build)


def render_run_report_excel(data):
    header = [{
        "Run ID": data["run_id"], "Plant": data["plant"], "Product family": data["product_family"],
        "Foam grade": data["foam_grade"], "Recipe version": data["recipe_version"], "Machine": data["machine"],
        "Run date": data["run_date"], "Batch reference": data["batch_reference"],
        "Block reference": data["block_reference"], "Operator/team": data["operator"], "Notes": data["notes"],
    }]
    return _excel_bytes({
        "Header": header,
        "Recipe Components": data["components"],
        "Process Settings": data["phase_settings"],
        "Quality Results": data["quality_results"],
        "Quality Issues": data["quality_issues"],
        "Adjustments": data["adjustments"],
        "Approvals": data["approvals"],
    })


# ---------------------------------------------------------------------------
# 2. Plant / Period Summary Report
# ---------------------------------------------------------------------------

def build_period_summary_data(session, plant_id=None, product_family_id=None, date_from=None, date_to=None):
    runs_q = session.query(ProductionRun)
    if plant_id:
        runs_q = runs_q.filter(ProductionRun.plant_id == plant_id)
    if product_family_id:
        runs_q = runs_q.join(FoamGrade, ProductionRun.foam_grade_id == FoamGrade.id).filter(
            FoamGrade.product_family_id == product_family_id
        )
    if date_from:
        runs_q = runs_q.filter(ProductionRun.run_date >= date_from)
    if date_to:
        runs_q = runs_q.filter(ProductionRun.run_date <= date_to)
    runs = runs_q.order_by(ProductionRun.run_date).all()
    run_ids = [r.id for r in runs]

    results = (
        session.query(PhysicalPropertyResult)
        .filter(PhysicalPropertyResult.production_run_id.in_(run_ids)).all()
        if run_ids else []
    )
    pass_count = len([r for r in results if r.pass_fail == "Pass"])
    fail_count = len([r for r in results if r.pass_fail == "Fail"])
    total_scored = pass_count + fail_count
    pass_rate = round(100 * pass_count / total_scored) if total_scored else None

    observations = (
        session.query(QualityObservation)
        .filter(QualityObservation.production_run_id.in_(run_ids)).all()
        if run_ids else []
    )
    recurring = [o for o in observations if o.frequency == "Recurring"]

    run_rows = [
        {
            "Run ID": r.id, "Date": r.run_date,
            "Foam grade": r.foam_grade.grade_name if r.foam_grade else "—",
            "Recipe version": r.recipe_version.version_label if r.recipe_version else "—",
            "Machine": r.machine.name if r.machine else "—",
            "Batch reference": r.batch_reference or "—",
        }
        for r in runs
    ]

    issue_rows = [
        {
            "Observed": o.observed_at, "Run": o.production_run_id, "Issue type": o.observation_type,
            "Severity": o.severity or "—", "Frequency": o.frequency or "—",
            "Confidence": o.confidence_level or "—",
        }
        for o in observations
    ]

    grade_counts = {}
    for r in runs:
        gname = r.foam_grade.grade_name if r.foam_grade else "—"
        grade_counts[gname] = grade_counts.get(gname, 0) + 1
    grade_breakdown = [{"Foam grade": k, "Production runs": v} for k, v in sorted(grade_counts.items())]

    return {
        "plant": plant_label(session, plant_id),
        "product_family": product_family_label(session, product_family_id),
        "date_from": date_from,
        "date_to": date_to,
        "total_runs": len(runs),
        "pass_rate": pass_rate,
        "total_results_scored": total_scored,
        "total_quality_issues": len(observations),
        "recurring_issues": len(recurring),
        "runs": run_rows,
        "quality_issues": issue_rows,
        "grade_breakdown": grade_breakdown,
    }


def render_period_summary_pdf(data):
    def build(story):
        _title_block(
            story, "Plant / Period Summary Report",
            f"{data['plant']} · {data['product_family']} · {data['date_from'] or 'earliest'} to {data['date_to'] or 'latest'}",
        )
        story.append(_key_value_table([
            ("Production runs", data["total_runs"]),
            ("Quality test pass rate", f"{data['pass_rate']}%" if data["pass_rate"] is not None else "—"),
            ("Quality issues", data["total_quality_issues"]),
            ("Recurring quality issues", data["recurring_issues"]),
        ], col_widths=(45 * mm, 40 * mm, 45 * mm, 40 * mm)))
        _section(story, "Production runs in range", data["runs"])
        _section(story, "Quality issues in range", data["quality_issues"])
        _section(story, "Breakdown by foam grade", data["grade_breakdown"])
    return _pdf_bytes(build)


def render_period_summary_excel(data):
    header = [{
        "Plant": data["plant"], "Product family": data["product_family"],
        "Date from": data["date_from"], "Date to": data["date_to"],
        "Production runs": data["total_runs"],
        "Pass rate (%)": data["pass_rate"], "Quality issues": data["total_quality_issues"],
        "Recurring issues": data["recurring_issues"],
    }]
    return _excel_bytes({
        "Summary": header,
        "Production Runs": data["runs"],
        "Quality Issues": data["quality_issues"],
        "Grade Breakdown": data["grade_breakdown"],
    })


# ---------------------------------------------------------------------------
# 3. Trial Closeout Report
# ---------------------------------------------------------------------------

def build_trial_report_data(session, trial_id):
    trial = session.get(TrialRecord, trial_id)
    if trial is None:
        return None
    run = trial.production_run
    grade = run.foam_grade if run else None

    quality_issues = [
        {
            "Issue type": o.observation_type, "Severity": o.severity or "—",
            "Frequency": o.frequency or "—", "Confidence": o.confidence_level or "—",
        }
        for o in trial.quality_observations
    ]
    adjustments = [
        {
            "Parameter/material changed": a.parameter_changed or a.material_changed or "—",
            "Result": a.result or "—", "Reuse recommendation": a.reuse_recommendation or "—",
            "Confidence": a.confidence_level or "—",
        }
        for a in trial.adjustment_conclusions
    ]
    approvals = [
        {
            "Status": a.approval_status or "—", "Reviewed by": a.reviewed_by or "—",
            "Approved by": a.approved_by or "—", "Date reviewed": a.date_reviewed,
            "Date approved": a.date_approved,
        }
        for a in trial.approval_records
    ]

    return {
        "trial_id": trial.id,
        "run_id": run.id if run else None,
        "foam_grade": grade.grade_name if grade else "—",
        "status": trial.status,
        "objective": trial.trial_or_change_objective,
        "hypothesis": trial.hypothesis or "—",
        "what_changed": trial.what_changed or "—",
        "responsible_person": trial.responsible_person or "—",
        "result_against_target": trial.result_against_target or "—",
        "physical_property_outcome": trial.physical_property_outcome or "—",
        "conclusion": trial.conclusion or "—",
        "reuse_recommendation": trial.reuse_recommendation or "—",
        "reviewed_by": trial.reviewed_by or "—",
        "approved_by": trial.approved_by or "—",
        "date_closed": trial.date_closed,
        "quality_issues": quality_issues,
        "adjustments": adjustments,
        "approvals": approvals,
    }


def render_trial_report_pdf(data):
    def build(story):
        _title_block(
            story, f"Trial Closeout Report — Trial #{data['trial_id']}",
            f"{data['foam_grade']} · run #{data['run_id']} · {data['status']}",
        )
        story.append(_key_value_table([
            ("Status", data["status"]), ("Responsible", data["responsible_person"]),
            ("Foam grade", data["foam_grade"]), ("Production run", f"#{data['run_id']}"),
            ("Reviewed by", data["reviewed_by"]), ("Approved by", data["approved_by"]),
            ("Date closed", data["date_closed"]), ("", ""),
        ]))
        story.append(Spacer(1, 8))
        story.append(Paragraph("Objective", STYLES["Heading3"]))
        story.append(_p(data["objective"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph("Hypothesis", STYLES["Heading3"]))
        story.append(_p(data["hypothesis"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph("What changed", STYLES["Heading3"]))
        story.append(_p(data["what_changed"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph("Result against target", STYLES["Heading3"]))
        story.append(_p(data["result_against_target"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph("Physical property outcome", STYLES["Heading3"]))
        story.append(_p(data["physical_property_outcome"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph("Conclusion", STYLES["Heading3"]))
        story.append(_p(data["conclusion"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph("Reuse recommendation", STYLES["Heading3"]))
        story.append(_p(data["reuse_recommendation"]))
        _section(story, "Quality issues observed", data["quality_issues"])
        _section(story, "Adjustments & conclusions", data["adjustments"])
        _section(story, "Approvals", data["approvals"])
    return _pdf_bytes(build)


def render_trial_report_excel(data):
    header = [{
        "Trial ID": data["trial_id"], "Run ID": data["run_id"], "Foam grade": data["foam_grade"],
        "Status": data["status"], "Responsible": data["responsible_person"],
        "Objective": data["objective"], "Hypothesis": data["hypothesis"],
        "What changed": data["what_changed"], "Result against target": data["result_against_target"],
        "Physical property outcome": data["physical_property_outcome"], "Conclusion": data["conclusion"],
        "Reuse recommendation": data["reuse_recommendation"], "Reviewed by": data["reviewed_by"],
        "Approved by": data["approved_by"], "Date closed": data["date_closed"],
    }]
    return _excel_bytes({
        "Trial": header,
        "Quality Issues": data["quality_issues"],
        "Adjustments": data["adjustments"],
        "Approvals": data["approvals"],
    })


# ---------------------------------------------------------------------------
# 4. PI3 Q&A Report (DOCX only)
# ---------------------------------------------------------------------------

_HTC_LOGO_PATH = "assets/htc_global_logo_blue_steel.png"
_HTC_BLUE = RGBColor(0x1B, 0x6F, 0xA8)  # matches .streamlit/config.toml primaryColor
_HTC_GREY = RGBColor(0x5A, 0x6B, 0x74)


def build_pi3_qa_report_data(
    question, answer, tool_log, page_context="", plant_name=None,
    foam_grade_name=None, asked_by=None, asked_at=None,
):
    """Plain-dict data assembly for one 'Ask PI3' question/answer exchange -
    no Streamlit or python-docx import, so this half is easy to unit test
    on its own. `tool_log` is exactly what ai_assistant.ask_plant_question()
    returns: a list of dicts, each either
    {"tool": "query_plant_data", "sql", "rows_returned", "rows", ["error"]}
    or {"tool": "get_verified_analysis", "args", "result"}."""
    return {
        "question": (question or "").strip(),
        "answer": (answer or "").strip(),
        "tool_log": tool_log or [],
        "page_context": (page_context or "").strip(),
        "plant_name": plant_name,
        "foam_grade_name": foam_grade_name,
        "asked_by": asked_by,
        "asked_at": asked_at or dt.datetime.utcnow(),
    }


def _docx_heading(doc, text, size=13, color=_HTC_BLUE, space_before=12):
    """A styled heading paragraph - deliberately not doc.add_heading()'s
    built-in Heading styles, since those pull from the default Word theme
    (unpredictable across machines); this way every report's headings look
    identical regardless of what Word template the opening machine has."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(size)
    run.font.color.rgb = color
    return p


def _docx_kv_table(doc, pairs):
    table = doc.add_table(rows=0, cols=2)
    table.autofit = True
    for label, value in pairs:
        row = table.add_row().cells
        label_run = row[0].paragraphs[0].add_run(label)
        label_run.bold = True
        label_run.font.size = Pt(9.5)
        row[1].paragraphs[0].add_run("—" if value in (None, "") else str(value)).font.size = Pt(9.5)
    return table


def _docx_data_table(doc, rows, max_rows=200):
    """Renders a list-of-dicts as a bordered table, capped at max_rows so a
    very large query result doesn't produce an unusable multi-hundred-page
    appendix - the SQL that produced it is always shown alongside, so the
    full result set is still reproducible."""
    if not rows:
        doc.add_paragraph("No rows returned.").runs[0].font.size = Pt(9)
        return
    shown = rows[:max_rows]
    headers = list(shown[0].keys())
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for cell, header in zip(table.rows[0].cells, headers):
        run = cell.paragraphs[0].add_run(header)
        run.bold = True
        run.font.size = Pt(8.5)
    for row_data in shown:
        cells = table.add_row().cells
        for cell, header in zip(cells, headers):
            value = row_data.get(header)
            cell.paragraphs[0].add_run("—" if value in (None, "") else str(value)).font.size = Pt(8.5)
    if len(rows) > max_rows:
        note = doc.add_paragraph(f"... {len(rows) - max_rows} further row(s) not shown.")
        note.runs[0].italic = True
        note.runs[0].font.size = Pt(8.5)


def render_pi3_qa_report_docx(data):
    """Renders one PI3 Q&A exchange as DOCX bytes: HTC-branded header,
    a metadata block, the question, PI3's answer, an advisory-boundary
    disclaimer, and an appendix showing exactly what PI3 checked to
    produce the answer. Same layout, same styling, every single time -
    that consistency is the whole point of generating this from code
    rather than starting from a hand-edited Word file each time."""
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)

    # --- Header: logo + title -------------------------------------------
    header = doc.add_table(rows=1, cols=2)
    header.autofit = False
    header.columns[0].width = Cm(3.6)
    logo_cell, title_cell = header.rows[0].cells
    if os.path.exists(_HTC_LOGO_PATH):
        run = logo_cell.paragraphs[0].add_run()
        run.add_picture(_HTC_LOGO_PATH, width=Cm(3.0))
    title_p = title_cell.paragraphs[0]
    title_run = title_p.add_run("PI3 Q&A Report")
    title_run.bold = True
    title_run.font.size = Pt(20)
    title_run.font.color.rgb = _HTC_BLUE
    subtitle_p = title_cell.add_paragraph()
    subtitle_run = subtitle_p.add_run("Flexible slabstock foam expert system | HTC Global Co. Ltd")
    subtitle_run.italic = True
    subtitle_run.font.size = Pt(10)
    subtitle_run.font.color.rgb = _HTC_GREY

    doc.add_paragraph()

    # --- Metadata ----------------------------------------------------------
    _docx_kv_table(doc, [
        ("Generated", data["asked_at"].strftime("%Y-%m-%d %H:%M UTC")),
        ("Plant", data.get("plant_name") or "—"),
        ("Foam grade", data.get("foam_grade_name") or "—"),
        ("Asked by", data.get("asked_by") or "—"),
        ("Page context", data.get("page_context") or "—"),
    ])

    # --- Question / answer ---------------------------------------------
    _docx_heading(doc, "Question asked")
    doc.add_paragraph(data["question"] or "—")

    _docx_heading(doc, "PI3's answer")
    for line in (data["answer"] or "—").split("\n"):
        if line.strip():
            doc.add_paragraph(line)

    disclaimer = doc.add_paragraph()
    disclaimer.paragraph_format.space_before = Pt(10)
    disc_run = disclaimer.add_run(
        "This is historical reference for the reviewer's own investigation, not an "
        "instruction. Confirm through your own investigation before acting on it."
    )
    disc_run.italic = True
    disc_run.font.size = Pt(9)
    disc_run.font.color.rgb = _HTC_GREY

    # --- Appendix: exactly what PI3 checked ------------------------------
    doc.add_page_break()
    _docx_heading(doc, "Appendix: data PI3 checked", size=15)
    tool_log = data.get("tool_log") or []
    if not tool_log:
        doc.add_paragraph("No tool calls were recorded for this answer.")
    for i, entry in enumerate(tool_log, start=1):
        tool_name = entry.get("tool", "unknown tool")
        _docx_heading(doc, f"{i}. {tool_name}", size=11.5, color=_HTC_GREY, space_before=14)
        if tool_name == "query_plant_data":
            sql_p = doc.add_paragraph()
            sql_run = sql_p.add_run(entry.get("sql", "—"))
            sql_run.font.name = "Consolas"
            sql_run.font.size = Pt(9)
            if "error" in entry:
                err_p = doc.add_paragraph()
                err_run = err_p.add_run(f"Rejected: {entry['error']}")
                err_run.font.color.rgb = RGBColor(0xB0, 0x00, 0x00)
                err_run.font.size = Pt(9)
            else:
                count_p = doc.add_paragraph()
                count_run = count_p.add_run(f"{entry.get('rows_returned', 0)} row(s) returned:")
                count_run.font.size = Pt(9)
                _docx_data_table(doc, entry.get("rows") or [])
        elif tool_name == "get_verified_analysis":
            args_p = doc.add_paragraph()
            args_run = args_p.add_run(f"Arguments: {entry.get('args')}")
            args_run.font.size = Pt(9)
            result = entry.get("result")
            if isinstance(result, dict):
                kv_pairs = []
                table_keys = []  # list-of-dicts values, rendered as a sub-table below instead
                for k, v in result.items():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        kv_pairs.append((k, f"[{len(v)} row(s) - see table below]"))
                        table_keys.append(k)
                    elif isinstance(v, list):
                        # Plain-value list (e.g. warnings/successes strings) - show the
                        # actual content inline rather than hiding it behind a count.
                        kv_pairs.append((k, "; ".join(str(x) for x in v) if v else "—"))
                    else:
                        kv_pairs.append((k, v))
                _docx_kv_table(doc, kv_pairs)
                for k in table_keys:
                    sub = doc.add_paragraph()
                    sub_run = sub.add_run(k)
                    sub_run.bold = True
                    sub_run.font.size = Pt(9)
                    _docx_data_table(doc, result[k])

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
