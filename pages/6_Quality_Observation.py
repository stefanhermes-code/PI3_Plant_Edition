"""Screen 7: Quality Issue

Approved terminology: "Quality Issue", not "Defect Module". The
underlying QualityObservation model/table name is unchanged — this is a
display-text rename only.

Keyed primarily to the production run — routine batches get quality
issues too, not just formal trials. Linking to a trial is optional.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from db import CONFIDENCE_LEVELS, ProductionRun, QualityObservation, TrialRecord, get_session, init_db
from auth import logout_button, require_login
from helpers import clickable_table, confidence_badge, csv_excel_uploader, delete_with_confirm, page_setup

OBSERVATION_REQUIRED_COLUMNS = ["production_run_id", "observation_type"]
OBSERVATION_OPTIONAL_COLUMNS = [
    "trial_record_id", "severity", "frequency", "location_in_block", "suspected_cause",
    "confidence_level", "product_impact", "customer_impact", "notes", "observed_at",
]

page_setup("Quality Issue")
init_db()
require_login()
logout_button()

st.title("Quality Issue")
st.caption(
    "Captures what was observed on a production run — not a defect-tracking or "
    "customer-complaint tool. Used to build a factual, confidence-rated history."
)
session = get_session()

runs = session.query(ProductionRun).order_by(ProductionRun.created_at.desc()).all()
if not runs:
    st.warning("Create a production run first (Production Run page).")
    st.stop()

tab_obs_manual, tab_obs_import = st.tabs(["Add quality issue", "CSV / Excel import"])

with tab_obs_manual:
    with st.expander("Add quality issue", expanded=False):
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
                "Issue type * (e.g. hardness drift, shrinkage, collapse, splitting)"
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
            submitted = st.form_submit_button("Save issue")
            if submitted:
                if not observation_type:
                    st.error("Issue type is required.")
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
                    st.success("Quality issue saved.")
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
            st.success(f"Imported {len(good_rows)} quality issue(s) from {obs_filename}.")
            st.rerun()

st.divider()
st.subheader("Quality issues")

severity_filter = st.multiselect("Severity filter", ["Low", "Medium", "High"], default=["Low", "Medium", "High"])
observations = (
    session.query(QualityObservation)
    .filter(QualityObservation.severity.in_(severity_filter))
    .order_by(QualityObservation.observed_at.desc())
    .all()
)

if not observations:
    st.info("No quality issues match this filter.")
else:
    obs_rows = [
        {
            "Issue": o.observation_type,
            "Run": f"#{o.production_run.id}",
            "Grade": o.production_run.foam_grade.grade_name,
            "Trial": f"#{o.trial_record_id}" if o.trial_record_id else "—",
            "Severity": o.severity,
            "Frequency": o.frequency,
            "Confidence": o.confidence_level,
            "Observed": o.observed_at,
        }
        for o in observations
    ]
    st.caption("Click a row to edit (and optionally delete) that quality issue.")
    idx = clickable_table(obs_rows, key="obs_table")
    if idx is not None:
        st.session_state["obs_selected_id"] = observations[idx].id

    selected_id = st.session_state.get("obs_selected_id")
    selected = next((o for o in observations if o.id == selected_id), None) or (
        session.query(QualityObservation).filter(QualityObservation.id == selected_id).first()
        if selected_id else None
    )

    if selected:
        st.divider()
        st.subheader(f"Edit: {selected.observation_type}")
        with st.form(f"edit_obs_{selected.id}"):
            trials_for_edit = (
                session.query(TrialRecord)
                .filter(TrialRecord.production_run_id == selected.production_run_id)
                .all()
            )
            trial_options = [None] + trials_for_edit
            trial_default = next((i for i, t in enumerate(trial_options) if t and t.id == selected.trial_record_id), 0)
            e_trial = st.selectbox(
                "Link to trial (optional)",
                trial_options,
                index=trial_default,
                format_func=lambda t: "— not linked to a trial —" if t is None else f"Trial #{t.id} ({t.status})",
                key=f"edit_obs_trial_{selected.id}",
            )
            e_type = st.text_input("Issue type *", value=selected.observation_type, key=f"edit_obs_type_{selected.id}")
            ec1, ec2 = st.columns(2)
            e_severity = ec1.selectbox(
                "Severity", ["Low", "Medium", "High"],
                index=["Low", "Medium", "High"].index(selected.severity) if selected.severity in ["Low", "Medium", "High"] else 0,
                key=f"edit_obs_severity_{selected.id}",
            )
            e_frequency = ec2.selectbox(
                "Frequency", ["One-off", "Recurring"],
                index=["One-off", "Recurring"].index(selected.frequency) if selected.frequency in ["One-off", "Recurring"] else 0,
                key=f"edit_obs_frequency_{selected.id}",
            )
            e_location = st.text_input("Location in block", value=selected.location_in_block or "", key=f"edit_obs_location_{selected.id}")
            e_cause = st.text_area("Suspected cause", value=selected.suspected_cause or "", key=f"edit_obs_cause_{selected.id}")
            e_confidence = st.selectbox(
                "Confidence level *", CONFIDENCE_LEVELS,
                index=CONFIDENCE_LEVELS.index(selected.confidence_level) if selected.confidence_level in CONFIDENCE_LEVELS else 2,
                key=f"edit_obs_confidence_{selected.id}",
            )
            e_product_impact = st.text_area("Product impact", value=selected.product_impact or "", key=f"edit_obs_pimpact_{selected.id}")
            e_customer_impact = st.text_area("Customer impact", value=selected.customer_impact or "", key=f"edit_obs_cimpact_{selected.id}")
            e_notes = st.text_area("Notes", value=selected.notes or "", key=f"edit_obs_notes_{selected.id}")
            e_observed_at = st.date_input("Observed on", value=selected.observed_at or dt.date.today(), key=f"edit_obs_observed_{selected.id}")
            if st.form_submit_button("Save changes"):
                if not e_type.strip():
                    st.error("Issue type is required.")
                else:
                    selected.trial_record_id = e_trial.id if e_trial else None
                    selected.observation_type = e_type.strip()
                    selected.severity = e_severity
                    selected.frequency = e_frequency
                    selected.location_in_block = e_location
                    selected.suspected_cause = e_cause
                    selected.confidence_level = e_confidence
                    selected.product_impact = e_product_impact
                    selected.customer_impact = e_customer_impact
                    selected.notes = e_notes
                    selected.observed_at = e_observed_at
                    session.commit()
                    st.success("Quality issue updated.")
                    st.rerun()

        def _do_delete_obs(_session=session, _id=selected.id):
            _session.query(QualityObservation).filter(QualityObservation.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("obs_selected_id", None)

        delete_with_confirm(
            "this quality issue", _do_delete_obs, key_prefix=f"obs_{selected.id}",
            extra_warning="This is a leaf record — deleting it has no other effects.",
        )

        if st.button("Clear selection", key="clear_obs_selection"):
            st.session_state.pop("obs_selected_id", None)
            st.rerun()

