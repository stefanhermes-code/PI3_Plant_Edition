"""Screen: Customer Trials

One of the two independent lab-trial flows added 2026-08-03 alongside
Optimization Trials (see db.py's CustomerTrial / SAMPLE_SOURCE_TYPES). A
customer trial is initiated by a customer request in light of a sales
opportunity - usually a lab trial in a small box - and is a completely
separate flow from production runs. It is NOT a production run with a
flag on it: it has its own table, its own plant/foam grade/recipe-version
references, and no ProductionPhase (no machine/process settings) behind
it, unlike a real production batch.

Samples taken from a customer trial (Samples & Conditioning page), and
quality test results / quality issues logged against it (Quality Test
Result / Quality Issue pages), all link back here by customer_trial_id -
mirroring how those same three tables link to a production run, just
through a different, mutually-exclusive foreign key (see
db.sample_source_fk_field()).
"""

import streamlit as st

from access_control import can_use_page
from auth import current_user, logout_button, require_login
from cascades import customer_trial_dependency_counts, delete_customer_trial_cascade
from db import CustomerTrial, FoamGrade, RecipeVersion, get_session, init_db
from helpers import (
    clickable_table,
    delete_with_confirm,
    page_setup,
    render_function_action_intro,
    view_only_notice,
)
from tenant_scope import apply_scope, company_picker, grade_ids_for_company, plant_ids_for_company

page_setup("Customer Trials")
init_db()
require_login()
logout_button()

st.title("Customer Trials")
render_function_action_intro(
    function_text=(
        "Tracks lab trials made for a specific customer in light of a sales opportunity - usually "
        "a small box trial, not a full production run. Independent of Production Run: its own "
        "record, its own samples and quality data, no machine/process settings behind it."
    ),
    action_text=(
        "Flag a new customer trial against a foam grade with the customer and objective. Once "
        "created, add its sample(s) on the Samples & Conditioning page and log quality test "
        "results / quality issues against it from the Quality Test Result and Quality Issue pages "
        "- all three pick this trial as their source. Close it out here once outcome, reviewed by, "
        "and date closed are filled in."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("customer_trials", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="customer_trials_company_filter"
)
active_company_id = company.id if company else None
plant_ids = plant_ids_for_company(session, active_company_id)
grade_ids = grade_ids_for_company(session, active_company_id)

grades = apply_scope(session.query(FoamGrade), FoamGrade.id, grade_ids).all()
if not grades:
    st.warning("Add a foam grade first (Product Family & Foam Grade page).")
    st.stop()

trials = (
    apply_scope(session.query(CustomerTrial), CustomerTrial.plant_id, plant_ids)
    .order_by(CustomerTrial.created_at.desc())
    .all()
)

with st.expander("Flag a new customer trial", expanded=False):
    if not page_usable:
        st.caption("View-only access - adding a customer trial is restricted for your role.")
    else:
        with st.form("add_customer_trial"):
            grade = st.selectbox(
                "Foam grade *", grades, format_func=lambda g: g.grade_name, key="ct_add_grade",
            )
            customer_name = st.text_input("Customer name *")
            sales_opportunity_reference = st.text_input("Sales opportunity reference")
            requested_by = st.text_input("Requested by")
            trial_objective = st.text_area("Trial objective (what the customer wants evaluated, and why)")
            responsible_person = st.text_input("Responsible person")
            trial_date = st.date_input("Trial date", value=None)
            batch_reference = st.text_input("Batch reference (this trial's own box/batch id)")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save customer trial")
            if submitted:
                if not customer_name.strip():
                    st.error("Customer name is required.")
                else:
                    # Recipe version auto-follows the grade's current active
                    # version, same reasoning as Production Run: which
                    # version is "current" isn't a decision made on this
                    # page, it's whatever Recipes has marked active.
                    versions_for_grade = (
                        session.query(RecipeVersion).filter(RecipeVersion.foam_grade_id == grade.id).all()
                    )
                    current_version = next(
                        (v for v in versions_for_grade if v.is_active),
                        versions_for_grade[-1] if versions_for_grade else None,
                    )
                    session.add(
                        CustomerTrial(
                            plant_id=grade.product_family.plant_id,
                            foam_grade_id=grade.id,
                            recipe_version_id=current_version.id if current_version else None,
                            customer_name=customer_name.strip(),
                            sales_opportunity_reference=sales_opportunity_reference,
                            requested_by=requested_by,
                            trial_objective=trial_objective,
                            responsible_person=responsible_person,
                            trial_date=trial_date,
                            batch_reference=batch_reference,
                            notes=notes,
                            status="Open",
                        )
                    )
                    session.commit()
                    st.success("Customer trial created.")
                    st.rerun()

st.divider()
st.subheader("Customer trials")

status_filter = st.multiselect(
    "Status filter", ["Open", "Pending Closure", "Closed"], default=["Open", "Pending Closure", "Closed"],
    key="ct_status_filter",
)
filtered_trials = [t for t in trials if t.status in status_filter]

if not filtered_trials:
    st.info("No customer trials match the current filter.")
else:
    trial_rows = [
        {
            "Trial": f"#{t.id}",
            "Status": t.status,
            "Grade": t.foam_grade.grade_name,
            "Customer": t.customer_name,
            "Trial date": t.trial_date,
            "Objective": t.trial_objective or "",
            "Responsible": t.responsible_person or "",
        }
        for t in filtered_trials
    ]
    st.caption("Click a row to edit (and optionally delete) that customer trial.")
    idx = clickable_table(trial_rows, key="customer_trials_table")
    if idx is not None and idx < len(filtered_trials):
        st.session_state["ct_selected_id"] = filtered_trials[idx].id
    else:
        st.session_state.pop("ct_selected_id", None)

    selected_id = st.session_state.get("ct_selected_id")
    selected = next((t for t in filtered_trials if t.id == selected_id), None) or (
        session.query(CustomerTrial).filter(CustomerTrial.id == selected_id).first() if selected_id else None
    )

    if selected:
        st.divider()
        st.subheader(f"Edit Customer Trial #{selected.id}")
        with st.form(f"edit_customer_trial_{selected.id}"):
            grade_idx = next((i for i, g in enumerate(grades) if g.id == selected.foam_grade_id), 0)
            e_grade = st.selectbox(
                "Foam grade *", grades, index=grade_idx, format_func=lambda g: g.grade_name,
                key=f"ct_edit_grade_{selected.id}",
            )
            e_customer_name = st.text_input("Customer name *", value=selected.customer_name or "", key=f"ct_edit_customer_{selected.id}")
            e_sales_ref = st.text_input(
                "Sales opportunity reference", value=selected.sales_opportunity_reference or "", key=f"ct_edit_salesref_{selected.id}"
            )
            e_requested_by = st.text_input("Requested by", value=selected.requested_by or "", key=f"ct_edit_reqby_{selected.id}")
            e_objective = st.text_area(
                "Trial objective", value=selected.trial_objective or "", key=f"ct_edit_objective_{selected.id}"
            )
            e_responsible = st.text_input(
                "Responsible person", value=selected.responsible_person or "", key=f"ct_edit_resp_{selected.id}"
            )
            e_trial_date = st.date_input("Trial date", value=selected.trial_date, key=f"ct_edit_date_{selected.id}")
            e_batch_ref = st.text_input("Batch reference", value=selected.batch_reference or "", key=f"ct_edit_batch_{selected.id}")
            e_status = st.selectbox(
                "Status", ["Open", "Pending Closure", "Closed"],
                index=["Open", "Pending Closure", "Closed"].index(selected.status) if selected.status in ["Open", "Pending Closure", "Closed"] else 0,
                key=f"ct_edit_status_{selected.id}",
            )
            st.markdown("**Closeout** (all required before status can be set to Closed)")
            e_outcome = st.text_area("Outcome", value=selected.outcome or "", key=f"ct_edit_outcome_{selected.id}")
            e_feedback = st.text_area(
                "Customer feedback", value=selected.customer_feedback or "", key=f"ct_edit_feedback_{selected.id}"
            )
            e_followup = st.text_area(
                "Follow-up action", value=selected.follow_up_action or "", key=f"ct_edit_followup_{selected.id}"
            )
            e_reviewed_by = st.text_input("Reviewed by", value=selected.reviewed_by or "", key=f"ct_edit_reviewedby_{selected.id}")
            e_date_closed = st.date_input("Date closed", value=selected.date_closed, key=f"ct_edit_dateclosed_{selected.id}")
            e_notes = st.text_area("Notes", value=selected.notes or "", key=f"ct_edit_notes_{selected.id}")
            if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
                if not e_customer_name.strip():
                    st.error("Customer name is required.")
                else:
                    selected.foam_grade_id = e_grade.id
                    selected.customer_name = e_customer_name.strip()
                    selected.sales_opportunity_reference = e_sales_ref
                    selected.requested_by = e_requested_by
                    selected.trial_objective = e_objective
                    selected.responsible_person = e_responsible
                    selected.trial_date = e_trial_date
                    selected.batch_reference = e_batch_ref
                    selected.outcome = e_outcome
                    selected.customer_feedback = e_feedback
                    selected.follow_up_action = e_followup
                    selected.reviewed_by = e_reviewed_by
                    selected.date_closed = e_date_closed
                    selected.notes = e_notes
                    if e_status == "Closed" and not selected.can_close():
                        missing = selected.missing_closeout_fields()
                        st.error(f"Can't close - missing: {', '.join(missing)}.")
                        session.rollback()
                    else:
                        selected.status = e_status
                        session.commit()
                        st.success("Customer trial updated.")
                        st.rerun()

        if selected.status != "Closed":
            missing = selected.missing_closeout_fields()
            if missing:
                st.caption(f"⏳ Missing before closure: {', '.join(missing)}")

        counts = customer_trial_dependency_counts(session, selected.id)
        linked_bits = [f"{v} {k}" for k, v in counts.items() if v]
        warning = (
            "This will permanently delete: " + ", ".join(linked_bits) + "."
            if linked_bits else "No related records — deleting it is safe."
        )

        def _do_delete(_session=session, _id=selected.id):
            delete_customer_trial_cascade(_session, _id)
            _session.commit()
            st.session_state.pop("ct_selected_id", None)

        if page_usable:
            delete_with_confirm(
                f"Customer Trial #{selected.id}", _do_delete, key_prefix=f"ct_{selected.id}", extra_warning=warning,
            )
        else:
            st.caption("View-only access - deleting is restricted for your role.")

        if st.button("Clear selection", key="clear_ct_selection"):
            st.session_state.pop("ct_selected_id", None)
            st.rerun()
