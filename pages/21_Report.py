"""Screen: Report

"Report" is one of PI3 Plant Edition's own standard, always-included
capabilities (Search, Compare, Retrieve, Structure, Report, Review and
Approval - see pages/10_PI3_AI_Connectivity.py). This screen was the gap:
it did not exist as a dedicated page before. Not gated behind PI3
connectivity - every logged-in user can generate these.

Four report types, each with an in-app preview plus a Word
download button: Batch Release / Conformance Record, Plant / Period
Summary Report, Trial Closeout Report, and Sample Certificate of Analysis
(added 2026-08-04). All data assembly and file rendering lives in
reports.py; this page is just selectors + st.download_button wiring.

Batch Release / Conformance Record lives here (rather than on the
Production Run page itself) because selecting its subject is a single
simple choice - pick one run from a dropdown - same as the other two
reports on this page. A report needing a more involved, multi-field
selection first (date range, foam grade, etc.) belongs on its own page
instead, next to where that selection naturally happens - see reports.py's
Recipe / Formulation Record and Where Used Report, both on the Recipes
page. Replaced the older, flatter build_run_report_data()-based version
2026-08-04 as part of the app-wide Reports redesign.
"""

import datetime as dt

import pandas as pd
import streamlit as st
from sqlalchemy import or_

from auth import current_user, logout_button, require_login
from db import (
    CustomerTrial,
    FoamGrade,
    OptimizationTrial,
    Plant,
    ProductFamily,
    ProductionRun,
    Sample,
    get_session,
    init_db,
)
from helpers import log_export_click, page_setup, render_data_table, render_function_action_intro
from tenant_scope import (
    apply_scope,
    company_picker,
    customer_trial_ids_for_company,
    family_ids_for_plants,
    optimization_trial_ids_for_company,
    plant_ids_for_company,
    run_ids_for_company,
)
import reports

page_setup("Report")
init_db()
require_login()
logout_button()

st.title("Report")
render_function_action_intro(
    function_text=(
        "Generates four standard report types - one production run's conformance record, a "
        "plant/period summary, a closed trial's formal writeup, or one sample's certificate of "
        "analysis - each with an in-app preview plus Word download. Every logged-in user "
        "can generate these; it's not gated behind PI3 connectivity."
    ),
    action_text=(
        "Pick the tab for the report you need, select the run, plant/period, trial, or sample it "
        "should cover, and preview it before downloading. Use the Batch Release / Conformance "
        "Record to see whether a single batch met spec (and what else was going on if it didn't), "
        "the Plant/Period Summary for a broader review across a date range, the Trial Closeout "
        "Report once a customer or optimization trial is formally closed, and the Sample "
        "Certificate of Analysis for one sample's full result-and-recipe traceability."
    ),
)
session = get_session()
user = current_user()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="report_company_filter"
)
active_company_id = company.id if company else None
scoped_plant_ids = plant_ids_for_company(session, active_company_id)
scoped_family_ids = family_ids_for_plants(session, scoped_plant_ids)
scoped_run_ids = run_ids_for_company(session, active_company_id)
scoped_customer_trial_ids = customer_trial_ids_for_company(session, active_company_id)
scoped_optimization_trial_ids = optimization_trial_ids_for_company(session, active_company_id)

tab_run, tab_period, tab_trial, tab_sample = st.tabs(
    ["Batch Release / Conformance Record", "Plant / Period Summary", "Trial Closeout Report",
     "Sample Certificate of Analysis"]
)

# ---------------------------------------------------------------------------
# 1. Batch Release / Conformance Record
#
# "Did this batch meet spec, and is there anything on record to flag" -
# the recipe used in full, a rolled-up quality verdict, and any quality
# issues. If a result failed or an issue was recorded, the report widens
# automatically to pull relevant context from every other tab on the
# Production Run page (Setup vs. Finalized process settings, actual
# component stream readings, and Production Events) - a clean run stays
# a short document, a flagged one shows what else was going on.
# ---------------------------------------------------------------------------
with tab_run:
    runs = (
        apply_scope(session.query(ProductionRun), ProductionRun.id, scoped_run_ids)
        .order_by(ProductionRun.run_date.desc())
        .all()
    )
    if not runs:
        st.info("No production runs recorded yet.")
    else:
        run = st.selectbox(
            "Production run",
            runs,
            format_func=lambda r: (
                f"Run #{r.id} — {r.foam_grade.grade_name if r.foam_grade else '—'} "
                f"({r.run_date}) · {r.batch_reference or 'no batch ref'}"
            ),
            key="report_run_select",
        )
        data = reports.build_batch_release_record_data(session, run.id)

        st.subheader(f"Run #{data['run_id']} — {data['foam_grade']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Quality verdict", data["quality_verdict"])
        c2.metric("Recipe version used", data["recipe_version_label"])
        c3.metric("Plant", data["plant"])
        st.write(
            f"**Run date:** {data['run_date']} · **Batch reference:** {data['batch_reference']} · "
            f"**Machine:** {data['machine']}"
        )
        if data["has_flags"]:
            st.warning("Flagged: " + "; ".join(data["flag_reasons"]))

        st.write("**Recipe used**")
        render_data_table(pd.DataFrame(data["recipe_components"] or [{"—": "No data recorded"}]))
        st.write("**Quality test results**")
        render_data_table(pd.DataFrame(data["quality_results"] or [{"—": "No data recorded"}]))
        st.write("**Quality issues**")
        render_data_table(pd.DataFrame(data["quality_issues"] or [{"—": "No data recorded"}]))

        if data["has_flags"]:
            st.write("**Process setting changes (Setup → Finalized)**")
            render_data_table(pd.DataFrame(data["setup_deviations"] or [{"—": "No changes"}]))
            if data["fallplate_deviations"]:
                st.write("**Fall-plate position changes (Setup → Finalized)**")
                render_data_table(pd.DataFrame(data["fallplate_deviations"]))
            st.write("**Component stream readings (Finalized phase)**")
            render_data_table(pd.DataFrame(data["stream_readings"] or [{"—": "No data recorded"}]))
            if data["stream_calibration_flags"]:
                st.warning(
                    "Non-valid calibration status recorded for: " + ", ".join(data["stream_calibration_flags"])
                )
            st.write("**Production events during this run**")
            render_data_table(pd.DataFrame(data["production_events"] or [{"—": "No data recorded"}]))

        st.download_button(
            "Download Word", data=reports.render_batch_release_record_docx(data),
            file_name=f"run_{data['run_id']}_batch_release_record.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="run_report_docx",
            on_click=log_export_click, args=("batch_release_record_docx",),
            kwargs={"description": f"Run #{data['run_id']}"},
        )

# ---------------------------------------------------------------------------
# 2. Plant / Period Summary Report
# ---------------------------------------------------------------------------
with tab_period:
    p1, p2, p3, p4 = st.columns(4)
    plants = apply_scope(session.query(Plant), Plant.id, scoped_plant_ids).all()
    with p1:
        plant = st.selectbox(
            "Plant", [None] + plants, format_func=lambda p: "All plants" if p is None else p.name,
            key="report_period_plant",
        )
    families_q = apply_scope(session.query(ProductFamily), ProductFamily.id, scoped_family_ids)
    if plant:
        families_q = families_q.filter(ProductFamily.plant_id == plant.id)
    with p2:
        family = st.selectbox(
            "Product family", [None] + families_q.all(),
            format_func=lambda f: "All families" if f is None else f.name,
            key="report_period_family",
        )
    with p3:
        date_from = st.date_input(
            "From", value=dt.date.today() - dt.timedelta(days=90), key="report_period_from"
        )
    with p4:
        date_to = st.date_input("To", value=dt.date.today(), key="report_period_to")

    data = reports.build_period_summary_data(
        session,
        plant_id=plant.id if plant else None,
        product_family_id=family.id if family else None,
        date_from=date_from,
        date_to=date_to,
        allowed_plant_ids=scoped_plant_ids,
    )

    st.subheader(f"{data['plant']} · {data['product_family']}")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Production runs", data["total_runs"])
    k2.metric("Quality test pass rate", f"{data['pass_rate']}%" if data["pass_rate"] is not None else "—")
    k3.metric("Quality issues", data["total_quality_issues"])
    k4.metric("Recurring quality issues", data["recurring_issues"])

    st.write("**Production runs in range**")
    render_data_table(pd.DataFrame(data["runs"] or [{"—": "No data recorded"}]))
    st.write("**Quality issues in range**")
    render_data_table(pd.DataFrame(data["quality_issues"] or [{"—": "No data recorded"}]))
    st.write("**Breakdown by foam grade**")
    render_data_table(pd.DataFrame(data["grade_breakdown"] or [{"—": "No data recorded"}]))

    period_label = f"{date_from}_to_{date_to}"
    st.download_button(
        "Download Word", data=reports.render_period_summary_docx(data),
        file_name=f"period_summary_{period_label}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key="period_report_docx",
        on_click=log_export_click, args=("period_summary_report_docx",),
        kwargs={"description": f"{data['plant']} · {data['product_family']} · {period_label}"},
    )

# ---------------------------------------------------------------------------
# 3. Trial Closeout Report
# ---------------------------------------------------------------------------
with tab_trial:
    source_type = st.radio(
        "Trial type", ["Customer Trial", "Optimization Trial"],
        horizontal=True, key="report_trial_source_type",
    )
    if source_type == "Customer Trial":
        closed_trials = (
            apply_scope(session.query(CustomerTrial), CustomerTrial.id, scoped_customer_trial_ids)
            .filter(CustomerTrial.status == "Closed")
            .order_by(CustomerTrial.date_closed.desc())
            .all()
        )
    else:
        closed_trials = (
            apply_scope(session.query(OptimizationTrial), OptimizationTrial.id, scoped_optimization_trial_ids)
            .filter(OptimizationTrial.status == "Closed")
            .order_by(OptimizationTrial.date_closed.desc())
            .all()
        )

    if not closed_trials:
        st.info("No closed trials yet - a trial must be closed before its report can be generated.")
    else:
        trial = st.selectbox(
            "Closed trial",
            closed_trials,
            format_func=lambda t: (
                f"#{t.id} — {t.foam_grade.grade_name if t.foam_grade else '—'} "
                f"(closed {t.date_closed})"
            ),
            key="report_trial_select",
        )
        data = reports.build_trial_report_data(session, source_type, trial.id)

        st.subheader(f"{data['source_type']} #{data['trial_id']} — {data['foam_grade']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Status", data["status"])
        c2.metric("Plant", data["plant"])
        c3.metric("Date closed", str(data["date_closed"]))

        st.write(f"**Responsible:** {data['responsible_person']} · **Trial date:** {data['trial_date']} · **Batch reference:** {data['batch_reference']}")

        for label, value in data["narrative_fields"]:
            st.write(f"**{label}:** {value}")

        if data["notes"]:
            st.write(f"**Notes:** {data['notes']}")

        st.write(f"**Reviewed by:** {data['reviewed_by']} · **Approved by:** {data['approved_by']}")

        st.write("**Quality issues observed**")
        render_data_table(pd.DataFrame(data["quality_issues"] or [{"—": "No data recorded"}]))

        st.download_button(
            "Download Word", data=reports.render_trial_report_docx(data),
            file_name=f"trial_{data['trial_id']}_closeout_report.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="trial_report_docx",
            on_click=log_export_click, args=("trial_closeout_report_docx",),
            kwargs={"description": f"Trial #{data['trial_id']}"},
        )

# ---------------------------------------------------------------------------
# 4. Sample Certificate of Analysis
#
# Picking one sample is a single simple choice, same placement logic as
# the other three reports on this page. Not customer-facing as-is - it
# includes the full recipe formulation used, same caveat as Batch Release
# Record and Recipe Formulation Record.
# ---------------------------------------------------------------------------
with tab_sample:
    st.caption(
        "Full traceability record for one sample: which run/trial it came from, the sample itself, "
        "the recipe used (full formulation - internal use only, not customer-facing), its quality "
        "test results, and the pass/fail assessment."
    )
    if active_company_id is None:
        sample_query = session.query(Sample)
    else:
        sample_query = session.query(Sample).filter(
            or_(
                Sample.production_run_id.in_(scoped_run_ids or []),
                Sample.customer_trial_id.in_(scoped_customer_trial_ids or []),
                Sample.optimization_trial_id.in_(scoped_optimization_trial_ids or []),
            )
        )
    all_samples = sample_query.order_by(Sample.id.desc()).all()

    def _sample_option_label(s):
        if s.production_run_id is not None:
            src = f"Production Run #{s.production_run_id}"
        elif s.customer_trial_id is not None:
            src = f"Customer Trial #{s.customer_trial_id}"
        elif s.optimization_trial_id is not None:
            src = f"Optimization Trial #{s.optimization_trial_id}"
        else:
            src = "—"
        return f"Sample #{s.id} — {src} · Zone: {s.zone_label or '—'}" + (f" · {s.sample_ts:%Y-%m-%d}" if s.sample_ts else "")

    if not all_samples:
        st.info("No samples recorded yet.")
    else:
        sample = st.selectbox(
            "Sample", all_samples, format_func=_sample_option_label, key="report_sample_select",
        )
        data = reports.build_sample_certificate_data(session, sample.id)

        st.subheader(f"Sample #{data['sample_id']} — {data['foam_grade']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Overall verdict", data["overall_verdict"])
        c2.metric("Zone", data["zone_label"])
        c3.metric("Plant", data["plant"])

        st.write("**Sample source**")
        for label, value in data["header_fields"]:
            st.write(f"**{label}:** {value}")
        st.write(f"**Sampled:** {data['sample_ts']}")
        if data["sample_notes"]:
            st.write(f"**Sample notes:** {data['sample_notes']}")

        st.write(
            f"**Recipe used:** {data['recipe_version_label']} · **Approval status:** "
            f"{data['recipe_approval_status']} · **Effective:** {data['recipe_effective_date']}"
        )
        st.write("**Formulation** (internal use only)")
        render_data_table(pd.DataFrame(data["recipe_components"] or [{"—": "No data recorded"}]))

        st.write(f"**Quality test results** — Pass: {data['pass_count']} · Fail: {data['fail_count']}")
        render_data_table(pd.DataFrame(data["quality_results"] or [{"—": "No data recorded"}]))

        st.download_button(
            "Download Word", data=reports.render_sample_certificate_docx(data),
            file_name=f"sample_{data['sample_id']}_certificate.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="sample_cert_docx",
            on_click=log_export_click, args=("sample_certificate_docx",),
            kwargs={"description": f"Sample #{data['sample_id']}"},
        )
