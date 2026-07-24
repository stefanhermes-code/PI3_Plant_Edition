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
from helpers import clickable_table, delete_with_confirm, page_setup

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
    approval_rows = [
        {
            "Reviewed by": a.reviewed_by,
            "Approved by": a.approved_by,
            "Status": a.approval_status,
            "Notes": a.review_notes,
            "Date reviewed": a.date_reviewed,
            "Date approved": a.date_approved,
        }
        for a in trial.approval_records
    ]
    st.caption("Click a row to edit (and optionally delete) that review record.")
    idx = clickable_table(approval_rows, key=f"approval_table_{trial.id}")
    if idx is not None:
        st.session_state["approval_selected_id"] = trial.approval_records[idx].id

    selected_approval_id = st.session_state.get("approval_selected_id")
    selected_approval = (
        session.query(ApprovalRecord).filter(ApprovalRecord.id == selected_approval_id).first()
    )

    if selected_approval:
        st.markdown("**Edit review record**")
        with st.form(f"edit_approval_{selected_approval.id}"):
            e_reviewed_by = st.text_input(
                "Reviewed by *", value=selected_approval.reviewed_by or "", key=f"edit_appr_reviewer_{selected_approval.id}"
            )
            e_approved_by = st.text_input(
                "Approved by *", value=selected_approval.approved_by or "", key=f"edit_appr_approver_{selected_approval.id}"
            )
            e_status = st.selectbox(
                "Approval status", APPROVAL_STATUSES,
                index=APPROVAL_STATUSES.index(selected_approval.approval_status) if selected_approval.approval_status in APPROVAL_STATUSES else 1,
                key=f"edit_appr_status_{selected_approval.id}",
            )
            e_notes = st.text_area("Review notes", value=selected_approval.review_notes or "", key=f"edit_appr_notes_{selected_approval.id}")
            e_date_reviewed = st.date_input(
                "Date reviewed", value=selected_approval.date_reviewed or dt.date.today(), key=f"edit_appr_dr_{selected_approval.id}"
            )
            e_date_approved = st.date_input(
                "Date approved", value=selected_approval.date_approved or dt.date.today(), key=f"edit_appr_da_{selected_approval.id}"
            )
            if st.form_submit_button("Save changes"):
                if not e_reviewed_by or not e_approved_by:
                    st.error("Reviewed by and approved by are both required.")
                else:
                    selected_approval.reviewed_by = e_reviewed_by
                    selected_approval.approved_by = e_approved_by
                    selected_approval.approval_status = e_status
                    selected_approval.review_notes = e_notes
                    selected_approval.date_reviewed = e_date_reviewed
                    selected_approval.date_approved = e_date_approved
                    session.commit()
                    st.success("Review record updated.")
                    st.rerun()

        def _do_delete_approval(_session=session, _id=selected_approval.id):
            _session.query(ApprovalRecord).filter(ApprovalRecord.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("approval_selected_id", None)

        delete_with_confirm(
            "this review record", _do_delete_approval, key_prefix=f"approval_{selected_approval.id}",
            extra_warning=(
                "This is a leaf record — deleting it has no other effects (it does not revert the "
                "trial's reviewed_by/approved_by fields or its closed status)."
            ),
        )

        if st.button("Clear selection", key="clear_approval_selection"):
            st.session_state.pop("approval_selected_id", None)
            st.rerun()

