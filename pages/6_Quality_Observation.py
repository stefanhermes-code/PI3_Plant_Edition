"""Screen 7: Quality Observation

Approved terminology: "Quality Observation", not "Defect Module".

Keyed primarily to the production run — routine batches get quality
observations too, not just formal trials. Linking to a trial is optional.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from db import CONFIDENCE_LEVELS, ProductionRun, QualityObservation, TrialRecord, get_session, init_db
from auth import logout_button, require_login
from helpers import confidence_badge, csv_excel_uploader, page_setup, show_advisory_footer

OBSERVATION_REQUIRED_COLUMNS = ["production_run_id", "observation_type"]
OBSERVATION_OPTIONAL_COLUMNS = [
    "trial_record_id", "severity", "frequency", "location_in_block", "suspected_cause",
    "confidence_level", "product_impact", "customer_impact", "notes", "observed_at",
]

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

tab_obs_manual, tab_obs_import = st.tabs(["Add quality observation", "CSV / Excel import"])

with tab_obs_manual:
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
            observation_type = st.text_input(
                "Observation type * (e.g. hardness drift, shrinkage, collapse, splitting)"
            )
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

with tab_obs_import:
    obs_df, obs_filename = csv_excel_uploader(
        OBSERVATION_REQUIRED_COLUMNS, OBSERVATION_OPTIONAL_COLUMNS, key="observation_upload"
    )
    if obs_df is not None:
        run_ids = {r.id for r in runs}
        trials_all = {t.id: t for t in session.query(TrialRecord).all()}
        good_rows, bad_rows = [], []
        for _, row in obs_df.iterrows():
            try:
                run_ok = row.get("production_run_id") in run_ids
                trial_val = row.get("trial_record_id")
                trial_ok = pd.isna(trial_val) or int(trial_val) in trials_all
                ok = bool(run_ok and trial_ok and str(row.get("observation_type", "")).strip())
            except (TypeError, ValueError):
                ok = False
            if ok:
                good_rows.append(row)
            else:
                bad_rows.append(row)

        st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
        if bad_rows:
            st.warning(
                "Flagged rows reference an unknown production_run_id/trial_record_id or have no "
                "observation_type."
            )
            st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

        if good_rows and st.button("Confirm import", key="confirm_observation_import"):
            for row in good_rows:
                trial_val = row.get("trial_record_id")
                severity_val = str(row.get("severity", "") or "").strip()
                frequency_val = str(row.get("frequency", "") or "").strip()
                confidence_val = str(row.get("confidence_level", "") or "").strip()
                observed_val = pd.to_datetime(row.get("observed_at"), errors="coerce")
                session.add(
                    QualityObservation(
                        production_run_id=int(row["production_run_id"]),
                        trial_record_id=int(trial_val) if not pd.isna(trial_val) else None,
                        observation_type=str(row["observation_type"]).strip(),
                        severity=severity_val if severity_val in ["Low", "Medium", "High"] else "Low",
                        frequency=frequency_val if frequency_val in ["One-off", "Recurring"] else "One-off",
                        location_in_block=str(row.get("location_in_block", "") or ""),
                        suspected_cause=str(row.get("suspected_cause", "") or ""),
                        confidence_level=confidence_val if confidence_val in CONFIDENCE_LEVELS else "Unconfirmed",
                        product_impact=str(row.get("product_impact", "") or ""),
                        customer_impact=str(row.get("customer_impact", "") or ""),
                        notes=str(row.get("notes", "") or ""),
                        observed_at=observed_val.date() if not pd.isna(observed_val) else dt.date.today(),
                    )
                )
            session.commit()
            st.success(f"Imported {len(good_rows)} quality observation(s) from {obs_filename}.")
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
