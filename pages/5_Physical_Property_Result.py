"""Screen 6: Quality Test Result

Sample and conditioning capture moved to its own page (Samples &
Conditioning, in Trials & Samples) on 2026-08-02 - this page had been
doing three jobs (samples, conditioning, and the result itself), which
crowded a single screen with tasks a reviewer does at different points in
the workflow. A lab result is still only comparable if it is tied to where
in the block the sample came from, its cure age, and its conditioning
history - that context now lives on the other page; a result here links
back to a sample by id, same as before.

Keyed primarily to the production run (every batch gets quality results,
trial or not). Linking to a trial is optional and only relevant when the
result is part of a formal experiment's evidence trail.

Redesigned 2026-08-02 (results browsing/edit make-over): the Add form now
sits behind a collapsed expander, same as the Quality Issue page, so
landing on this page shows the browsable table by default rather than an
always-open form. The old "Results by production run" section - one
bordered container with its own mini-table PER production run, stacked one
after another - is replaced by a single flat, filterable table across
every result (scoped to the active company), with one click-to-select/
edit/delete flow, matching the Quality Issue page's established pattern.
At real data volumes (100+ runs x several properties each) the per-run
version amounted to little more than "every result, one after another" -
a page of many small tables rather than one properly browsable list.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from access_control import can_use_page
from auth import current_user, logout_button, require_login
from db import (
    PhysicalPropertyDefinition,
    PhysicalPropertyMethod,
    PhysicalPropertyResult,
    PhysicalPropertyUOM,
    ProductionRun,
    Sample,
    TrialRecord,
    get_session,
    init_db,
)
from helpers import (
    clickable_table,
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
from quality_standards import compute_pass_fail, tolerance_label
from tenant_scope import apply_scope, company_picker, run_ids_for_company

RESULT_REQUIRED_COLUMNS = ["production_run_id", "property_name", "test_method", "unit", "actual_value"]
RESULT_OPTIONAL_COLUMNS = [
    "target_value", "sample_id", "trial_record_id", "method_revision",
    "replicate_no", "tested_at", "notes",
]

page_setup("Quality Test Result")
init_db()
require_login()
logout_button()

st.title("Quality Test Result")
render_function_action_intro(
    function_text=(
        "Records the lab results that prove out (or flag) a batch against the property/method/"
        "unit master list - density, 40% IFD/hardness, tensile strength, elongation, compression "
        "set, resilience, and so on - each compared to a target value and marked pass or fail. "
        "Link a result to a sample (recorded on the Samples & Conditioning page) for full "
        "traceability back to where in the block it was cut, its cure age, and what conditioning "
        "it went through before testing."
    ),
    action_text=(
        "Add the sample(s) for this run first, on the Samples & Conditioning page (in Trials & "
        "Samples), if you want results traceable back to block location and cure age. Then pick "
        "the property, test method, and unit from the master list here and link the result back "
        "to its sample if one applies. Use the CSV/Excel import tab to bulk-load a batch of "
        "results at once instead of entering them one by one."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("quality_test_result", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="qtr_company_filter"
)
active_company_id = company.id if company else None
run_ids = run_ids_for_company(session, active_company_id)

runs = (
    apply_scope(session.query(ProductionRun), ProductionRun.id, run_ids)
    .order_by(ProductionRun.created_at.desc())
    .all()
)
if not runs:
    st.warning("Create a production run first (Production Run page).")
    st.stop()

# ---------------------------------------------------------------------------
# Physical property results
# ---------------------------------------------------------------------------
st.divider()

property_defs = (
    session.query(PhysicalPropertyDefinition)
    .order_by(PhysicalPropertyDefinition.is_common.desc(), PhysicalPropertyDefinition.sort_order)
    .all()
)
if not property_defs:
    st.warning(
        "The physical property master list has not been loaded yet. Run the migration that seeds "
        "physical_property_definitions/methods/uoms before recording results."
    )

tab_result_manual, tab_result_import = st.tabs(["Add quality test result", "CSV / Excel import"])

with tab_result_manual:
    with st.expander("Add quality test result", expanded=False):
        if not page_usable:
            st.caption("View-only access - adding a quality test result is restricted for your role.")
        else:
            run = st.selectbox(
                "Production run *",
                runs,
                format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
                key="result_run_select",
            )
            trials_for_run = (
                session.query(TrialRecord).filter(TrialRecord.production_run_id == run.id).all() if run else []
            )
            trial = st.selectbox(
                "Link to trial (optional — only if this result is part of a formal experiment)",
                [None] + trials_for_run,
                format_func=lambda t: "— not linked to a trial —" if t is None else f"Trial #{t.id} ({t.status})",
                key="result_trial_select",
            )
            samples_for_run = (
                session.query(Sample).filter(Sample.production_run_id == run.id).all() if run else []
            )
            sample = st.selectbox(
                "Sample (optional, but recommended for comparability)",
                [None] + samples_for_run,
                format_func=lambda s: "— not linked to a sample —" if s is None else f"Sample #{s.id} — {s.zone_label}",
                key="result_sample_select",
            )
            property_def = st.selectbox(
                "Property * (⭐ = most commonly tested; full list searchable below)",
                property_defs,
                format_func=lambda p: f"⭐ {p.name}" if p.is_common else p.name,
                key="result_property_select",
            )
            if property_def:
                st.caption(f"{property_def.what_it_measures} — category: {property_def.category}")

            methods_for_property = (
                session.query(PhysicalPropertyMethod)
                .filter(PhysicalPropertyMethod.property_definition_id == property_def.id)
                .order_by(PhysicalPropertyMethod.sort_order)
                .all()
                if property_def
                else []
            )
            uoms_for_property = (
                session.query(PhysicalPropertyUOM)
                .filter(PhysicalPropertyUOM.property_definition_id == property_def.id)
                .order_by(PhysicalPropertyUOM.sort_order)
                .all()
                if property_def
                else []
            )

            with st.form("add_property_result"):
                c1, c2 = st.columns(2)
                method_choice = c1.selectbox(
                    "Measuring method *",
                    methods_for_property,
                    format_func=lambda m: m.method_code,
                )
                method_other = c1.text_input("Or type a method not listed above")
                uom_choice = c2.selectbox(
                    "Unit of measure *",
                    uoms_for_property,
                    format_func=lambda u: u.unit_label,
                )
                uom_other = c2.text_input("Or type a unit not listed above")

                c3, c4, c5 = st.columns(3)
                target_value = c3.number_input("Target value", step=0.1)
                actual_value = c4.number_input("Actual value", step=0.1)
                method_revision = c5.text_input("Method edition / revision (e.g. 2017)")
                if property_def:
                    st.caption(f"Industry accepted tolerance for {property_def.name}: {tolerance_label(property_def.name)}")
                replicate_no = st.number_input("Replicate no.", min_value=1, step=1, value=1)
                tested_at = st.date_input("Tested on", value=dt.date.today())
                notes = st.text_area("Notes (e.g. specimen geometry, orientation, deflection, temperature)")
                submitted = st.form_submit_button("Save result")
                if submitted:
                    final_method = method_other.strip() or (method_choice.method_code if method_choice else "")
                    final_unit = uom_other.strip() or (uom_choice.unit_label if uom_choice else "")
                    if not property_def:
                        st.error("Select a property.")
                    elif not final_method:
                        st.error("A measuring method is required — pick one or type a custom one.")
                    else:
                        pass_fail = compute_pass_fail(property_def.name, target_value, actual_value)
                        session.add(
                            PhysicalPropertyResult(
                                production_run_id=run.id,
                                trial_record_id=trial.id if trial else None,
                                sample_id=sample.id if sample else None,
                                property_definition_id=property_def.id,
                                property_method_id=method_choice.id if (method_choice and not method_other.strip()) else None,
                                property_name=property_def.name,
                                target_value=target_value or None,
                                actual_value=actual_value or None,
                                unit=final_unit,
                                pass_fail=pass_fail,
                                test_method=final_method,
                                method_revision=method_revision,
                                replicate_no=int(replicate_no),
                                tested_at=tested_at,
                                notes=notes,
                            )
                        )
                        session.commit()
                        st.success("Quality test result saved.")
                        st.rerun()

with tab_result_import:
    show_pending_banner("result_import_msg")
    st.caption(
        "property_name must match a name in the physical property master list (case-insensitive). "
        "test_method and unit are stored as typed — they don't need to match an existing method/UOM."
    )
    result_df, result_filename = csv_excel_uploader(
        RESULT_REQUIRED_COLUMNS, RESULT_OPTIONAL_COLUMNS, key="result_upload"
    )
    if result_df is not None:
        import_run_ids = {r.id for r in runs}
        defs_by_name = {p.name.strip().lower(): p for p in property_defs}
        # Scoped to this company's runs - otherwise a CSV row could attach a
        # new result to a different company's sample or trial (the run_id
        # check alone doesn't catch that, since sample_id/trial_record_id
        # are independent columns).
        samples_all = {
            s.id: s for s in apply_scope(session.query(Sample), Sample.production_run_id, run_ids).all()
        }
        trials_all = {
            t.id: t
            for t in apply_scope(session.query(TrialRecord), TrialRecord.production_run_id, run_ids).all()
        }

        good_rows, bad_rows = [], []
        for _, row in result_df.iterrows():
            try:
                prop_def = defs_by_name.get(str(row.get("property_name", "")).strip().lower())
                run_ok = row.get("production_run_id") in import_run_ids
                sample_val = row.get("sample_id")
                sample_ok = pd.isna(sample_val) or int(sample_val) in samples_all
                trial_val = row.get("trial_record_id")
                trial_ok = pd.isna(trial_val) or int(trial_val) in trials_all
                has_method_unit_value = (
                    str(row.get("test_method", "")).strip()
                    and str(row.get("unit", "")).strip()
                    and not pd.isna(row.get("actual_value"))
                )
                ok = bool(prop_def and run_ok and sample_ok and trial_ok and has_method_unit_value)
            except (TypeError, ValueError):
                ok = False
            if ok:
                good_rows.append(row)
            else:
                bad_rows.append(row)

        st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
        if bad_rows:
            st.warning(
                "Flagged rows have an unrecognized property_name, production_run_id, sample_id, or "
                "trial_record_id, or are missing test_method / unit / actual_value."
            )
            render_data_table(pd.DataFrame(bad_rows), max_height="300px")

        if good_rows and st.button("Confirm import", key="confirm_result_import", disabled=not page_usable):
            existing_keys = {
                (r.production_run_id, r.property_definition_id, r.sample_id, r.replicate_no)
                for r in session.query(PhysicalPropertyResult).all()
            }

            def _result_key(row):
                prop_def = defs_by_name[str(row["property_name"]).strip().lower()]
                sample_val = row.get("sample_id")
                replicate_val = row.get("replicate_no")
                return (
                    int(row["production_run_id"]),
                    prop_def.id,
                    int(sample_val) if not pd.isna(sample_val) else None,
                    int(replicate_val) if not pd.isna(replicate_val) else 1,
                )

            new_rows, dup_rows = dedupe_import_rows(good_rows, existing_keys, key_func=_result_key)

            for row in new_rows:
                prop_def = defs_by_name[str(row["property_name"]).strip().lower()]
                test_method = str(row["test_method"]).strip()
                method_match = next(
                    (
                        m
                        for m in session.query(PhysicalPropertyMethod)
                        .filter(PhysicalPropertyMethod.property_definition_id == prop_def.id)
                        .all()
                        if m.method_code.strip().lower() == test_method.lower()
                    ),
                    None,
                )
                target_val = row.get("target_value")
                actual_val = row.get("actual_value")
                pass_fail = (
                    compute_pass_fail(prop_def.name, target_val, actual_val)
                    if not pd.isna(target_val) and not pd.isna(actual_val)
                    else None
                )
                sample_val = row.get("sample_id")
                trial_val = row.get("trial_record_id")
                replicate_val = row.get("replicate_no")
                tested_val = pd.to_datetime(row.get("tested_at"), errors="coerce")
                session.add(
                    PhysicalPropertyResult(
                        production_run_id=int(row["production_run_id"]),
                        trial_record_id=int(trial_val) if not pd.isna(trial_val) else None,
                        sample_id=int(sample_val) if not pd.isna(sample_val) else None,
                        property_definition_id=prop_def.id,
                        property_method_id=method_match.id if method_match else None,
                        property_name=prop_def.name,
                        target_value=target_val if not pd.isna(target_val) else None,
                        actual_value=actual_val if not pd.isna(actual_val) else None,
                        unit=str(row["unit"]).strip(),
                        pass_fail=pass_fail,
                        test_method=test_method,
                        method_revision=str(row.get("method_revision", "") or ""),
                        replicate_no=int(replicate_val) if not pd.isna(replicate_val) else 1,
                        tested_at=tested_val.date() if not pd.isna(tested_val) else dt.date.today(),
                        notes=str(row.get("notes", "") or ""),
                    )
                )
            session.commit()
            msg = f"Imported {len(new_rows)} quality test result(s) from {result_filename}."
            if dup_rows:
                msg += f" Skipped {len(dup_rows)} row(s) already recorded for their run/property/sample (likely a repeat click)."
            set_pending_banner("result_import_msg", msg)
            st.rerun()

# ---------------------------------------------------------------------------
# Browse / edit / delete - one flat, filterable table across every result
# (scoped to the active company), not one mini-table per production run.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Quality test results")

pass_fail_options = ["Pass", "Fail", "Not computed"]
pass_fail_filter = st.multiselect("Pass/Fail filter", pass_fail_options, default=pass_fail_options)

property_name_options = sorted({p.name for p in property_defs}) if property_defs else []
property_filter = (
    st.multiselect("Property filter", property_name_options, default=property_name_options)
    if property_name_options
    else None
)

results_query = apply_scope(
    session.query(PhysicalPropertyResult), PhysicalPropertyResult.production_run_id, run_ids
)
if property_filter is not None:
    results_query = results_query.filter(PhysicalPropertyResult.property_name.in_(property_filter))
all_results = results_query.order_by(PhysicalPropertyResult.tested_at.desc()).all()

# Pass/Fail is recomputed live rather than trusted from the stored column -
# see the same note in analytics.property_results_dataframe - so this
# filter is applied here in Python against the live verdict, not as a SQL
# WHERE clause against the stored (possibly stale) column.
filtered_results = []
for r in all_results:
    live_pass_fail = compute_pass_fail(r.property_name, r.target_value, r.actual_value)
    if (live_pass_fail or "Not computed") in pass_fail_filter:
        filtered_results.append((r, live_pass_fail))

if not filtered_results:
    st.info("No quality test results match this filter.")
else:
    result_rows = [
        {
            "Run": f"#{r.production_run_id}",
            "Grade": r.production_run.foam_grade.grade_name,
            "Property": r.property_name,
            "Target": r.target_value,
            "Actual": r.actual_value,
            "Unit": r.unit,
            "Pass/Fail": live_pass_fail or "—",
            "Sample": f"#{r.sample_id} ({r.sample.zone_label})" if r.sample else "—",
            "Trial": f"#{r.trial_record_id}" if r.trial_record_id else "—",
            "Method": r.test_method,
            "Rev.": r.method_revision,
            "Replicate": r.replicate_no,
            "Tested": r.tested_at,
            "Notes": r.notes,
        }
        for r, live_pass_fail in filtered_results
    ]
    st.caption(f"{len(filtered_results)} result(s). Click a row to edit (and optionally delete) that result.")
    idx = clickable_table(result_rows, key="results_table")
    if idx is not None and idx < len(filtered_results):
        st.session_state["result_selected_id"] = filtered_results[idx][0].id
    else:
        st.session_state.pop("result_selected_id", None)

selected_result_id = st.session_state.get("result_selected_id")
selected_result = (
    session.query(PhysicalPropertyResult).filter(PhysicalPropertyResult.id == selected_result_id).first()
    if selected_result_id else None
)

if selected_result:
    st.divider()
    st.subheader(f"Edit quality test result #{selected_result.id}")
    # Same controlled method/UOM pickers as the Add form above, scoped to
    # this result's own property - previously this edit form used a free
    # text_input for both fields, which lost the structured picker the Add
    # form offers (see PI3_Gaps_and_Ambiguities.docx, findings 2.5/2.6).
    methods_for_edit = (
        session.query(PhysicalPropertyMethod)
        .filter(PhysicalPropertyMethod.property_definition_id == selected_result.property_definition_id)
        .order_by(PhysicalPropertyMethod.sort_order)
        .all()
        if selected_result.property_definition_id
        else []
    )
    uoms_for_edit = (
        session.query(PhysicalPropertyUOM)
        .filter(PhysicalPropertyUOM.property_definition_id == selected_result.property_definition_id)
        .order_by(PhysicalPropertyUOM.sort_order)
        .all()
        if selected_result.property_definition_id
        else []
    )
    method_match_idx = next(
        (i for i, m in enumerate(methods_for_edit) if m.method_code == selected_result.test_method), None
    )
    uom_match_idx = next(
        (i for i, u in enumerate(uoms_for_edit) if u.unit_label == selected_result.unit), None
    )
    with st.form(f"edit_result_{selected_result.id}"):
        samples_for_edit = (
            session.query(Sample).filter(Sample.production_run_id == selected_result.production_run_id).all()
        )
        sample_options = [None] + samples_for_edit
        sample_default = next((i for i, s in enumerate(sample_options) if s and s.id == selected_result.sample_id), 0)
        e_sample = st.selectbox(
            "Sample (optional)", sample_options, index=sample_default,
            format_func=lambda s: "— not linked to a sample —" if s is None else f"Sample #{s.id} — {s.zone_label}",
            key=f"edit_result_sample_{selected_result.id}",
        )
        trials_for_edit_result = (
            session.query(TrialRecord).filter(TrialRecord.production_run_id == selected_result.production_run_id).all()
        )
        trial_options_result = [None] + trials_for_edit_result
        trial_default_result = next(
            (i for i, t in enumerate(trial_options_result) if t and t.id == selected_result.trial_record_id), 0
        )
        e_trial = st.selectbox(
            "Link to trial (optional)", trial_options_result, index=trial_default_result,
            format_func=lambda t: "— not linked to a trial —" if t is None else f"Trial #{t.id} ({t.status})",
            key=f"edit_result_trial_{selected_result.id}",
        )
        ec1, ec2 = st.columns(2)
        e_target = ec1.number_input(
            "Target value", step=0.1, value=float(selected_result.target_value or 0.0), key=f"edit_result_target_{selected_result.id}"
        )
        e_actual = ec2.number_input(
            "Actual value", step=0.1, value=float(selected_result.actual_value or 0.0), key=f"edit_result_actual_{selected_result.id}"
        )
        st.caption(
            f"Industry accepted tolerance for {selected_result.property_name}: "
            f"{tolerance_label(selected_result.property_name)}"
        )

        emc1, emc2 = st.columns(2)
        if methods_for_edit:
            e_method_choice = emc1.selectbox(
                "Measuring method", methods_for_edit, index=method_match_idx or 0,
                format_func=lambda m: m.method_code, key=f"edit_result_method_select_{selected_result.id}",
            )
        else:
            e_method_choice = None
        e_method_other = emc1.text_input(
            "Or type a method not listed above",
            value=(selected_result.test_method or "") if method_match_idx is None else "",
            key=f"edit_result_method_other_{selected_result.id}",
        )
        if uoms_for_edit:
            e_uom_choice = emc2.selectbox(
                "Unit of measure", uoms_for_edit, index=uom_match_idx or 0,
                format_func=lambda u: u.unit_label, key=f"edit_result_uom_select_{selected_result.id}",
            )
        else:
            e_uom_choice = None
        e_uom_other = emc2.text_input(
            "Or type a unit not listed above",
            value=(selected_result.unit or "") if uom_match_idx is None else "",
            key=f"edit_result_uom_other_{selected_result.id}",
        )
        e_revision = st.text_input(
            "Method edition / revision", value=selected_result.method_revision or "", key=f"edit_result_rev_{selected_result.id}"
        )
        e_replicate = st.number_input(
            "Replicate no.", min_value=1, step=1, value=selected_result.replicate_no or 1, key=f"edit_result_replicate_{selected_result.id}"
        )
        e_tested_at = st.date_input(
            "Tested on", value=selected_result.tested_at or dt.date.today(), key=f"edit_result_tested_{selected_result.id}"
        )
        e_notes = st.text_area("Notes", value=selected_result.notes or "", key=f"edit_result_notes_{selected_result.id}")
        if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
            e_method = e_method_other.strip() or (e_method_choice.method_code if e_method_choice else "")
            e_unit = e_uom_other.strip() or (e_uom_choice.unit_label if e_uom_choice else "")
            if not e_method:
                st.error("A measuring method is required.")
            else:
                pass_fail = compute_pass_fail(selected_result.property_name, e_target, e_actual)
                selected_result.sample_id = e_sample.id if e_sample else None
                selected_result.trial_record_id = e_trial.id if e_trial else None
                selected_result.target_value = e_target or None
                selected_result.actual_value = e_actual or None
                selected_result.unit = e_unit
                selected_result.pass_fail = pass_fail
                selected_result.test_method = e_method
                selected_result.property_method_id = (
                    e_method_choice.id if (e_method_choice and not e_method_other.strip()) else None
                )
                selected_result.method_revision = e_revision
                selected_result.replicate_no = int(e_replicate)
                selected_result.tested_at = e_tested_at
                selected_result.notes = e_notes
                session.commit()
                st.success("Quality test result updated.")
                st.rerun()

    def _do_delete_result(_session=session, _id=selected_result.id):
        _session.query(PhysicalPropertyResult).filter(PhysicalPropertyResult.id == _id).delete(synchronize_session=False)
        _session.commit()
        st.session_state.pop("result_selected_id", None)

    if page_usable:
        delete_with_confirm(
            f"result #{selected_result.id}", _do_delete_result, key_prefix=f"result_{selected_result.id}",
            extra_warning="This is a leaf record — deleting it has no other effects.",
        )
    else:
        st.caption("View-only access - deleting is restricted for your role.")

    if st.button("Clear selection", key="clear_result_selection"):
        st.session_state.pop("result_selected_id", None)
        st.rerun()
