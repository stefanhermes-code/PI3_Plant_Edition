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
from helpers import confidence_badge, page_setup, show_advisory_footer

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
    st.dataframe(
        [
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
        ],
        hide_index=True,
        use_container_width=True,
    )

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

show_advisory_footer()
