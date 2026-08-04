"""Report generation: data assembly + PDF/Excel rendering.

"Report" is one of PI3 Plant Edition's own standard, always-included
capabilities (see pages/10_PI3_AI_Connectivity.py's docstring: "Standard
version (always included): Search, Compare, Retrieve, Structure, Report,
Review and Approval.") - this module is what had been missing. It is not
gated behind PI3 connectivity; every logged-in user can generate reports.

Three report types, each with a data-assembly function (plain dict, no
Streamlit import, easy to unit test) and a PDF + Excel renderer pair:

- build_run_report_data() / render_run_report_pdf() / render_run_report_excel()
  One production run: recipe, process settings, quality results/issues -
  the "hand this to a customer or auditor for this batch" document.
- build_period_summary_data() / render_period_summary_pdf() / render_period_summary_excel()
  One plant/product family/date range: KPIs, pass rate, recurring issues,
  the run list, and a breakdown by foam grade.
- build_trial_report_data() / render_trial_report_pdf() / render_trial_report_excel()
  One closed Customer Trial or Optimization Trial (see db.CustomerTrial /
  db.OptimizationTrial - the two independent lab-trial flows, added
  2026-08-03): objective/hypothesis, what changed, outcome/conclusion,
  reviewer sign-off - a formal closeout writeup. Rebuilt 2026-08-04 to
  cover these two (the trials people actually use) after the old
  TrialRecord concept (a formal-experiment flag on a production run) was
  removed - zero real rows across 244 production runs, fully superseded
  by these two independent, self-contained closeout flows.

pages/21_Report.py wires these to selectors, an in-app preview, and
st.download_button for both file formats.

Two purpose-built reports for the Recipes page (pages/3_Recipe_Version_
Record.py), added 2026-08-04 as the first output of the app-wide Reports
redesign (see PI3_Gaps note: reports must be aggregated/purpose-built
answers to a specific question, never a raw-data dump or a PI3-narrative
document - the CSV export on every table already covers raw-data needs,
and PI3's own Word download on relevant pages already covers narrative):

- build_recipe_formulation_record_data() / render_recipe_formulation_record_pdf()
  / render_recipe_formulation_record_excel()
  One recipe version: the formulation itself (materials/php/supplier/
  role), its quality specs vs. actual results aggregated over a chosen
  date range (across every production run/customer trial/optimization
  trial built on this recipe version), and cost per kg - "is this
  formulation meeting spec, and what does it cost" in one document for
  internal use/approval (not customer-facing - a customer-facing version
  would need to omit the formulation itself).
- build_where_used_report_data() / render_where_used_report_pdf()
  / render_where_used_report_excel()
  One raw material: every recipe version (active and retired) that uses
  it with its php/role, the target properties of every foam grade
  affected, and any Customer/Optimization Trial precedent tied to a
  recipe version containing it - "if I replace this material, what's
  affected and what trials already exist to lean on."

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
import re

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

from analytics import PHASE_SETTING_FIELDS, PHASE_SETTING_LABELS, recipe_version_cost
from db import (
    CustomerTrial,
    FoamGrade,
    FoamGradeTargetProperty,
    OptimizationTrial,
    Plant,
    PhysicalPropertyResult,
    ProductFamily,
    ProductionPhase,
    ProductionRun,
    QualityObservation,
    RawMaterial,
    RecipeComponent,
    RecipeVersion,
)
from quality_standards import compute_pass_fail

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
            if field == "ratio_index":
                # Recipe-level constant since 2026-08-03 (see
                # RecipeVersion.ratio_index in db.py) - same value for every
                # phase of this run, sourced from the recipe rather than
                # getattr(phase, ...) like every other field here.
                row[PHASE_SETTING_LABELS[field]] = recipe.ratio_index if recipe else None
            else:
                row[PHASE_SETTING_LABELS[field]] = getattr(phase, field)
        phase_settings.append(row)

    quality_results = [
        {
            "Property": r.property_name,
            "Target": r.target_value,
            "Actual": r.actual_value,
            "Unit": r.unit or "",
            # Recomputed live rather than trusted from the stored pass_fail
            # column - see the same note in analytics.property_results_dataframe.
            "Pass/Fail": compute_pass_fail(r.property_name, r.target_value, r.actual_value) or "—",
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
    })


# ---------------------------------------------------------------------------
# 2. Plant / Period Summary Report
# ---------------------------------------------------------------------------

def build_period_summary_data(session, plant_id=None, product_family_id=None, date_from=None, date_to=None, allowed_plant_ids=None):
    """allowed_plant_ids is the tenant-scope guardrail (see tenant_scope.py):
    None = unfiltered (platform owner viewing "All companies"), otherwise the
    list of plant ids the calling company is allowed to see - applied
    unconditionally, on top of whatever single-plant choice plant_id
    represents. Without this, a non-owner user who leaves the on-screen
    "Plant" selector at its default "All plants" would get a report across
    every plant in the database, not just their own company's."""
    runs_q = session.query(ProductionRun)
    if allowed_plant_ids is not None:
        runs_q = runs_q.filter(ProductionRun.plant_id.in_(allowed_plant_ids))
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
    # Recomputed live rather than trusted from each result's stored
    # pass_fail column - see the same note in
    # analytics.property_results_dataframe.
    computed_verdicts = [compute_pass_fail(r.property_name, r.target_value, r.actual_value) for r in results]
    pass_count = computed_verdicts.count("Pass")
    fail_count = computed_verdicts.count("Fail")
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
#
# Covers the two independent lab-trial flows (see db.CustomerTrial /
# db.OptimizationTrial, added 2026-08-03) - NOT the old TrialRecord concept
# (a formal-experiment flag on a production run), which was removed
# 2026-08-04 after confirming zero real rows across 244 production runs.
# The two models have different closeout fields (a sales-driven Customer
# Trial has customer_name/outcome/customer_feedback; an internally-driven
# Optimization Trial has hypothesis/conclusion/reuse_recommendation), so
# build_trial_report_data() normalizes both into one common "narrative
# fields" list of (label, value) pairs the PDF/Excel renderers can walk
# without needing to know which trial type produced them.
# ---------------------------------------------------------------------------

def build_trial_report_data(session, source_type, trial_id):
    """source_type is "Customer Trial" or "Optimization Trial" (see
    db.SAMPLE_SOURCE_TYPES) - the same source-type string used throughout
    the app to disambiguate the three mutually-exclusive parents a sample/
    quality result can belong to."""
    if source_type == "Customer Trial":
        trial = session.get(CustomerTrial, trial_id)
    elif source_type == "Optimization Trial":
        trial = session.get(OptimizationTrial, trial_id)
    else:
        return None
    if trial is None:
        return None
    grade = trial.foam_grade
    plant = trial.plant

    quality_issues = [
        {
            "Issue type": o.observation_type, "Severity": o.severity or "—",
            "Frequency": o.frequency or "—", "Confidence": o.confidence_level or "—",
        }
        for o in session.query(QualityObservation).filter(
            (QualityObservation.customer_trial_id == trial_id)
            if source_type == "Customer Trial"
            else (QualityObservation.optimization_trial_id == trial_id)
        ).all()
    ]

    if source_type == "Customer Trial":
        narrative_fields = [
            ("Customer", trial.customer_name),
            ("Sales opportunity reference", trial.sales_opportunity_reference or "—"),
            ("Requested by", trial.requested_by or "—"),
            ("Trial objective", trial.trial_objective or "—"),
            ("Outcome", trial.outcome or "—"),
            ("Customer feedback", trial.customer_feedback or "—"),
            ("Follow-up action", trial.follow_up_action or "—"),
        ]
    else:
        narrative_fields = [
            ("Improvement initiative reference", trial.improvement_initiative_reference or "—"),
            ("Hypothesis", trial.hypothesis or "—"),
            ("What changed", trial.what_changed or "—"),
            ("Result against target", trial.result_against_target or "—"),
            ("Conclusion", trial.conclusion or "—"),
            ("Reuse recommendation", trial.reuse_recommendation or "—"),
        ]

    return {
        "source_type": source_type,
        "trial_id": trial.id,
        "foam_grade": grade.grade_name if grade else "—",
        "plant": plant.name if plant else "—",
        "status": trial.status,
        "responsible_person": trial.responsible_person or "—",
        "trial_date": trial.trial_date,
        "batch_reference": trial.batch_reference or "—",
        "notes": trial.notes or "",
        "narrative_fields": narrative_fields,
        "reviewed_by": trial.reviewed_by or "—",
        # Only OptimizationTrial has a separate approved_by (CustomerTrial's
        # closeout is reviewed_by only - see db.py's REQUIRED_CLOSEOUT_FIELDS
        # on each model).
        "approved_by": getattr(trial, "approved_by", None) or "—",
        "date_closed": trial.date_closed,
        "quality_issues": quality_issues,
    }


def render_trial_report_pdf(data):
    def build(story):
        _title_block(
            story, f"Trial Closeout Report — {data['source_type']} #{data['trial_id']}",
            f"{data['foam_grade']} · {data['plant']} · {data['status']}",
        )
        story.append(_key_value_table([
            ("Status", data["status"]), ("Responsible", data["responsible_person"]),
            ("Foam grade", data["foam_grade"]), ("Plant", data["plant"]),
            ("Trial date", data["trial_date"]), ("Batch reference", data["batch_reference"]),
            ("Reviewed by", data["reviewed_by"]), ("Approved by", data["approved_by"]),
            ("Date closed", data["date_closed"]), ("", ""),
        ]))
        if data["notes"]:
            story.append(Spacer(1, 6))
            story.append(_p(f"Notes: {data['notes']}"))
        story.append(Spacer(1, 8))
        for label, value in data["narrative_fields"]:
            story.append(Paragraph(label, STYLES["Heading3"]))
            story.append(_p(value))
            story.append(Spacer(1, 6))
        _section(story, "Quality issues observed", data["quality_issues"])
    return _pdf_bytes(build)


def render_trial_report_excel(data):
    header = [{
        "Trial type": data["source_type"], "Trial ID": data["trial_id"],
        "Foam grade": data["foam_grade"], "Plant": data["plant"],
        "Status": data["status"], "Responsible": data["responsible_person"],
        "Trial date": data["trial_date"], "Batch reference": data["batch_reference"],
        **{label: value for label, value in data["narrative_fields"]},
        "Reviewed by": data["reviewed_by"], "Approved by": data["approved_by"],
        "Date closed": data["date_closed"], "Notes": data["notes"],
    }]
    return _excel_bytes({
        "Trial": header,
        "Quality Issues": data["quality_issues"],
    })


# ---------------------------------------------------------------------------
# 4. Recipe / Formulation Record Report
#
# Internal-use record for one recipe version: the formulation, its quality
# specs vs. actual results aggregated over a chosen date range, and cost
# per kg. NOT customer-facing as-is (see reports.py module docstring) -
# the recipe/formulation section is the whole reason it can't be handed
# to a customer.
#
# "Quality specs & results" pulls actual PhysicalPropertyResult rows from
# every production run, customer trial, and optimization trial built on
# this recipe version (the three mutually-exclusive parents - see
# db.SAMPLE_SOURCE_TYPES), filtered to the caller's date range, then
# aggregated per property (average actual, pass rate, sample count)
# against that foam grade's target - never a flat row-by-row dump, per
# the Reports redesign ruling that raw data belongs in each page's own
# CSV export, not in a report.
# ---------------------------------------------------------------------------

def _recipe_version_target_properties(grade):
    """FoamGrade's target specs as a flat list of {property_name, target_value,
    unit} dicts - density/hardness (fixed columns on FoamGrade) plus any
    additional targets recorded in FoamGradeTargetProperty."""
    if grade is None:
        return []
    targets = []
    if grade.target_density is not None:
        targets.append({"property_name": "Density", "target_value": grade.target_density, "unit": "kg/m³"})
    if grade.target_hardness is not None:
        # "40% IFD / hardness" is the canonical property_name used app-wide
        # (see quality_standards.INDUSTRY_TOLERANCES and pages/15_Recipe_
        # Optimization.py's own target_by_name dict) - matching it here,
        # rather than a differently-worded label, is what lets this target
        # line up with actual PhysicalPropertyResult rows recorded against
        # that same property name instead of showing as two separate rows.
        targets.append({"property_name": "40% IFD / hardness", "target_value": grade.target_hardness, "unit": "N"})
    for tp in grade.target_properties:
        targets.append({"property_name": tp.property_name, "target_value": tp.target_value, "unit": tp.unit or ""})
    return targets


def _property_results_for_recipe_version(session, recipe_version_id, date_from=None, date_to=None):
    """Every PhysicalPropertyResult tied (via production run, customer trial,
    or optimization trial - see db.SAMPLE_SOURCE_TYPES) to this recipe
    version, filtered to [date_from, date_to] on that parent's own date
    field (run_date / trial_date). Three separate joins rather than one
    query, since which date field applies depends on which of the three
    parent types the result belongs to."""
    results = []

    run_q = (
        session.query(PhysicalPropertyResult)
        .join(ProductionRun, PhysicalPropertyResult.production_run_id == ProductionRun.id)
        .filter(ProductionRun.recipe_version_id == recipe_version_id)
    )
    if date_from:
        run_q = run_q.filter(ProductionRun.run_date >= date_from)
    if date_to:
        run_q = run_q.filter(ProductionRun.run_date <= date_to)
    results.extend(run_q.all())

    ct_q = (
        session.query(PhysicalPropertyResult)
        .join(CustomerTrial, PhysicalPropertyResult.customer_trial_id == CustomerTrial.id)
        .filter(CustomerTrial.recipe_version_id == recipe_version_id)
    )
    if date_from:
        ct_q = ct_q.filter(CustomerTrial.trial_date >= date_from)
    if date_to:
        ct_q = ct_q.filter(CustomerTrial.trial_date <= date_to)
    results.extend(ct_q.all())

    ot_q = (
        session.query(PhysicalPropertyResult)
        .join(OptimizationTrial, PhysicalPropertyResult.optimization_trial_id == OptimizationTrial.id)
        .filter(OptimizationTrial.recipe_version_id == recipe_version_id)
    )
    if date_from:
        ot_q = ot_q.filter(OptimizationTrial.trial_date >= date_from)
    if date_to:
        ot_q = ot_q.filter(OptimizationTrial.trial_date <= date_to)
    results.extend(ot_q.all())

    return results


def build_recipe_formulation_record_data(session, recipe_version_id, date_from=None, date_to=None):
    rv = session.get(RecipeVersion, recipe_version_id)
    if rv is None:
        return None
    grade = rv.foam_grade
    family = grade.product_family if grade else None

    ordered_components = sorted(
        rv.components,
        key=lambda c: (c.role_in_formulation or "", c.raw_material_name or ""),
    )
    components = [
        {
            "Material": c.raw_material_name,
            "Supplier": c.supplier or "—",
            "PHP": c.php,
            "Role": c.role_in_formulation or "—",
            "Notes": c.notes or "—",
        }
        for c in ordered_components
    ]

    targets_by_name = {t["property_name"]: t for t in _recipe_version_target_properties(grade)}
    results = _property_results_for_recipe_version(session, rv.id, date_from, date_to)
    by_property = {}
    for r in results:
        by_property.setdefault(r.property_name, []).append(r)

    quality_rows = []
    # Show every declared target even if zero results fell in range (an
    # honest "no data yet" beats silently omitting the row), plus any
    # measured property that isn't a formally declared target.
    for prop_name in sorted(set(targets_by_name) | set(by_property)):
        rs = by_property.get(prop_name, [])
        verdicts = [compute_pass_fail(r.property_name, r.target_value, r.actual_value) for r in rs]
        pass_ct, fail_ct = verdicts.count("Pass"), verdicts.count("Fail")
        scored = pass_ct + fail_ct
        actuals = [r.actual_value for r in rs if r.actual_value is not None]
        target = targets_by_name.get(prop_name)
        target_value = target["target_value"] if target else next(
            (r.target_value for r in rs if r.target_value is not None), None
        )
        unit = (target["unit"] if target else None) or next((r.unit for r in rs if r.unit), "")
        quality_rows.append({
            "Property": prop_name,
            "Target": target_value,
            "Avg. actual": round(sum(actuals) / len(actuals), 2) if actuals else None,
            "Unit": unit,
            "Results in range": len(rs),
            "Pass rate": f"{round(100 * pass_ct / scored)}%" if scored else "—",
        })

    cost = recipe_version_cost(session, rv)
    # Same php-parts-as-kg convention as pages/15_Recipe_Optimization.py's
    # _cost_per_kg() - a formulation's php total already IS its cost basis
    # (see analytics.recipe_version_cost's own docstring), so cost per kg
    # is simply total cost / total php, once any component is priced.
    cost_per_kg = (
        round(cost["total_cost"] / cost["total_php"], 2)
        if cost["total_cost"] is not None and cost["total_php"]
        else None
    )

    return {
        "recipe_version_id": rv.id,
        "version_label": rv.version_label,
        "foam_grade": grade.grade_name if grade else "—",
        "product_family": family.name if family else "—",
        "approval_status": rv.approval_status,
        "is_active": rv.is_active,
        "effective_date": rv.effective_date,
        "created_by": rv.created_by or "—",
        "change_note": rv.change_note or "",
        "ratio_index": rv.ratio_index,
        "components": components,
        "date_from": date_from,
        "date_to": date_to,
        "quality_rows": quality_rows,
        "cost_per_kg": cost_per_kg,
        "cost_priced_php": cost["priced_php"],
        "cost_total_php": cost["total_php"],
        "cost_missing_materials": cost["missing"],
    }


def render_recipe_formulation_record_pdf(data):
    def build(story):
        _title_block(
            story, f"Recipe / Formulation Record — {data['version_label']}",
            f"{data['foam_grade']} · {data['product_family']} · "
            f"{'Active recipe' if data['is_active'] else 'Retired version'}",
        )
        story.append(_key_value_table([
            ("Approval status", data["approval_status"]), ("Active", "Yes" if data["is_active"] else "No"),
            ("Effective date", data["effective_date"]), ("Created by", data["created_by"]),
            ("Ratio / index", f"{data['ratio_index']:.3f}" if data["ratio_index"] is not None else "—"),
            ("", ""),
        ]))
        if data["change_note"]:
            story.append(Spacer(1, 6))
            story.append(_p(f"Change note: {data['change_note']}"))
        _section(story, "Formulation (recipe components)", data["components"])

        date_range = f"{data['date_from'] or 'earliest'} to {data['date_to'] or 'latest'}"
        _section(story, f"Quality specs vs. results ({date_range})", data["quality_rows"])

        story.append(Spacer(1, 8))
        story.append(Paragraph("Cost", STYLES["Heading3"]))
        if data["cost_per_kg"] is None:
            story.append(_p("No priced components - cost per kg cannot be calculated."))
        else:
            coverage = f"{data['cost_priced_php']:.2f} / {data['cost_total_php']:.2f} php priced"
            story.append(_key_value_table([
                ("Cost per kg", data["cost_per_kg"]), ("Cost coverage", coverage),
            ]))
            if data["cost_missing_materials"]:
                story.append(_p("Unpriced materials (excluded from total): " + ", ".join(data["cost_missing_materials"])))
    return _pdf_bytes(build)


def render_recipe_formulation_record_excel(data):
    header = [{
        "Recipe version": data["version_label"], "Foam grade": data["foam_grade"],
        "Product family": data["product_family"], "Approval status": data["approval_status"],
        "Active": "Yes" if data["is_active"] else "No", "Effective date": data["effective_date"],
        "Created by": data["created_by"], "Ratio / index": data["ratio_index"],
        "Change note": data["change_note"], "Date from": data["date_from"], "Date to": data["date_to"],
        "Cost per kg": data["cost_per_kg"],
        "Cost coverage (php priced / total)": f"{data['cost_priced_php']} / {data['cost_total_php']}",
        "Unpriced materials": ", ".join(data["cost_missing_materials"]) or "—",
    }]
    return _excel_bytes({
        "Header": header,
        "Formulation": data["components"],
        "Quality vs Spec": data["quality_rows"],
    })


# ---------------------------------------------------------------------------
# 5. Where Used Report
#
# Given a raw material, answers "which recipes use this, and what depends
# on it" - the reverse lookup a Plant Manager needs before considering a
# material substitution. Scoped inherently by the tenant boundary: it
# joins on RecipeComponent.raw_material_id, a real FK to one specific
# (already company-scoped) raw_materials row, not a name match, so it
# can't cross into another company's recipes.
# ---------------------------------------------------------------------------

def build_where_used_report_data(session, raw_material_id):
    rm = session.get(RawMaterial, raw_material_id)
    if rm is None:
        return None

    components = (
        session.query(RecipeComponent)
        .filter(RecipeComponent.raw_material_id == rm.id)
        .all()
    )
    recipe_version_ids = {c.recipe_version_id for c in components}
    versions = (
        session.query(RecipeVersion).filter(RecipeVersion.id.in_(recipe_version_ids)).all()
        if recipe_version_ids else []
    )
    version_by_id = {v.id: v for v in versions}

    def _sort_key(c):
        v = version_by_id.get(c.recipe_version_id)
        grade = v.foam_grade if v else None
        return (grade.grade_name if grade else "", v.version_label if v else "")

    usage_rows = []
    grade_ids, family_names = set(), set()
    for c in sorted(components, key=_sort_key):
        v = version_by_id.get(c.recipe_version_id)
        grade = v.foam_grade if v else None
        family = grade.product_family if grade else None
        if grade:
            grade_ids.add(grade.id)
        if family:
            family_names.add(family.name)
        usage_rows.append({
            "Foam grade": grade.grade_name if grade else "—",
            "Product family": family.name if family else "—",
            "Recipe version": v.version_label if v else "—",
            "Status": "Active" if v and v.is_active else "Retired",
            "PHP": c.php,
            "Role": c.role_in_formulation or "—",
            "Approval status": v.approval_status if v else "—",
        })

    grades = session.query(FoamGrade).filter(FoamGrade.id.in_(grade_ids)).all() if grade_ids else []
    target_rows = []
    for g in sorted(grades, key=lambda g: g.grade_name):
        for t in _recipe_version_target_properties(g):
            target_rows.append({
                "Foam grade": g.grade_name, "Property": t["property_name"],
                "Target": t["target_value"], "Unit": t["unit"],
            })

    trial_rows = []
    if recipe_version_ids:
        customer_trials = (
            session.query(CustomerTrial)
            .filter(CustomerTrial.recipe_version_id.in_(recipe_version_ids))
            .order_by(CustomerTrial.trial_date.desc())
            .all()
        )
        for t in customer_trials:
            trial_rows.append({
                "Trial type": "Customer Trial", "Trial ID": t.id,
                "Foam grade": t.foam_grade.grade_name if t.foam_grade else "—",
                "Status": t.status, "Trial date": t.trial_date, "Outcome": t.outcome or "—",
            })
        optimization_trials = (
            session.query(OptimizationTrial)
            .filter(OptimizationTrial.recipe_version_id.in_(recipe_version_ids))
            .order_by(OptimizationTrial.trial_date.desc())
            .all()
        )
        for t in optimization_trials:
            trial_rows.append({
                "Trial type": "Optimization Trial", "Trial ID": t.id,
                "Foam grade": t.foam_grade.grade_name if t.foam_grade else "—",
                "Status": t.status, "Trial date": t.trial_date, "Outcome": t.conclusion or "—",
            })

    return {
        "raw_material_id": rm.id,
        "raw_material_name": rm.name,
        "category": rm.category or "—",
        "default_supplier": rm.default_supplier or "—",
        "active": rm.active,
        "recipe_version_count": len(recipe_version_ids),
        "foam_grade_count": len(grade_ids),
        "product_family_count": len(family_names),
        "usage_rows": usage_rows,
        "target_rows": target_rows,
        "trial_rows": trial_rows,
    }


def render_where_used_report_pdf(data):
    def build(story):
        _title_block(
            story, f"Where Used Report — {data['raw_material_name']}",
            f"{data['category']} · Default supplier: {data['default_supplier']} · "
            f"{'Active' if data['active'] else 'Inactive'} material",
        )
        story.append(_key_value_table([
            ("Recipe versions using this material", data["recipe_version_count"]),
            ("Foam grades affected", data["foam_grade_count"]),
            ("Product families affected", data["product_family_count"]),
            ("", ""),
        ]))
        _section(story, "Recipes using this material", data["usage_rows"])
        _section(story, "Target properties of affected foam grades", data["target_rows"])
        _section(story, "Trial precedent (Customer / Optimization Trials on these recipes)", data["trial_rows"])
    return _pdf_bytes(build)


def render_where_used_report_excel(data):
    header = [{
        "Raw material": data["raw_material_name"], "Category": data["category"],
        "Default supplier": data["default_supplier"], "Active": "Yes" if data["active"] else "No",
        "Recipe versions using it": data["recipe_version_count"],
        "Foam grades affected": data["foam_grade_count"],
        "Product families affected": data["product_family_count"],
    }]
    return _excel_bytes({
        "Header": header,
        "Recipe Usage": data["usage_rows"],
        "Target Properties": data["target_rows"],
        "Trial Precedent": data["trial_rows"],
    })


# ---------------------------------------------------------------------------
# 6. PI3 Q&A Report (DOCX only)
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
    """A styled heading paragraph, built on a real Word "Heading N" style
    (rather than a bold Normal paragraph) so it behaves like an actual
    heading: it shows up in Word's Navigation Pane/outline view and in any
    auto-generated table of contents, and - the practical reason this
    matters here - `keep_with_next` below stops Word from ever stranding a
    heading alone at the bottom of a page with its content pushed to the
    next one.

    Appearance is still fully controlled here via explicit run-level
    formatting (size/color/bold), same as before, so the look stays
    identical regardless of what Word template the opening machine has -
    using a named style doesn't reintroduce that risk, it just makes the
    style semantically real on top of the same explicit formatting."""
    level = 2 if size >= 15 else (3 if size >= 12 else 4)
    p = doc.add_paragraph(style=f"Heading {level}")
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(size)
    run.font.color.rgb = color
    return p


# Recognizes the fixed structure PI3's system prompt always produces (see
# ai_assistant.py's SYSTEM_PROMPT, section "9) Default Response Structure"):
# short, punctuation-free numbered top-level section titles ("1. Direct
# Answer"), optional single-letter-lettered sub-sections ("A. Reduced
# silicone performance"), and "- " prefixed list items. Matching on these
# turns PI3's plain text into real headings/bullets instead of one flat
# Normal paragraph per line - deliberately conservative (short line, starts
# with a capital letter, no internal period, no trailing period) so an
# ordinary sentence that happens to start with a number or single letter
# doesn't get misread as a heading.
_TOP_HEADING_RE = re.compile(r"^\d{1,2}\.\s+[A-Z][^.\n]{2,78}$")
_SUB_HEADING_RE = re.compile(r"^[A-J]\.\s+[A-Z][^.\n]{2,98}$")
_BULLET_RE = re.compile(r"^[-•*]\s+(\S.*)$")

# PI3's free-form question-answering prompt (ai_assistant.PLANT_QUERY_SYSTEM_PROMPT,
# used by the Ask PI3 box) doesn't forbid markdown the way the fixed-prompt
# one does, so its answers commonly contain **bold** / *italic* / `code`
# inline. Without this, those markers came through as literal asterisks/
# backticks in the Word doc instead of real formatting.
_INLINE_MD_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`")

# PI3 also frequently answers with a GFM-style markdown table (a header
# row, a "|---|---:|" alignment/separator row, then data rows) when
# comparing several components or properties - confirmed in production
# output (e.g. a Recipe Optimization report's "Recommended formulation
# direction" and "Target-property focus" tables). Before this existed,
# every line of a markdown table fell through to the plain-paragraph case
# below and rendered as literal "| Water | 3.00 php | ... |" / "|---|---:|"
# text - unreadable, and the single biggest formatting complaint on these
# reports. _PIPE_ROW_RE spots a candidate row; _SEP_CELL_RE recognizes the
# separator row's cells (dashes, optionally colon-flanked for alignment)
# so a real header+data table can be told apart from an ordinary line that
# merely happens to contain a "|" character.
_PIPE_ROW_RE = re.compile(r"^\|.*\|$")
_SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _split_md_table_row(line):
    """Split a markdown table row ('| a | b |') into ['a', 'b'], honoring a
    backslash-escaped pipe inside a cell and dropping the empty strings
    produced by the row's own leading/trailing pipes."""
    protected = line.replace("\\|", "\x00")
    cells = [c.strip().replace("\x00", "|") for c in protected.split("|")]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _is_md_table_separator(line):
    if not _PIPE_ROW_RE.match(line):
        return False
    cells = _split_md_table_row(line)
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells)


def _strip_inline_markdown(text):
    """Plain-text version of a line with markdown markers removed but their
    content kept - used for heading lines, which are already bold/colored
    by their own style, so there's no run-formatting reason to parse
    **bold**/*italic* there, just to avoid showing the literal markers."""
    return _INLINE_MD_RE.sub(lambda m: next(g for g in m.groups() if g is not None), text)


def _add_runs_with_inline_markdown(paragraph, text, size=None):
    """Append `text` to `paragraph` as one or more runs, converting
    **bold**, *italic*, and `code` markdown spans into real run formatting
    instead of leaving the literal markers in the output."""
    pos = 0
    for m in _INLINE_MD_RE.finditer(text):
        if m.start() > pos:
            run = paragraph.add_run(text[pos:m.start()])
            if size:
                run.font.size = size
        if m.group(1) is not None:
            run = paragraph.add_run(m.group(1))
            run.bold = True
        elif m.group(2) is not None:
            run = paragraph.add_run(m.group(2))
            run.italic = True
        else:
            run = paragraph.add_run(m.group(3))
            run.font.name = "Consolas"
        if size:
            run.font.size = size
        pos = m.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        if size:
            run.font.size = size


def _docx_markdown_table(doc, header_cells, data_rows):
    """Renders a parsed markdown table (a header cell list plus a list of
    data-row cell lists, both already produced by _split_md_table_row) as a
    real bordered Word table - same "Light Grid Accent 1" style used for
    PI3's own SQL-result appendix table (_docx_data_table), so a markdown
    table and a data table look consistent in the same report. Ragged rows
    (a data row with a different cell count than the header) are padded or
    truncated to the header's column count rather than raising - a
    model-written table is exactly the kind of input that occasionally
    comes out uneven."""
    ncols = len(header_cells)
    table = doc.add_table(rows=1, cols=ncols)
    table.style = "Light Grid Accent 1"
    for cell, header_text in zip(table.rows[0].cells, header_cells):
        run = cell.paragraphs[0].add_run(_strip_inline_markdown(header_text))
        run.bold = True
        run.font.size = Pt(9)
    for row_cells in data_rows:
        padded = (row_cells + [""] * ncols)[:ncols]
        cells = table.add_row().cells
        for cell, cell_text in zip(cells, padded):
            _add_runs_with_inline_markdown(cell.paragraphs[0], cell_text or "—", size=Pt(9))
    return table


def _render_ai_answer_body(doc, text):
    """Render a PI3 answer's plain text into real Word structure: numbered
    top-level sections and lettered sub-sections become real headings,
    "- " list items become real bulleted paragraphs, a markdown pipe table
    (header row + "|---|" separator row + data rows) becomes a real Word
    table, inline **bold**/*italic*/`code` markdown becomes real run
    formatting, and everything else stays a normal paragraph. Replaces the
    previous behavior of dumping one flat Normal paragraph per non-blank
    line verbatim, which produced an unreadable wall of text with no
    headings, bullets, or tables, and left literal markdown markers
    (including whole "| a | b |" / "|---|---:|" table rows) in place."""
    lines = [raw_line.strip() for raw_line in (text or "").split("\n")]
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if not line:
            i += 1
            continue

        if _PIPE_ROW_RE.match(line) and i + 1 < n and _is_md_table_separator(lines[i + 1]):
            header_cells = _split_md_table_row(line)
            j = i + 2
            data_rows = []
            while j < n and _PIPE_ROW_RE.match(lines[j]):
                data_rows.append(_split_md_table_row(lines[j]))
                j += 1
            _docx_markdown_table(doc, header_cells, data_rows)
            i = j
            continue

        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            p = doc.add_paragraph(style="List Bullet")
            _add_runs_with_inline_markdown(p, bullet_match.group(1), size=Pt(10.5))
            i += 1
            continue
        if _TOP_HEADING_RE.match(line):
            _docx_heading(doc, _strip_inline_markdown(line), size=13, space_before=14)
            i += 1
            continue
        if _SUB_HEADING_RE.match(line):
            _docx_heading(doc, _strip_inline_markdown(line), size=11.5, color=_HTC_GREY, space_before=10)
            i += 1
            continue
        p = doc.add_paragraph()
        _add_runs_with_inline_markdown(p, line)
        i += 1


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
    # "Question asked"/"PI3's answer"/"Appendix" are the report's top-level
    # sections, so all three sit at Heading 2 - that leaves Heading 3 free
    # for PI3's own numbered sections ("1. Direct Answer") to nest properly
    # underneath "PI3's answer" instead of sitting as its siblings.
    _docx_heading(doc, "Question asked", size=15)
    doc.add_paragraph(data["question"] or "—")

    _docx_heading(doc, "PI3's answer", size=15)
    _render_ai_answer_body(doc, data["answer"] or "—")

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
        _docx_heading(doc, f"{i}. {tool_name}", size=13, color=_HTC_GREY, space_before=14)
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
