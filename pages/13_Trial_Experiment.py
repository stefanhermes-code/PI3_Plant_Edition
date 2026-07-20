"""Screen: Trial / Experiment (optional)

Production runs are the primary, self-sufficient record in PI3 — recipe,
machine parameters, and quality results all attach directly to a run and
need nothing else. A "trial" is a separate, optional annotation you attach
to a run only when it is genuinely a deliberate change/experiment with a
hypothesis, a closeout narrative, and a formal review/approval requirement.
Most production runs never need this page.

Quality data (physical property results, quality observations, adjustment
conclusions, approvals) can optionally reference a trial for cross-linking,
but always belongs to a production run first and foremost.
"""

import streamlit as st

from auth import logout_button, require_login
from db import ProductionRun, TrialRecord, get_session, init_db
from helpers import page_setup, show_advisory_footer

page_setup("Trial / Experiment")
init_db()
require_login()
logout_button()

st.title("Trial / Experiment (optional)")
st.caption(
    "Flag a production run as a deliberate trial or change investigation. This is optional — "
    "only use it when there's a real hypothesis and a closeout/approval requirement, not for "
    "routine batches."
)
session = get_session()

runs = session.query(ProductionRun).order_by(ProductionRun.created_at.desc()).all()
if not runs:
    st.warning("Create a production run first (Production Run page).")
    st.stop()

with st.expander("Flag a run as a trial / experiment", expanded=False):
    with st.form("add_trial"):
        run = st.selectbox(
            "Production run *",
            runs,
            format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date} · batch {r.batch_reference or '—'}",
        )
        objective = st.text_area("Trial or change objective *")
        hypothesis = st.text_area("Hypothesis")
        what_changed = st.text_area("What changed vs. baseline")
        responsible_person = st.text_input("Responsible person")
        submitted = st.form_submit_button("Save trial")
        if submitted:
            if not objective:
                st.error("Trial objective is required.")
            else:
                trial = TrialRecord(
                    production_run_id=run.id,
                    trial_or_change_objective=objective,
                    hypothesis=hypothesis,
                    what_changed=what_changed,
                    responsible_person=responsible_person,
                    status="Open",
                )
                session.add(trial)
                session.commit()
                st.success("Trial created and linked to the run.")
                st.rerun()

st.divider()
st.subheader("Trials")

status_filter = st.multiselect(
    "Status filter", ["Open", "Pending Closure", "Closed"], default=["Open", "Pending Closure", "Closed"]
)
trials = session.query(TrialRecord).order_by(TrialRecord.created_at.desc()).all()
trials = [t for t in trials if t.status in status_filter]

if not trials:
    st.info("No trials match the current filter.")

for t in trials:
    run = t.production_run
    grade = run.foam_grade
    status_icon = {"Open": "🟡", "Pending Closure": "🟠", "Closed": "🟢"}.get(t.status, "⚪")
    with st.container(border=True):
        st.markdown(
            f"{status_icon} **Trial #{t.id}** — {grade.grade_name} · "
            f"recipe {run.recipe_version.version_label} · run {run.run_date} · status `{t.status}`"
        )
        st.write(f"**Objective:** {t.trial_or_change_objective}")
        if t.what_changed:
            st.caption(f"Changed: {t.what_changed}")
        if t.status != "Closed":
            missing = t.missing_closeout_fields()
            if missing:
                st.caption(f"⏳ Missing before closure: {', '.join(missing)}")
        else:
            st.caption(f"Closed {t.date_closed} — reviewed by {t.reviewed_by}, approved by {t.approved_by}")

show_advisory_footer()
