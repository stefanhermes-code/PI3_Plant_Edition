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
from db import (
    AdjustmentConclusion,
    ApprovalRecord,
    PhysicalPropertyResult,
    ProductionRun,
    QualityObservation,
    TrialRecord,
    get_session,
    init_db,
)
from helpers import clickable_table, delete_with_confirm, page_setup

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
else:
    trial_rows = [
        {
            "Trial": f"#{t.id}",
            "Status": t.status,
            "Grade": t.production_run.foam_grade.grade_name,
            "Run": f"#{t.production_run.id}",
            "Objective": t.trial_or_change_objective,
            "What changed": t.what_changed or "",
            "Responsible": t.responsible_person or "",
        }
        for t in trials
    ]
    st.caption("Click a row to edit (and optionally delete) that trial.")
    idx = clickable_table(trial_rows, key="trials_table")
    if idx is not None:
        st.session_state["trial_selected_id"] = trials[idx].id

    selected_trial_id = st.session_state.get("trial_selected_id")
    selected_trial = next((t for t in trials if t.id == selected_trial_id), None) or (
        session.query(TrialRecord).filter(TrialRecord.id == selected_trial_id).first()
        if selected_trial_id else None
    )

    if selected_trial:
        st.divider()
        st.subheader(f"Edit Trial #{selected_trial.id}")
        st.caption(
            "Status, closeout narrative, and review/approval fields are owned by the Adjustment & "
            "Conclusion and Approval & Review pages — edit those there. This form covers the fields "
            "captured when the trial was flagged."
        )
        with st.form(f"edit_trial_{selected_trial.id}"):
            e_run = st.selectbox(
                "Production run *", runs,
                index=next((i for i, r in enumerate(runs) if r.id == selected_trial.production_run_id), 0),
                format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date} · batch {r.batch_reference or '—'}",
                key=f"edit_trial_run_{selected_trial.id}",
            )
            e_objective = st.text_area(
                "Trial or change objective *", value=selected_trial.trial_or_change_objective or "",
                key=f"edit_trial_objective_{selected_trial.id}",
            )
            e_hypothesis = st.text_area("Hypothesis", value=selected_trial.hypothesis or "", key=f"edit_trial_hyp_{selected_trial.id}")
            e_what_changed = st.text_area(
                "What changed vs. baseline", value=selected_trial.what_changed or "", key=f"edit_trial_changed_{selected_trial.id}"
            )
            e_responsible = st.text_input(
                "Responsible person", value=selected_trial.responsible_person or "", key=f"edit_trial_resp_{selected_trial.id}"
            )
            if st.form_submit_button("Save changes"):
                if not e_objective.strip():
                    st.error("Trial objective is required.")
                else:
                    selected_trial.production_run_id = e_run.id
                    selected_trial.trial_or_change_objective = e_objective
                    selected_trial.hypothesis = e_hypothesis
                    selected_trial.what_changed = e_what_changed
                    selected_trial.responsible_person = e_responsible
                    session.commit()
                    st.success("Trial updated.")
                    st.rerun()

        linked_counts = {
            "quality issue(s)": session.query(QualityObservation).filter(QualityObservation.trial_record_id == selected_trial.id).count(),
            "quality test result(s)": session.query(PhysicalPropertyResult).filter(PhysicalPropertyResult.trial_record_id == selected_trial.id).count(),
            "adjustment & conclusion record(s)": session.query(AdjustmentConclusion).filter(AdjustmentConclusion.trial_record_id == selected_trial.id).count(),
            "review record(s)": session.query(ApprovalRecord).filter(ApprovalRecord.trial_record_id == selected_trial.id).count(),
        }
        linked_bits = [f"{v} {k}" for k, v in linked_counts.items() if v]
        if linked_bits:
            warning = (
                "This trial is referenced by " + ", ".join(linked_bits) + ". Deleting it will unlink those "
                "records (they stay, the trial reference is cleared), not delete them."
            )
        else:
            warning = "No other records reference this trial — deleting it is safe."

        def _do_delete_trial(_session=session, _id=selected_trial.id):
            _session.query(QualityObservation).filter(QualityObservation.trial_record_id == _id).update(
                {"trial_record_id": None}, synchronize_session="fetch"
            )
            _session.query(PhysicalPropertyResult).filter(PhysicalPropertyResult.trial_record_id == _id).update(
                {"trial_record_id": None}, synchronize_session="fetch"
            )
            _session.query(AdjustmentConclusion).filter(AdjustmentConclusion.trial_record_id == _id).update(
                {"trial_record_id": None}, synchronize_session="fetch"
            )
            _session.query(ApprovalRecord).filter(ApprovalRecord.trial_record_id == _id).update(
                {"trial_record_id": None}, synchronize_session="fetch"
            )
            _session.query(TrialRecord).filter(TrialRecord.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("trial_selected_id", None)

        delete_with_confirm(
            f"Trial #{selected_trial.id}", _do_delete_trial, key_prefix=f"trial_{selected_trial.id}",
            extra_warning=warning,
        )

        if st.button("Clear selection", key="clear_trial_selection"):
            st.session_state.pop("trial_selected_id", None)
            st.rerun()

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

