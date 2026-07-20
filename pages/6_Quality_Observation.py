"""Screen 7: Quality Observation

Approved terminology: "Quality Observation", not "Defect Module".

Keyed primarily to the production run — routine batches get quality
observations too, not just formal trials. Linking to a trial is optional.
"""

import datetime as dt

import streamlit as st

from db import CONFIDENCE_LEVELS, ProductionRun, QualityObservation, TrialRecord, get_session, init_db
from auth import logout_button, require_login
from helpers import confidence_badge, page_setup, show_advisory_footer

page_setup("Quality Observation")
init_db()
require_login()
logout_button()

st.title("Quality Observation")
st.caption(
    "Captures what was observed on a production run — not a defect-tracking or "
    "customer-complaint tool. Used to build a factual, confidence-rated history."
)
session = get_session()

runs = session.query(ProductionRun).order_by(ProductionRun.created_at.desc()).all()
if not runs:
    st.warning("Create a production run first (Production Run page).")
    st.stop()

with st.expander("Add quality observation", expanded=False):
    with st.form("add_observation"):
        run = st.selectbox(
            "Production run *",
            runs,
            format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
        )
        trials_for_run = (
            session.query(TrialRecord).filter(TrialRecord.production_run_id == run.id).all() if run else []
        )
        trial = st.selectbox(
            "Link to trial (optional)",
            [None] + trials_for_run,
            format_func=lambda t: "— not linked to a trial —" if t is None else f"Trial #{t.id} ({t.status})",
        )
        observation_type = st.text_input("Observation type * (e.g. hardness drift, shrinkage, collapse, splitting)")
        c1, c2 = st.columns(2)
        severity = c1.selectbox("Severity", ["Low", "Medium", "High"])
        frequency = c2.selectbox("Frequency", ["One-off", "Recurring"])
        location_in_block = st.text_input("Location in block")
        suspected_cause = st.text_area("Suspected cause")
        confidence_level = st.selectbox("Confidence level *", CONFIDENCE_LEVELS, index=2)
        product_impact = st.text_area("Product impact")
        customer_impact = st.text_area("Customer impact")
        notes = st.text_area("Notes")
        observed_at = st.date_input("Observed on", value=dt.date.today())
        submitted = st.form_submit_button("Save observation")
        if submitted:
            if not observation_type:
                st.error("Observation type is required.")
            else:
                session.add(
                    QualityObservation(
                        production_run_id=run.id,
                        trial_record_id=trial.id if trial else None,
                        observation_type=observation_type,
                        severity=severity,
                        frequency=frequency,
                        location_in_block=location_in_block,
                        suspected_cause=suspected_cause,
                        confidence_level=confidence_level,
                        product_impact=product_impact,
                        customer_impact=customer_impact,
                        notes=notes,
                        observed_at=observed_at,
                    )
                )
                session.commit()
                st.success("Quality observation saved.")
                st.rerun()

st.divider()
st.subheader("Observations")

severity_filter = st.multiselect("Severity filter", ["Low", "Medium", "High"], default=["Low", "Medium", "High"])
observations = (
    session.query(QualityObservation)
    .filter(QualityObservation.severity.in_(severity_filter))
    .order_by(QualityObservation.observed_at.desc())
    .all()
)

for obs in observations:
    run = obs.production_run
    trial_label = f"Trial #{obs.trial_record_id}" if obs.trial_record_id else "no trial"
    with st.container(border=True):
        st.markdown(
            f"**{obs.observation_type}** — {run.foam_grade.grade_name} (run #{run.id}) · "
            f"{trial_label} · {confidence_badge(obs.confidence_level)}"
        )
        st.caption(f"Severity: {obs.severity} | Frequency: {obs.frequency} | Observed: {obs.observed_at}")
        if obs.suspected_cause:
            st.write(f"Suspected cause: {obs.suspected_cause}")

show_advisory_footer()
