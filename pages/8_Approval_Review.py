"""Screen 9: Approval and Review

Enforces the critical non-negotiable rule:
A trial or recipe change cannot be marked Closed unless conclusion,
reuse_recommendation, reviewed_by, approved_by, and date_closed are all
present. This screen is the only place a trial can move to "Closed".
"""

import datetime as dt

import streamlit as st

from db import APPROVAL_STATUSES, ApprovalRecord, TrialRecord, get_session, init_db
from auth import current_user, logout_button, require_login
from helpers import page_setup, show_advisory_footer

page_setup("Approval & Review")
init_db()
require_login()
logout_button()

st.title("Approval and Review")
session = get_session()
user = current_user()

pending_trials = (
    session.query(TrialRecord)
    .filter(TrialRecord.status.in_(["Open", "Pending Closure"]))
    .order_by(TrialRecord.created_at.desc())
    .all()
)

if not pending_trials:
    st.info("No trials awaiting review.")
    st.stop()

trial = st.selectbox(
    "Trial",
    pending_trials,
    format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name} ({t.status})",
)

st.markdown(f"**Objective:** {trial.trial_or_change_objective}")
st.markdown(f"**Conclusion:** {trial.conclusion or '_not yet written — see Adjustment & Conclusion screen_'}")
st.markdown(f"**Reuse recommendation:** {trial.reuse_recommendation or '_missing_'}")

missing = trial.missing_closeout_fields()
if missing:
    st.warning(
        f"This trial is missing: {', '.join(missing)}. It cannot be closed until every "
        f"closeout field is complete."
    )

st.divider()
st.subheader("Record review and approval")

with st.form("approval_form"):
    reviewed_by = st.text_input("Reviewed by *", value=trial.reviewed_by or "")
    approved_by = st.text_input("Approved by *", value=trial.approved_by or "")
    approval_status = st.selectbox("Approval status", APPROVAL_STATUSES, index=1)
    review_notes = st.text_area("Review notes")
    date_reviewed = st.date_input("Date reviewed", value=dt.date.today())
    date_approved = st.date_input("Date approved", value=dt.date.today())
    submitted = st.form_submit_button("Save review")
    if submitted:
        if not reviewed_by or not approved_by:
            st.error("Reviewed by and approved by are both required.")
        else:
            session.add(
                ApprovalRecord(
                    production_run_id=trial.production_run_id,
                    trial_record_id=trial.id,
                    reviewed_by=reviewed_by,
                    approved_by=approved_by,
                    approval_status=approval_status,
                    review_notes=review_notes,
                    date_reviewed=date_reviewed,
                    date_approved=date_approved,
                )
            )
            trial.reviewed_by = reviewed_by
            trial.approved_by = approved_by
            session.commit()
            st.success("Review recorded.")
            st.rerun()

st.divider()
st.subheader("Close trial")

still_missing = trial.missing_closeout_fields()
if still_missing:
    st.error(f"Cannot close — missing: {', '.join(still_missing)}")
    st.button("Close trial", disabled=True)
else:
    if st.button("Close trial", type="primary"):
        trial.date_closed = dt.date.today()
        trial.status = "Closed"
        session.commit()
        st.success(f"Trial #{trial.id} closed by {user['display_name']}.")
        st.rerun()

if trial.approval_records:
    st.divider()
    st.subheader("Review history")
    st.dataframe(
        [
            {
                "Reviewed by": a.reviewed_by,
                "Approved by": a.approved_by,
                "Status": a.approval_status,
                "Notes": a.review_notes,
                "Date reviewed": a.date_reviewed,
                "Date approved": a.date_approved,
            }
            for a in trial.approval_records
        ],
        hide_index=True,
        use_container_width=True,
    )

show_advisory_footer()
