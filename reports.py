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
"""

import io

import pandas as pd
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
