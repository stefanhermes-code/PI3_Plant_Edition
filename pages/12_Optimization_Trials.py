"""Screen: Optimization Trials & Samples

Restructured 2026-08-04 as part of the Samples & Trials nav-section
rework: mirrors Customer Trials & Samples exactly, for OptimizationTrial
instead of CustomerTrial. This page now owns the optimization trial
itself AND every sample taken against it. Three top-level tabs: Create
Trial (the create form, plus a "Manage samples" workspace for adding/
editing/importing samples against any existing trial), Edit/Delete
Trial, and CSV/Excel import for bulk-creating trials.

An optimization trial is initiated by a Performance Improvement
initiative related to (but independent of) the Industrial Intelligence
section's own analysis - usually the same kind of small-box lab trial as
a customer trial, just triggered internally rather than by a customer
request. Like Customer Trial, this is NOT a production run with a flag on
it: its own table, its own plant/foam grade/recipe-version references, no
ProductionPhase behind it.

The sample CSV/Excel import nested under Create Trial deliberately stays
multi-source (accepting production_run_id/customer_trial_id/
optimization_trial_id rows, not just this trial's own samples), per
explicit user direction - it's the same combined importer the old
Samples & Conditioning page had, just relocated rather than narrowed.

Quality test results / quality issues logged against a trial (Quality
Test Result / Quality Issue pages) still link back here by
optimization_trial_id - mirroring Customer Trial's own wiring, just
through a different, mutually-exclusive foreign key (see
db.sample_source_fk_field()).
"""

import pandas as pd
import streamlit as st

from access_control import can_use_page
from auth import current_user, logout_button, require_login
from cascades import delete_optimization_trial_cascade, optimization_trial_dependency_counts
from db import (
    SAMPLE_SOURCE_TYPES,
    ZONE_LABELS,
    FoamGrade,
    OptimizationTrial,
    PhysicalPropertyResult,
    RecipeVersion,
    Sample,
    get_session,
    init_db,
    sample_source_fk_field,
)
from helpers import (
    clickable_table,
    combine_date_time,
    csv_excel_uploader,
    dedupe_import_rows,
    delete_with_confirm,
    page_setup,
    render_data_table,
    render_function_action_intro,
    set_pending_banner,
    show_pending_banner,
    view_only_notice,
)
from tenant_scope import (
    apply_scope,
    company_picker,
    customer_trial_ids_for_company,
    grade_ids_for_company,
    optimization_trial_ids_for_company,
    plant_ids_for_company,
    run_ids_for_company,
)

TRIAL_REQUIRED_COLUMNS = ["foam_grade_id"]
TRIAL_OPTIONAL_COLUMNS = [
    "improvement_initiative_reference", "hypothesis", "what_changed",
    "responsible_person", "trial_date", "batch_reference", "notes",
]
SAMPLE_REQUIRED_COLUMNS = ["source_type", "source_id", "zone_label"]
SAMPLE_OPTIONAL_COLUMNS = ["sample_ts", "notes"]
STATUS_OPTIONS = ["Open", "Pending Closure", "Closed"]

page_setup("Optimization Trials & Samples")
init_db()
require_login()
logout_button()

st.title("Optimization Trials & Samples")
render_function_action_intro(
    function_text=(
        "Tracks lab trials stemming from a Performance Improvement initiative - related to, but "
        "independent of, the Industrial Intelligence section's own analysis - along with every "
        "sample taken against that trial. Independent of Production Run: its own record, its own "
        "samples and quality data, no machine/process settings behind it."
    ),
    action_steps=[
        "Create Trial: flag a new optimization trial, then use Manage samples below to add/edit/"
        "import samples for it (or for any other open trial).",
        "Edit/Delete Trial: update or close out an existing trial, or delete it.",
        "CSV / Excel import: bulk-create trials from a spreadsheet.",
    ],
    action_note=(
        "Log quality test results / quality issues against a trial from the Quality Test Result and "
        "Quality Issue pages - both pick this trial as their source."
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

# Which source_id values are legal for the multi-source sample importer
# below, per source_type - None means unfiltered (platform owner viewing
# "All companies"). Without this, a crafted CSV row could attach a sample
# to another company's production run/trial by guessing its id (found in
# the 2026-08-04 Duroflex-pilot tenant-scoping audit; page 9's
# single-source importer already had the equivalent check).
_valid_run_ids = run_ids_for_company(session, active_company_id)
_valid_ct_ids = customer_trial_ids_for_company(session, active_company_id)
_valid_ot_ids = optimization_trial_ids_for_company(session, active_company_id)
valid_source_ids = {
    "Production Run": None if _valid_run_ids is None else set(_valid_run_ids),
    "Customer Trial": None if _valid_ct_ids is None else set(_valid_ct_ids),
    "Optimization Trial": None if _valid_ot_ids is None else set(_valid_ot_ids),
}


def _resolve_recipe_version(grade_id):
    versions_for_grade = session.query(RecipeVersion).filter(RecipeVersion.foam_grade_id == grade_id).all()
    current_version = next(
        (v for v in versions_for_grade if v.is_active), versions_for_grade[-1] if versions_for_grade else None,
    )
    return current_version.id if current_version else None


tab_create, tab_edit_delete, tab_import = st.tabs(["Create Trial", "Edit/Delete Trial", "CSV / Excel import"])

with tab_create:
    st.subheader("Flag a new optimization trial")
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
                session.add(
                    OptimizationTrial(
                        plant_id=grade.product_family.plant_id,
                        foam_grade_id=grade.id,
                        recipe_version_id=_resolve_recipe_version(grade.id),
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
    st.subheader("Manage samples")
    if not trials:
        st.info("Create a trial above first, then come back here to add its samples.")
    else:
        managed_trial = st.selectbox(
            "Trial *", trials,
            format_func=lambda t: f"#{t.id} — {t.foam_grade.grade_name}"
            + (f" ({t.improvement_initiative_reference})" if t.improvement_initiative_reference else "")
            + f" — {t.status}",
            key="ot_manage_trial",
        )
        sub_create, sub_edit_delete, sub_import = st.tabs(["Create Sample", "Edit/Delete Sample", "CSV / Excel import"])

        with sub_create:
            if not page_usable:
                st.caption("View-only access - adding a sample is restricted for your role.")
            else:
                with st.form(f"add_sample_{managed_trial.id}"):
                    zone_label = st.selectbox("Zone *", ZONE_LABELS, key=f"ot_sample_zone_{managed_trial.id}")
                    sample_ts = combine_date_time("Sample creation time", f"ot_sample_ts_{managed_trial.id}")
                    sample_notes = st.text_area("Notes", key=f"ot_sample_notes_{managed_trial.id}")
                    if st.form_submit_button("Save sample"):
                        session.add(
                            Sample(
                                optimization_trial_id=managed_trial.id, zone_label=zone_label,
                                sample_ts=sample_ts, notes=sample_notes,
                            )
                        )
                        session.commit()
                        st.success("Sample saved.")
                        st.rerun()

        with sub_edit_delete:
            trial_samples = (
                session.query(Sample)
                .filter(Sample.optimization_trial_id == managed_trial.id)
                .order_by(Sample.id.desc()).all()
            )
            if not trial_samples:
                st.info("No samples recorded yet for this trial.")
            else:
                sample_rows = [
                    {"Sample ID": s.id, "Zone": s.zone_label, "Sampled": s.sample_ts, "Notes": s.notes or ""}
                    for s in trial_samples
                ]
                st.caption("Click a row to edit (and optionally delete) that sample.")
                s_idx = clickable_table(sample_rows, key=f"ot_samples_table_{managed_trial.id}")
                sel_key = f"ot_sample_selected_id_{managed_trial.id}"
                if s_idx is not None:
                    st.session_state[sel_key] = trial_samples[s_idx].id
                else:
                    st.session_state.pop(sel_key, None)

                selected_sample_id = st.session_state.get(sel_key)
                selected_sample = next((s for s in trial_samples if s.id == selected_sample_id), None)

                if selected_sample:
                    st.markdown(f"**Edit sample #{selected_sample.id}**")
                    with st.form(f"edit_ot_sample_{selected_sample.id}"):
                        e_zone = st.selectbox(
                            "Zone *", ZONE_LABELS,
                            index=ZONE_LABELS.index(selected_sample.zone_label) if selected_sample.zone_label in ZONE_LABELS else 0,
                            key=f"edit_ot_sample_zone_{selected_sample.id}",
                        )
                        e_sample_ts = combine_date_time(
                            "Sample creation time", f"edit_ot_sample_ts_{selected_sample.id}",
                            default_date=selected_sample.sample_ts.date() if selected_sample.sample_ts else None,
                            default_time=selected_sample.sample_ts.time() if selected_sample.sample_ts else None,
                        )
                        e_notes = st.text_area(
                            "Notes", value=selected_sample.notes or "", key=f"edit_ot_sample_notes_{selected_sample.id}"
                        )
                        if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
                            selected_sample.zone_label = e_zone
                            selected_sample.sample_ts = e_sample_ts
                            selected_sample.notes = e_notes
                            session.commit()
                            st.success("Sample updated.")
                            st.rerun()

                    result_count = (
                        session.query(PhysicalPropertyResult)
                        .filter(PhysicalPropertyResult.sample_id == selected_sample.id).count()
                    )
                    s_warning = (
                        f"{result_count} quality test result(s) will be unlinked from this sample (kept, sample reference cleared)."
                        if result_count else "No related records — deleting it is safe."
                    )

                    def _do_delete_ot_sample(_session=session, _id=selected_sample.id, _key=sel_key):
                        _session.query(PhysicalPropertyResult).filter(PhysicalPropertyResult.sample_id == _id).update(
                            {"sample_id": None}, synchronize_session="fetch"
                        )
                        _session.query(Sample).filter(Sample.id == _id).delete(synchronize_session=False)
                        _session.commit()
                        st.session_state.pop(_key, None)

                    if page_usable:
                        delete_with_confirm(
                            f"sample #{selected_sample.id}", _do_delete_ot_sample,
                            key_prefix=f"ot_sample_{selected_sample.id}", extra_warning=s_warning,
                        )
                    else:
                        st.caption("View-only access - deleting is restricted for your role.")

        with sub_import:
            show_pending_banner("ot_sample_import_msg")
            st.caption(
                "Multi-source import - not scoped to the trial selected above. Required columns: "
                "source_type (Production Run / Customer Trial / Optimization Trial), source_id, "
                "zone_label. Optional columns: sample_ts, notes."
            )
            ot_sample_df, ot_sample_filename = csv_excel_uploader(
                SAMPLE_REQUIRED_COLUMNS, SAMPLE_OPTIONAL_COLUMNS, key=f"ot_sample_upload_{managed_trial.id}"
            )
            if ot_sample_df is not None:
                good_rows, bad_rows = [], []
                for _, row in ot_sample_df.iterrows():
                    source_type = str(row.get("source_type", "")).strip()
                    try:
                        source_id_val = int(row.get("source_id"))
                    except (TypeError, ValueError):
                        source_id_val = None
                    ids_for_type = valid_source_ids.get(source_type)
                    source_id_in_scope = (
                        source_id_val is not None
                        and (ids_for_type is None or source_id_val in ids_for_type)
                    )
                    if source_type in SAMPLE_SOURCE_TYPES and source_id_in_scope and str(row.get("zone_label", "")).strip():
                        good_rows.append(row)
                    else:
                        bad_rows.append(row)

                st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged as invalid: **{len(bad_rows)}**")
                if bad_rows:
                    st.warning(
                        "These rows have an invalid source_type (must be one of: "
                        + ", ".join(SAMPLE_SOURCE_TYPES) + "), a missing/non-numeric source_id, a source_id that "
                        "doesn't belong to " + (company.name if company else "the current company") + ", or a "
                        "missing zone_label."
                    )
                    render_data_table(pd.DataFrame(bad_rows), max_height="300px")

                if good_rows and st.button("Confirm import", key=f"confirm_ot_sample_import_{managed_trial.id}", disabled=not page_usable):
                    existing_keys = {
                        (s.production_run_id, s.customer_trial_id, s.optimization_trial_id, (s.zone_label or "").strip().lower())
                        for s in session.query(Sample).all()
                    }

                    def _ot_dedupe_key(row):
                        field = sample_source_fk_field(str(row["source_type"]).strip())
                        fk_vals = {"production_run_id": None, "customer_trial_id": None, "optimization_trial_id": None}
                        fk_vals[field] = int(row["source_id"])
                        return (
                            fk_vals["production_run_id"], fk_vals["customer_trial_id"], fk_vals["optimization_trial_id"],
                            str(row["zone_label"]).strip().lower(),
                        )

                    new_rows, dup_rows = dedupe_import_rows(good_rows, existing_keys, key_func=_ot_dedupe_key)
                    for row in new_rows:
                        field = sample_source_fk_field(str(row["source_type"]).strip())
                        kwargs = {field: int(row["source_id"])}
                        session.add(
                            Sample(
                                zone_label=str(row["zone_label"]).strip(),
                                sample_ts=pd.to_datetime(row.get("sample_ts"), errors="coerce"),
                                notes=str(row.get("notes", "") or ""),
                                **kwargs,
                            )
                        )
                    session.commit()
                    msg = f"Imported {len(new_rows)} sample(s) from {ot_sample_filename}."
                    if dup_rows:
                        msg += f" Skipped {len(dup_rows)} row(s) already recorded for their source + zone (likely a repeat click)."
                    set_pending_banner("ot_sample_import_msg", msg)
                    st.rerun()

with tab_import:
    show_pending_banner("ot_trial_import_msg")
    st.caption(
        "Required columns: foam_grade_id. Optional columns: improvement_initiative_reference, "
        "hypothesis, what_changed, responsible_person, trial_date, batch_reference, notes. "
        "foam_grade_id must be one of your foam grades; recipe version is auto-resolved from the "
        "grade's active version; status is always set to Open."
    )
    trial_df, trial_filename = csv_excel_uploader(TRIAL_REQUIRED_COLUMNS, TRIAL_OPTIONAL_COLUMNS, key="ot_trial_upload")
    if trial_df is not None:
        import_grade_ids = {g.id for g in grades}
        good_trial_rows, bad_trial_rows = [], []
        for _, row in trial_df.iterrows():
            try:
                grade_id_val = int(row.get("foam_grade_id"))
            except (TypeError, ValueError):
                grade_id_val = None
            if grade_id_val in import_grade_ids:
                good_trial_rows.append(row)
            else:
                bad_trial_rows.append(row)

        st.write(f"Rows ready to import: **{len(good_trial_rows)}** | Rows flagged as invalid: **{len(bad_trial_rows)}**")
        if bad_trial_rows:
            st.warning("These rows are missing foam_grade_id, or it isn't one of your foam grades.")
            render_data_table(pd.DataFrame(bad_trial_rows), max_height="300px")

        if good_trial_rows and st.button("Confirm import", key="confirm_ot_trial_import", disabled=not page_usable):
            existing_trial_keys = {
                (t.foam_grade_id, (t.improvement_initiative_reference or "").strip().lower(), t.trial_date)
                for t in trials
            }

            def _trial_dedupe_key(row):
                parsed_date = pd.to_datetime(row.get("trial_date"), errors="coerce")
                return (
                    int(row["foam_grade_id"]), str(row.get("improvement_initiative_reference", "") or "").strip().lower(),
                    parsed_date.date() if pd.notna(parsed_date) else None,
                )

            new_trial_rows, dup_trial_rows = dedupe_import_rows(good_trial_rows, existing_trial_keys, key_func=_trial_dedupe_key)
            grades_by_id = {g.id: g for g in grades}
            for row in new_trial_rows:
                grade_id_val = int(row["foam_grade_id"])
                grade_obj = grades_by_id[grade_id_val]
                trial_date_val = pd.to_datetime(row.get("trial_date"), errors="coerce")
                session.add(
                    OptimizationTrial(
                        plant_id=grade_obj.product_family.plant_id,
                        foam_grade_id=grade_id_val,
                        recipe_version_id=_resolve_recipe_version(grade_id_val),
                        improvement_initiative_reference=str(row.get("improvement_initiative_reference", "") or ""),
                        hypothesis=str(row.get("hypothesis", "") or ""),
                        what_changed=str(row.get("what_changed", "") or ""),
                        responsible_person=str(row.get("responsible_person", "") or ""),
                        trial_date=trial_date_val.date() if pd.notna(trial_date_val) else None,
                        batch_reference=str(row.get("batch_reference", "") or ""),
                        notes=str(row.get("notes", "") or ""),
                        status="Open",
                    )
                )
            session.commit()
            msg = f"Imported {len(new_trial_rows)} optimization trial(s) from {trial_filename}."
            if dup_trial_rows:
                msg += f" Skipped {len(dup_trial_rows)} row(s) already recorded for their grade + initiative ref + trial date (likely a repeat click)."
            set_pending_banner("ot_trial_import_msg", msg)
            st.rerun()

with tab_edit_delete:
    status_filter = st.multiselect(
        "Status filter", STATUS_OPTIONS, default=STATUS_OPTIONS, key="ot_status_filter",
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
                    "Status", STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(selected.status) if selected.status in STATUS_OPTIONS else 0,
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
