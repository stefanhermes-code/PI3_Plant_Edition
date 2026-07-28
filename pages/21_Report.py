"""Screen: Report

"Report" is one of PI3 Plant Edition's own standard, always-included
capabilities (Search, Compare, Retrieve, Structure, Report, Review and
Approval - see pages/10_PI3_AI_Connectivity.py). This screen was the gap:
it did not exist as a dedicated page before. Not gated behind PI3
connectivity - every logged-in user can generate these.

Three report types, each with an in-app preview plus PDF and Excel
download buttons: Production Run Report, Plant / Period Summary Report,
and Trial Closeout Report. All data assembly and file rendering lives in
reports.py; this page is just selectors + st.download_button wiring.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from auth import logout_button, require_login
from db import FoamGrade, Plant, ProductFamily, ProductionRun, TrialRecord, get_session, init_db
from helpers import page_setup, render_data_table
import reports

page_setup("Report")
init_db()
require_login()
logout_button()

st.title("Report")
st.caption(
    "Generate a report for a single production run, a plant/period summary, or a closed "
    "trial's formal writeup. Preview it here, then download as PDF or Excel."
)
session = get_session()

tab_run, tab_period, tab_trial = st.tabs(
    ["Production Run Report", "Plant / Period Summary", "Trial Closeout Report"]
)

# ---------------------------------------------------------------------------
# 1. Production Run Report
# ---------------------------------------------------------------------------
with tab_run:
    runs = session.query(ProductionRun).order_by(ProductionRun.run_date.desc()).all()
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
        data = reports.build_run_report_data(session, run.id)

        st.subheader(f"Run #{data['run_id']} — {data['foam_grade']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Plant", data["plant"])
        c2.metric("Recipe version", data["recipe_version"])
        c3.metric("Machine", data["machine"])
        st.write(f"**Run date:** {data['run_date']} · **Batch reference:** {data['batch_reference']}")

        st.write("**Recipe components**")
        render_data_table(pd.DataFrame(data["components"] or [{"—": "No data recorded"}]))
        st.write("**Process settings (by phase)**")
        render_data_table(pd.DataFrame(data["phase_settings"] or [{"—": "No data recorded"}]))
        st.write("**Quality test results**")
        render_data_table(pd.DataFrame(data["quality_results"] or [{"—": "No data recorded"}]))
        st.write("**Quality issues**")
        render_data_table(pd.DataFrame(data["quality_issues"] or [{"—": "No data recorded"}]))
        st.write("**Adjustments & conclusions**")
        render_data_table(pd.DataFrame(data["adjustments"] or [{"—": "No data recorded"}]))
        st.write("**Approvals**")
        render_data_table(pd.DataFrame(data["approvals"] or [{"—": "No data recorded"}]))

        dl1, dl2 = st.columns(2)
        dl1.download_button(
            "Download PDF", data=reports.render_run_report_pdf(data),
            file_name=f"production_run_{data['run_id']}_report.pdf", mime="application/pdf",
            key="run_report_pdf",
        )
        dl2.download_button(
            "Download Excel", data=reports.render_run_report_excel(data),
            file_name=f"production_run_{data['run_id']}_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="run_report_excel",
        )

# ---------------------------------------------------------------------------
# 2. Plant / Period Summary Report
# ---------------------------------------------------------------------------
with tab_period:
    p1, p2, p3, p4 = st.columns(4)
    plants = session.query(Plant).all()
    with p1:
        plant = st.selectbox(
            "Plant", [None] + plants, format_func=lambda p: "All plants" if p is None else p.name,
            key="report_period_plant",
        )
    families_q = session.query(ProductFamily)
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

    dl1, dl2 = st.columns(2)
    period_label = f"{date_from}_to_{date_to}"
    dl1.download_button(
        "Download PDF", data=reports.render_period_summary_pdf(data),
        file_name=f"period_summary_{period_label}.pdf", mime="application/pdf",
        key="period_report_pdf",
    )
    dl2.download_button(
        "Download Excel", data=reports.render_period_summary_excel(data),
        file_name=f"period_summary_{period_label}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="period_report_excel",
    )

# ---------------------------------------------------------------------------
# 3. Trial Closeout Report
# ---------------------------------------------------------------------------
with tab_trial:
    closed_trials = (
        session.query(TrialRecord)
        .filter(TrialRecord.status == "Closed")
        .order_by(TrialRecord.date_closed.desc())
        .all()
    )
    if not closed_trials:
        st.info("No closed trials yet - a trial must be closed before its report can be generated.")
    else:
        trial = st.selectbox(
            "Closed trial",
            closed_trials,
            format_func=lambda t: (
                f"Trial #{t.id} — {t.production_run.foam_grade.grade_name if t.production_run and t.production_run.foam_grade else '—'} "
                f"(closed {t.date_closed})"
            ),
            key="report_trial_select",
        )
        data = reports.build_trial_report_data(session, trial.id)

        st.subheader(f"Trial #{data['trial_id']} — {data['foam_grade']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Status", data["status"])
        c2.metric("Production run", f"#{data['run_id']}" if data["run_id"] else "—")
        c3.metric("Date closed", str(data["date_closed"]))

        st.write(f"**Objective:** {data['objective']}")
        st.write(f"**Hypothesis:** {data['hypothesis']}")
        st.write(f"**What changed:** {data['what_changed']}")
        st.write(f"**Result against target:** {data['result_against_target']}")
        st.write(f"**Physical property outcome:** {data['physical_property_outcome']}")
        st.write(f"**Conclusion:** {data['conclusion']}")
        st.write(f"**Reuse recommendation:** {data['reuse_recommendation']}")
        st.write(f"**Reviewed by:** {data['reviewed_by']} · **Approved by:** {data['approved_by']}")

        st.write("**Quality issues observed**")
        render_data_table(pd.DataFrame(data["quality_issues"] or [{"—": "No data recorded"}]))
        st.write("**Adjustments & conclusions**")
        render_data_table(pd.DataFrame(data["adjustments"] or [{"—": "No data recorded"}]))
        st.write("**Approvals**")
        render_data_table(pd.DataFrame(data["approvals"] or [{"—": "No data recorded"}]))

        dl1, dl2 = st.columns(2)
        dl1.download_button(
            "Download PDF", data=reports.render_trial_report_pdf(data),
            file_name=f"trial_{data['trial_id']}_closeout_report.pdf", mime="application/pdf",
            key="trial_report_pdf",
        )
        dl2.download_button(
            "Download Excel", data=reports.render_trial_report_excel(data),
            file_name=f"trial_{data['trial_id']}_closeout_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="trial_report_excel",
        )
