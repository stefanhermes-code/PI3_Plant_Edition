"""Screen: Optimization Trials

The second of the two independent lab-trial flows added 2026-08-03 (see
db.py's OptimizationTrial / SAMPLE_SOURCE_TYPES), alongside Customer
Trials. An optimization trial is initiated by a Performance Improvement
initiative related to (but independent of) the Industrial Intelligence
section's own analysis - usually the same kind of small-box lab trial as
a customer trial, just triggered internally rather than by a customer
request. Like Customer Trial, this is NOT a production run with a flag on
it: its own table, its own plant/foam grade/recipe-version references, no
ProductionPhase behind it.

Samples taken from an optimization trial (Samples & Conditioning page),
and quality test results / quality issues logged against it (Quality Test
Result / Quality Issue pages), all link back here by
optimization_trial_id - mirroring Customer Trial's own wiring, just
through a different, mutually-exclusive foreign key (see
db.sample_source_fk_field()).
"""

import streamlit as st

from access_control import can_use_page
from auth import current_user, logout_button, require_login
from cascades import delete_optimization_trial_cascade, optimization_trial_dependency_counts
from db import FoamGrade, OptimizationTrial, RecipeVersion, get_session, init_db
from helpers import (
    clickable_table,
    delete_with_confirm,
    page_setup,
    render_function_action_intro,
    view_only_notice,
)
from tenant_scope import apply_scope, company_picker, grade_ids_for_company, plant_ids_for_company

page_setup("Optimization Trials")
init_db()
require_login()
logout_button()

st.title("Optimization Trials")
render_function_action_intro(
    function_text=(
        "Tracks lab trials stemming from a Performance Improvement initiative - related to, but "
        "independent of, the Industrial Intelligence section's own analysis. Independent of "
        "Production Run: its own record, its own samples and quality data, no machine/process "
        "settings behind it."
    ),
    action_text=(
        "Flag a new optimization trial against a foam grade with the hypothesis and what changed "
        "vs. baseline. Once created, add its sample(s) on the Samples & Conditioning page and log "
        "quality test results / quality issues against it from the Quality Test Result and Quality "
        "Issue pages - all three pick this trial as their source. Close it out here once "
        "conclusion, reuse recommendation, reviewed by, approved by, and date closed are filled in."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("optimization_trials", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="optimization_trials_company_filter"
)
active_company_id = company.id if company else None
plant_ids = plant_ids_for_company(session, active_company_id)
grade_ids = grade_ids_for_company(session, active_company_id)

grades = apply_scope(session.query(FoamGrade), FoamGrade.id, grade_ids).all()
if not grades:
    st.warning("Add a foam grade first (Product Family & Foam Grade page).")
    st.stop()

trials = (
    apply_scope(session.query(OptimizationTrial), OptimizationTrial.plant_id, plant_ids)
    .order_by(OptimizationTrial.created_at.desc())
    .all()
)

with st.expander("Flag a new optimization trial", expanded=False):
    if not page_usable:
        st.caption("View-only access - adding an optimization trial is restricted for your role.")
    else:
        with st.form("add_optimization_trial"):
            grade = st.selectbox(
                "Foam grade *", grades, format_func=lambda g: g.grade_name, key="ot_add_grade",
            )
            improvement_initiative_reference = st.text_input("Improvement initiative reference")
            hypothesis = st.text_area("Hypothesis")
            what_changed = st.text_area("What changed vs. baseline")
            responsible_person = st.text_input("Responsible person")
            trial_date = st.date_input("Trial date", value=None, key="ot_add_date")
            batch_reference = st.text_input("Batch reference (this trial's own box/batch id)")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save optimization trial")
            if submitted:
                # Recipe version auto-follows the grade's current active
                # version, same reasoning as Customer Trial/Production Run.
                versions_for_grade = (
                    session.query(RecipeVersion).filter(RecipeVersion.foam_grade_id == grade.id).all()
                )
                current_version = next(
                    (v for v in versions_for_grade if v.is_active),
                    versions_for_grade[-1] if versions_for_grade else None,
                )
                session.add(
                    OptimizationTrial(
                        plant_id=grade.product_family.plant_id,
                        foam_grade_id=grade.id,
                        recipe_version_id=current_version.id if current_version else None,
                        improvement_initiative_reference=improvement_initiative_reference,
                        hypothesis=hypothesis,
                        what_changed=what_changed,
                        responsible_person=responsible_person,
                        trial_date=trial_date,
                        batch_reference=batch_reference,
                        notes=notes,
                        status="Open",
                    )
                )
                session.commit()
                st.success("Optimization trial created.")
                st.rerun()

st.divider()
st.subheader("Optimization trials")

status_filter = st.multiselect(
    "Status filter", ["Open", "Pending Closure", "Closed"], default=["Open", "Pending Closure", "Closed"],
    key="ot_status_filter",
)
filtered_trials = [t for t in trials if t.status in status_filter]

if not filtered_trials:
    st.info("No optimization trials match the current filter.")
else:
    trial_rows = [
        {
            "Trial": f"#{t.id}",
            "Status": t.status,
            "Grade": t.foam_grade.grade_name,
            "Initiative ref": t.improvement_initiative_reference or "",
            "Trial date": t.trial_date,
            "What changed": t.what_changed or "",
            "Responsible": t.responsible_person or "",
        }
        for t in filtered_trials
    ]
    st.caption("Click a row to edit (and optionally delete) that optimization trial.")
    idx = clickable_table(trial_rows, key="optimization_trials_table")
    if idx is not None and idx < len(filtered_trials):
        st.session_state["ot_selected_id"] = filtered_trials[idx].id
    else:
        st.session_state.pop("ot_selected_id", None)

    selected_id = st.session_state.get("ot_selected_id")
    selected = next((t for t in filtered_trials if t.id == selected_id), None) or (
        session.query(OptimizationTrial).filter(OptimizationTrial.id == selected_id).first() if selected_id else None
    )

    if selected:
        st.divider()
        st.subheader(f"Edit Optimization Trial #{selected.id}")
        with st.form(f"edit_optimization_trial_{selected.id}"):
            grade_idx = next((i for i, g in enumerate(grades) if g.id == selected.foam_grade_id), 0)
            e_grade = st.selectbox(
                "Foam grade *", grades, index=grade_idx, format_func=lambda g: g.grade_name,
                key=f"ot_edit_grade_{selected.id}",
            )
            e_initiative_ref = st.text_input(
                "Improvement initiative reference", value=selected.improvement_initiative_reference or "",
                key=f"ot_edit_initref_{selected.id}",
            )
            e_hypothesis = st.text_area("Hypothesis", value=selected.hypothesis or "", key=f"ot_edit_hyp_{selected.id}")
            e_what_changed = st.text_area(
                "What changed vs. baseline", value=selected.what_changed or "", key=f"ot_edit_changed_{selected.id}"
            )
            e_responsible = st.text_input(
                "Responsible person", value=selected.responsible_person or "", key=f"ot_edit_resp_{selected.id}"
            )
            e_trial_date = st.date_input("Trial date", value=selected.trial_date, key=f"ot_edit_date_{selected.id}")
            e_batch_ref = st.text_input("Batch reference", value=selected.batch_reference or "", key=f"ot_edit_batch_{selected.id}")
            e_status = st.selectbox(
                "Status", ["Open", "Pending Closure", "Closed"],
                index=["Open", "Pending Closure", "Closed"].index(selected.status) if selected.status in ["Open", "Pending Closure", "Closed"] else 0,
                key=f"ot_edit_status_{selected.id}",
            )
            st.markdown("**Closeout** (all required before status can be set to Closed)")
            e_result = st.text_area(
                "Result against target", value=selected.result_against_target or "", key=f"ot_edit_result_{selected.id}"
            )
            e_conclusion = st.text_area("Conclusion", value=selected.conclusion or "", key=f"ot_edit_conclusion_{selected.id}")
            e_reuse = st.text_area(
                "Reuse recommendation", value=selected.reuse_recommendation or "", key=f"ot_edit_reuse_{selected.id}"
            )
            e_reviewed_by = st.text_input("Reviewed by", value=selected.reviewed_by or "", key=f"ot_edit_reviewedby_{selected.id}")
            e_approved_by = st.text_input("Approved by", value=selected.approved_by or "", key=f"ot_edit_approvedby_{selected.id}")
            e_date_closed = st.date_input("Date closed", value=selected.date_closed, key=f"ot_edit_dateclosed_{selected.id}")
            e_notes = st.text_area("Notes", value=selected.notes or "", key=f"ot_edit_notes_{selected.id}")
            if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
                selected.foam_grade_id = e_grade.id
                selected.improvement_initiative_reference = e_initiative_ref
                selected.hypothesis = e_hypothesis
                selected.what_changed = e_what_changed
                selected.responsible_person = e_responsible
                selected.trial_date = e_trial_date
                selected.batch_reference = e_batch_ref
                selected.result_against_target = e_result
                selected.conclusion = e_conclusion
                selected.reuse_recommendation = e_reuse
                selected.reviewed_by = e_reviewed_by
                selected.approved_by = e_approved_by
                selected.date_closed = e_date_closed
                selected.notes = e_notes
                if e_status == "Closed" and not selected.can_close():
                    missing = selected.missing_closeout_fields()
                    st.error(f"Can't close - missing: {', '.join(missing)}.")
                    session.rollback()
                else:
                    selected.status = e_status
                    session.commit()
                    st.success("Optimization trial updated.")
                    st.rerun()

        if selected.status != "Closed":
            missing = selected.missing_closeout_fields()
            if missing:
                st.caption(f"⏳ Missing before closure: {', '.join(missing)}")

        counts = optimization_trial_dependency_counts(session, selected.id)
        linked_bits = [f"{v} {k}" for k, v in counts.items() if v]
        warning = (
            "This will permanently delete: " + ", ".join(linked_bits) + "."
            if linked_bits else "No related records — deleting it is safe."
        )

        def _do_delete(_session=session, _id=selected.id):
            delete_optimization_trial_cascade(_session, _id)
            _session.commit()
            st.session_state.pop("ot_selected_id", None)

        if page_usable:
            delete_with_confirm(
                f"Optimization Trial #{selected.id}", _do_delete, key_prefix=f"ot_{selected.id}", extra_warning=warning,
            )
        else:
            st.caption("View-only access - deleting is restricted for your role.")

        if st.button("Clear selection", key="clear_ot_selection"):
            st.session_state.pop("ot_selected_id", None)
            st.rerun()
