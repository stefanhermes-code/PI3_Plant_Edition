"""Screen 8: Adjustment and Conclusion

Approved terminology: "Adjustment and Conclusion History", not "Corrective
Action Memory". This screen captures individual adjustments AND the trial's
closeout narrative (result, physical property outcome, conclusion, reuse
recommendation). Completing this moves a trial to "Pending Closure" — final
closure still requires review + approval on the Approval & Review screen.
"""

import streamlit as st

from db import CONFIDENCE_LEVELS, AdjustmentConclusion, TrialRecord, get_session, init_db
from auth import logout_button, require_login
from helpers import clickable_table, confidence_badge, delete_with_confirm, page_setup

page_setup("Adjustment & Conclusion")
init_db()
require_login()
logout_button()

st.title("Adjustment and Conclusion History")
session = get_session()

trials = session.query(TrialRecord).filter(TrialRecord.status != "Closed").order_by(TrialRecord.created_at.desc()).all()
if not trials:
    st.info("No open trials. Create one on the Trial / Experiment page.")
    st.stop()

trial = st.selectbox(
    "Select trial",
    trials,
    format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name} ({t.status})",
)

st.divider()
st.subheader("Log an adjustment")

with st.form(f"add_adjustment_{trial.id}"):
    parameter_changed = st.text_input("Parameter changed (process)")
    formulation_changed = st.checkbox("Formulation changed?")
    material_changed = st.text_input("Material changed (if any)")
    result = st.text_area("Result of this specific adjustment")
    reuse_recommendation = st.text_area("Reuse recommendation for this adjustment")
    confidence_level = st.selectbox("Confidence level", CONFIDENCE_LEVELS, index=2)
    follow_up_required = st.checkbox("Follow-up required?")
    created_by = st.text_input("Logged by")
    submitted = st.form_submit_button("Save adjustment")
    if submitted:
        session.add(
            AdjustmentConclusion(
                production_run_id=trial.production_run_id,
                trial_record_id=trial.id,
                parameter_changed=parameter_changed,
                formulation_changed=formulation_changed,
                material_changed=material_changed,
                result=result,
                reuse_recommendation=reuse_recommendation,
                confidence_level=confidence_level,
                follow_up_required=follow_up_required,
                created_by=created_by,
            )
        )
        session.commit()
        st.success("Adjustment logged.")
        st.rerun()

if trial.adjustment_conclusions:
    adj_rows = [
        {
            "Parameter changed": a.parameter_changed,
            "Formulation changed": a.formulation_changed,
            "Material changed": a.material_changed,
            "Result": a.result,
            "Confidence": a.confidence_level,
            "Follow-up required": a.follow_up_required,
            "Logged by": a.created_by,
        }
        for a in trial.adjustment_conclusions
    ]
    st.caption("Click a row to edit (and optionally delete) that adjustment.")
    idx = clickable_table(adj_rows, key=f"adj_table_{trial.id}")
    if idx is not None:
        st.session_state["adj_selected_id"] = trial.adjustment_conclusions[idx].id

    selected_adj_id = st.session_state.get("adj_selected_id")
    selected_adj = session.query(AdjustmentConclusion).filter(AdjustmentConclusion.id == selected_adj_id).first()

    if selected_adj:
        st.markdown("**Edit adjustment**")
        with st.form(f"edit_adj_{selected_adj.id}"):
            e_parameter = st.text_input(
                "Parameter changed (process)", value=selected_adj.parameter_changed or "",
                key=f"edit_adj_param_{selected_adj.id}",
            )
            e_formulation_changed = st.checkbox(
                "Formulation changed?", value=selected_adj.formulation_changed, key=f"edit_adj_formchg_{selected_adj.id}"
            )
            e_material_changed = st.text_input(
                "Material changed (if any)", value=selected_adj.material_changed or "",
                key=f"edit_adj_material_{selected_adj.id}",
            )
            e_result = st.text_area("Result of this specific adjustment", value=selected_adj.result or "", key=f"edit_adj_result_{selected_adj.id}")
            e_reuse = st.text_area(
                "Reuse recommendation for this adjustment", value=selected_adj.reuse_recommendation or "",
                key=f"edit_adj_reuse_{selected_adj.id}",
            )
            e_confidence = st.selectbox(
                "Confidence level", CONFIDENCE_LEVELS,
                index=CONFIDENCE_LEVELS.index(selected_adj.confidence_level) if selected_adj.confidence_level in CONFIDENCE_LEVELS else 2,
                key=f"edit_adj_confidence_{selected_adj.id}",
            )
            e_followup = st.checkbox(
                "Follow-up required?", value=selected_adj.follow_up_required, key=f"edit_adj_followup_{selected_adj.id}"
            )
            e_created_by = st.text_input("Logged by", value=selected_adj.created_by or "", key=f"edit_adj_by_{selected_adj.id}")
            if st.form_submit_button("Save changes"):
                selected_adj.parameter_changed = e_parameter
                selected_adj.formulation_changed = e_formulation_changed
                selected_adj.material_changed = e_material_changed
                selected_adj.result = e_result
                selected_adj.reuse_recommendation = e_reuse
                selected_adj.confidence_level = e_confidence
                selected_adj.follow_up_required = e_followup
                selected_adj.created_by = e_created_by
                session.commit()
                st.success("Adjustment updated.")
                st.rerun()

        def _do_delete_adj(_session=session, _id=selected_adj.id):
            _session.query(AdjustmentConclusion).filter(AdjustmentConclusion.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("adj_selected_id", None)

        delete_with_confirm(
            "this adjustment", _do_delete_adj, key_prefix=f"adj_{selected_adj.id}",
            extra_warning="This is a leaf record — deleting it has no other effects.",
        )

        if st.button("Clear selection", key="clear_adj_selection"):
            st.session_state.pop("adj_selected_id", None)
            st.rerun()

st.divider()
st.subheader("Trial closeout narrative")
st.caption(
    "These fields are mandatory before the trial can be closed. Reviewed_by, "
    "approved_by, and date_closed are set on the Approval & Review screen."
)

with st.form(f"closeout_{trial.id}"):
    result_against_target = st.text_area("Result against target", value=trial.result_against_target or "")
    physical_property_outcome = st.text_area(
        "Physical property outcome summary", value=trial.physical_property_outcome or ""
    )
    conclusion = st.text_area("Conclusion *", value=trial.conclusion or "")
    reuse_recommendation = st.text_area("Reuse recommendation *", value=trial.reuse_recommendation or "")
    submitted = st.form_submit_button("Save closeout narrative")
    if submitted:
        if not conclusion or not reuse_recommendation:
            st.error("Conclusion and reuse recommendation are both required.")
        else:
            trial.result_against_target = result_against_target
            trial.physical_property_outcome = physical_property_outcome
            trial.conclusion = conclusion
            trial.reuse_recommendation = reuse_recommendation
            if trial.status == "Open":
                trial.status = "Pending Closure"
            session.commit()
            st.success("Closeout narrative saved. Trial is now ready for review and approval.")
            st.rerun()

