"""Screen: Samples & Conditioning

Split out of Quality Test Result on 2026-08-02 (was crowding that page with
three separate jobs - samples, conditioning, and the test result itself).
Where in the block a sample was taken and what conditioning it went
through before testing both matter for whether a lab result is comparable
to another one - that context lives here; the result itself is still
recorded on the Quality Test Result page, which links back to a sample
from here by id. (cure_age_hours was removed app-wide 2026-08-03, per
user feedback that it wasn't a real field in practice.)

Covers all three sample sources (production run, customer trial,
optimization trial) - lives in the "Trials & Samples" nav section
alongside Customer Trials and Optimization Trials per the platform
owner's own placement call.

Conditioning is a one-to-many HISTORY per sample, not a flat set of fields
on Sample - a sample can go through more than one distinct conditioning
stage before testing (e.g. an ambient hold on the plant floor, then a
standard 23C/50%RH chamber). Because of that it stays its own related
table (ConditioningSegment), but the UI nests it inside whichever sample is
currently selected below rather than showing it as a disconnected
top-level section with its own "which sample does this belong to"
dropdown - the relationship should be obvious from where the controls
appear, not just from a field label.
"""

import datetime as dt

import pandas as pd
import streamlit as st
from sqlalchemy import or_

from access_control import can_use_page
from auth import current_user, logout_button, require_login
from db import (
    CONDITIONING_TYPE_DEFAULTS,
    CONDITIONING_TYPES,
    SAMPLE_SOURCE_TYPES,
    ZONE_LABELS,
    ConditioningSegment,
    CustomerTrial,
    OptimizationTrial,
    PhysicalPropertyResult,
    ProductionPhase,
    ProductionRun,
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
    parse_dt,
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
    optimization_trial_ids_for_company,
    run_ids_for_company,
)

# A sample belongs to exactly one parent - a production run, a customer
# trial, or an optimization trial (see db.SAMPLE_SOURCE_TYPES). CSV rows
# carry all three FK columns as optional and must set exactly one.
SAMPLE_REQUIRED_COLUMNS = ["zone_label"]
SAMPLE_OPTIONAL_COLUMNS = [
    "production_run_id", "customer_trial_id", "optimization_trial_id", "sample_ts", "notes",
]


def _sample_source_desc(sample):
    """(source label, human-readable parent description) for a Sample,
    resolving whichever of the three FKs is actually set."""
    if sample.production_run_id is not None:
        run = sample.production_run
        desc = f"Run #{run.id} — {run.foam_grade.grade_name} · {run.run_date}" if run else f"Run #{sample.production_run_id}"
        return "Production Run", desc
    if sample.customer_trial_id is not None:
        t = sample.customer_trial
        desc = f"Trial #{t.id} — {t.customer_name}" if t else f"Trial #{sample.customer_trial_id}"
        return "Customer Trial", desc
    if sample.optimization_trial_id is not None:
        t = sample.optimization_trial
        ref = (t.improvement_initiative_reference or "(no reference)") if t else ""
        desc = f"Trial #{t.id} — {ref}" if t else f"Trial #{sample.optimization_trial_id}"
        return "Optimization Trial", desc
    return "—", "—"

page_setup("Samples & Conditioning")
init_db()
require_login()
logout_button()

st.title("Samples & Conditioning")
render_function_action_intro(
    function_text=(
        "Records where in the block a sample was taken and what conditioning it went through "
        "before testing - the context that makes a lab result comparable to another one. Covers "
        "all three sample sources: production-run QC samples, customer trial samples, and "
        "optimization trial samples. Quality Test Result links back to a sample from here by id; "
        "this page doesn't record test results itself."
    ),
    action_text=(
        "Pick which of the three you're sampling from (Production Run / Customer Trial / "
        "Optimization Trial), then add its sample(s) with block zone and extraction time. Select a "
        "sample below to add, edit, or remove its conditioning history - a sample can go through "
        "more than one conditioning stage before testing. Use the CSV/Excel import tab to bulk-load "
        "a batch of samples at once instead of entering them one by one."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("samples_conditioning", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="samples_company_filter"
)
active_company_id = company.id if company else None
run_ids = run_ids_for_company(session, active_company_id)
customer_trial_ids = customer_trial_ids_for_company(session, active_company_id)
optimization_trial_ids = optimization_trial_ids_for_company(session, active_company_id)

runs = (
    apply_scope(session.query(ProductionRun), ProductionRun.id, run_ids)
    .order_by(ProductionRun.created_at.desc())
    .all()
)
customer_trials = (
    apply_scope(session.query(CustomerTrial), CustomerTrial.id, customer_trial_ids)
    .order_by(CustomerTrial.created_at.desc())
    .all()
)
optimization_trials = (
    apply_scope(session.query(OptimizationTrial), OptimizationTrial.id, optimization_trial_ids)
    .order_by(OptimizationTrial.created_at.desc())
    .all()
)
if not runs and not customer_trials and not optimization_trials:
    st.warning(
        "Create a production run, customer trial, or optimization trial first "
        "(Production Run / Customer Trials / Optimization Trials pages)."
    )
    st.stop()

tab_sample_manual, tab_sample_import = st.tabs(["Add sample", "CSV / Excel import"])

with tab_sample_manual:
    if not page_usable:
        st.caption("View-only access - adding a sample is restricted for your role.")
    else:
        available_sources = [
            s for s in SAMPLE_SOURCE_TYPES
            if (s == "Production Run" and runs)
            or (s == "Customer Trial" and customer_trials)
            or (s == "Optimization Trial" and optimization_trials)
        ]
        # Source picker lives outside the form: which parent-picker shows
        # below it depends on this choice, and form-internal widgets don't
        # rerun until submit (same reason as the OEM/Model picker on the
        # Machine setup page).
        source_type = st.selectbox("Record against *", available_sources, key="sample_source_type")
        if source_type == "Production Run":
            sample_parent = st.selectbox(
                "Production run *", runs,
                format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
                key="sample_run_select",
            )
        elif source_type == "Customer Trial":
            sample_parent = st.selectbox(
                "Customer trial *", customer_trials,
                format_func=lambda t: f"Trial #{t.id} — {t.foam_grade.grade_name} · {t.customer_name} · {t.trial_date or '—'}",
                key="sample_ct_select",
            )
        else:
            sample_parent = st.selectbox(
                "Optimization trial *", optimization_trials,
                format_func=lambda t: (
                    f"Trial #{t.id} — {t.foam_grade.grade_name} · "
                    f"{t.improvement_initiative_reference or '(no reference)'} · {t.trial_date or '—'}"
                ),
                key="sample_ot_select",
            )

        with st.form("add_sample"):
            zone_label = st.selectbox("Zone *", ZONE_LABELS)
            sample_ts = combine_date_time("Sample extraction time", "sample_ts")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save sample")
            if submitted:
                time_error = None
                if source_type == "Production Run":
                    # Only production runs have a ProductionPhase - lab
                    # trials (Customer/Optimization) have no machine/process
                    # settings behind them, so there's nothing to check the
                    # extraction time against.
                    phases_for_run = (
                        session.query(ProductionPhase)
                        .filter(ProductionPhase.production_run_id == sample_parent.id)
                        .all()
                    )
                    earliest_start = min(
                        (p.phase_start for p in phases_for_run if p.phase_start), default=None
                    )
                    if earliest_start and sample_ts < earliest_start:
                        time_error = (
                            f"Sample extraction time ({sample_ts:%Y-%m-%d %H:%M}) is before this run started "
                            f"({earliest_start:%Y-%m-%d %H:%M}). Check the date/time."
                        )
                if time_error:
                    st.error(time_error)
                else:
                    new_sample = Sample(zone_label=zone_label, sample_ts=sample_ts, notes=notes)
                    setattr(new_sample, sample_source_fk_field(source_type), sample_parent.id)
                    session.add(new_sample)
                    session.commit()
                    st.success("Sample saved.")
                st.rerun()

with tab_sample_import:
    show_pending_banner("sample_import_msg")
    st.caption(
        "Each row needs exactly one of production_run_id / customer_trial_id / optimization_trial_id "
        "set, matching which of the three that sample belongs to, plus zone_label."
    )
    sample_df, sample_filename = csv_excel_uploader(
        SAMPLE_REQUIRED_COLUMNS, SAMPLE_OPTIONAL_COLUMNS, key="sample_upload"
    )
    if sample_df is not None:
        # Local names (not the outer, possibly-None *_ids) since runs/
        # customer_trials/optimization_trials are already concrete,
        # company-scoped lists at this point.
        sample_import_run_ids = {r.id for r in runs}
        sample_import_ct_ids = {t.id for t in customer_trials}
        sample_import_ot_ids = {t.id for t in optimization_trials}

        def _row_fk(row):
            """(fk_field, fk_value) if exactly one of the three FK columns
            is set to a value in-scope, else (None, None)."""
            candidates = []
            for field, id_set in (
                ("production_run_id", sample_import_run_ids),
                ("customer_trial_id", sample_import_ct_ids),
                ("optimization_trial_id", sample_import_ot_ids),
            ):
                val = row.get(field)
                if pd.notna(val) and str(val).strip():
                    candidates.append((field, val, id_set))
            if len(candidates) != 1:
                return None, None
            field, val, id_set = candidates[0]
            try:
                val_int = int(val)
            except (TypeError, ValueError):
                return None, None
            return (field, val_int) if val_int in id_set else (None, None)

        good_rows, bad_rows = [], []
        for _, row in sample_df.iterrows():
            fk_field, _fk_val = _row_fk(row)
            if fk_field and str(row.get("zone_label", "")).strip():
                good_rows.append(row)
            else:
                bad_rows.append(row)

        st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged as invalid: **{len(bad_rows)}**")
        if bad_rows:
            st.warning(
                "These rows are missing zone_label, or don't have exactly one in-scope "
                "production_run_id / customer_trial_id / optimization_trial_id set."
            )
            render_data_table(pd.DataFrame(bad_rows), max_height="300px")

        if good_rows and st.button("Confirm import", key="confirm_sample_import", disabled=not page_usable):
            existing_keys = set()
            for s in session.query(Sample).all():
                field, _ = _sample_source_desc(s)
                fk_col = sample_source_fk_field(field) if field in SAMPLE_SOURCE_TYPES else None
                if fk_col:
                    existing_keys.add((fk_col, getattr(s, fk_col), (s.zone_label or "").strip().lower()))

            def _dedupe_key(row):
                field, val = _row_fk(row)
                return (field, val, str(row["zone_label"]).strip().lower())

            new_rows, dup_rows = dedupe_import_rows(good_rows, existing_keys, key_func=_dedupe_key)
            for row in new_rows:
                fk_field, fk_val = _row_fk(row)
                new_sample = Sample(
                    zone_label=str(row["zone_label"]).strip(),
                    sample_ts=parse_dt(row.get("sample_ts")),
                    notes=str(row.get("notes", "") or ""),
                )
                setattr(new_sample, fk_field, fk_val)
                session.add(new_sample)
            session.commit()
            msg = f"Imported {len(new_rows)} sample(s) from {sample_filename}."
            if dup_rows:
                msg += f" Skipped {len(dup_rows)} row(s) already recorded for their source + zone (likely a repeat click)."
            set_pending_banner("sample_import_msg", msg)
            st.rerun()

st.divider()
st.subheader("🧊 Existing samples")

samples_query = session.query(Sample)
if active_company_id is not None:
    samples_query = samples_query.filter(
        or_(
            Sample.production_run_id.in_(run_ids or []),
            Sample.customer_trial_id.in_(customer_trial_ids or []),
            Sample.optimization_trial_id.in_(optimization_trial_ids or []),
        )
    )
samples = samples_query.order_by(Sample.id.desc()).all()
if not samples:
    st.info("No samples recorded yet.")
else:
    cond_counts = {}
    for s in samples:
        cond_counts[s.id] = session.query(ConditioningSegment).filter(ConditioningSegment.sample_id == s.id).count()

    sample_rows = []
    for s in samples:
        source_label, source_desc = _sample_source_desc(s)
        sample_rows.append(
            {
                "Sample ID": s.id,
                "Source": source_label,
                "Parent": source_desc,
                "Zone": s.zone_label,
                "Sampled": s.sample_ts,
                "Cond. segments": cond_counts[s.id],
            }
        )
    st.caption("Click a row to edit that sample and manage its conditioning history.")
    idx = clickable_table(sample_rows, key="samples_table")
    if idx is not None:
        st.session_state["sample_selected_id"] = samples[idx].id
    else:
        st.session_state.pop("sample_selected_id", None)

    selected_sample_id = st.session_state.get("sample_selected_id")
    selected_sample = next((s for s in samples if s.id == selected_sample_id), None)

    if selected_sample:
        st.divider()
        edit_source_label, edit_source_desc = _sample_source_desc(selected_sample)
        st.markdown(f"**Edit sample #{selected_sample.id}** — {edit_source_label}: {edit_source_desc}")
        st.caption("Which run/trial a sample belongs to can't be changed here - delete and re-add it under the correct source instead.")
        with st.form(f"edit_sample_{selected_sample.id}"):
            e_zone = st.selectbox(
                "Zone *", ZONE_LABELS,
                index=ZONE_LABELS.index(selected_sample.zone_label) if selected_sample.zone_label in ZONE_LABELS else 0,
                key=f"edit_sample_zone_{selected_sample.id}",
            )
            e_sample_ts = combine_date_time(
                "Sample extraction time", f"edit_sample_ts_{selected_sample.id}",
                default_date=selected_sample.sample_ts.date() if selected_sample.sample_ts else None,
                default_time=selected_sample.sample_ts.time() if selected_sample.sample_ts else None,
            )
            e_notes = st.text_area("Notes", value=selected_sample.notes or "", key=f"edit_sample_notes_{selected_sample.id}")
            if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
                time_error = None
                if selected_sample.production_run_id is not None:
                    phases_for_edit_run = (
                        session.query(ProductionPhase)
                        .filter(ProductionPhase.production_run_id == selected_sample.production_run_id)
                        .all()
                    )
                    earliest_start = min(
                        (p.phase_start for p in phases_for_edit_run if p.phase_start), default=None
                    )
                    if earliest_start and e_sample_ts < earliest_start:
                        time_error = (
                            f"Sample extraction time ({e_sample_ts:%Y-%m-%d %H:%M}) is before this run started "
                            f"({earliest_start:%Y-%m-%d %H:%M}). Check the date/time."
                        )
                if time_error:
                    st.error(time_error)
                else:
                    selected_sample.zone_label = e_zone
                    selected_sample.sample_ts = e_sample_ts
                    selected_sample.notes = e_notes
                    session.commit()
                    st.success("Sample updated.")
                    st.rerun()

        cond_count = (
            session.query(ConditioningSegment)
            .filter(ConditioningSegment.sample_id == selected_sample.id).count()
        )
        result_count = (
            session.query(PhysicalPropertyResult)
            .filter(PhysicalPropertyResult.sample_id == selected_sample.id).count()
        )
        warning_bits = []
        if cond_count:
            warning_bits.append(f"{cond_count} conditioning segment(s) will be permanently deleted")
        if result_count:
            warning_bits.append(f"{result_count} quality test result(s) will be unlinked from this sample (kept, sample reference cleared)")
        warning = (". ".join(warning_bits) + "." ) if warning_bits else "No related records — deleting it is safe."

        def _do_delete_sample(_session=session, _id=selected_sample.id):
            _session.query(ConditioningSegment).filter(ConditioningSegment.sample_id == _id).delete(synchronize_session=False)
            _session.query(PhysicalPropertyResult).filter(PhysicalPropertyResult.sample_id == _id).update(
                {"sample_id": None}, synchronize_session="fetch"
            )
            _session.query(Sample).filter(Sample.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("sample_selected_id", None)

        if page_usable:
            delete_with_confirm(
                f"sample #{selected_sample.id}", _do_delete_sample, key_prefix=f"sample_{selected_sample.id}",
                extra_warning=warning,
            )
        else:
            st.caption("View-only access - deleting is restricted for your role.")

        # ---------------------------------------------------------------
        # Conditioning history for THIS sample only - nested here rather
        # than as a separate top-level section with its own sample picker,
        # so the "this belongs to that sample" relationship is obvious from
        # where the controls appear, not just from a field label. See the
        # module docstring for why this stays a related table (a sample can
        # have more than one conditioning stage) instead of flat fields on
        # Sample.
        # ---------------------------------------------------------------
        st.divider()
        st.markdown(f"**Conditioning history for sample #{selected_sample.id}**")
        st.caption(
            "What this sample went through before testing (e.g. Standard 23°C/50%RH, 24h). A "
            "sample can have more than one segment - e.g. an ambient hold followed by standard "
            "conditioning."
        )

        with st.expander("Add conditioning segment", expanded=False):
            if not page_usable:
                st.caption("View-only access - adding a conditioning segment is restricted for your role.")
            else:
                condition_choice = st.selectbox(
                    "Condition type *", CONDITIONING_TYPES, key=f"cond_type_select_{selected_sample.id}",
                )
                condition_other = None
                if condition_choice == "Other (specify)":
                    condition_other = st.text_input("Specify condition type", key=f"cond_type_other_{selected_sample.id}")
                default_temp, default_rh = CONDITIONING_TYPE_DEFAULTS[condition_choice]

                with st.form(f"add_conditioning_{selected_sample.id}"):
                    c1, c2 = st.columns(2)
                    temperature_c = c1.number_input(
                        "Temperature (°C)", step=0.1, value=default_temp if default_temp is not None else 0.0,
                        help="Prefilled from the condition type's nominal value - adjust to the actual chamber reading.",
                    )
                    relative_humidity_pct = c2.number_input(
                        "Relative humidity (%)", min_value=0.0, max_value=100.0, step=1.0,
                        value=default_rh if default_rh is not None else 0.0,
                    )
                    segment_start = combine_date_time("Segment start", f"cond_start_{selected_sample.id}")
                    segment_end = combine_date_time("Segment end", f"cond_end_{selected_sample.id}")
                    notes = st.text_area("Notes", key=f"cond_notes_{selected_sample.id}")
                    submitted = st.form_submit_button("Save conditioning segment")
                    if submitted:
                        final_condition_type = (
                            (condition_other or "").strip() if condition_choice == "Other (specify)" else condition_choice
                        )
                        if not final_condition_type:
                            st.error("Specify a condition type.")
                        elif segment_end < segment_start:
                            st.error("Segment end must not be before segment start.")
                        else:
                            session.add(
                                ConditioningSegment(
                                    sample_id=selected_sample.id,
                                    condition_type=final_condition_type,
                                    temperature_c=temperature_c or None,
                                    relative_humidity_pct=relative_humidity_pct or None,
                                    segment_start=segment_start,
                                    segment_end=segment_end,
                                    notes=notes,
                                )
                            )
                            session.commit()
                            st.success("Conditioning segment saved.")
                            st.rerun()

        segments_for_sample = (
            session.query(ConditioningSegment)
            .filter(ConditioningSegment.sample_id == selected_sample.id)
            .order_by(ConditioningSegment.id.desc())
            .all()
        )
        if not segments_for_sample:
            st.caption("No conditioning segments recorded yet for this sample.")
        else:
            cond_rows = [
                {
                    "Condition": c.condition_type,
                    "Temp (°C)": c.temperature_c,
                    "RH (%)": c.relative_humidity_pct,
                    "Start": c.segment_start,
                    "End": c.segment_end,
                }
                for c in segments_for_sample
            ]
            st.caption("Click a row to edit (and optionally delete) that conditioning segment.")
            cond_idx = clickable_table(cond_rows, key=f"conditioning_table_{selected_sample.id}")
            if cond_idx is not None:
                st.session_state["cond_selected_id"] = segments_for_sample[cond_idx].id
            else:
                st.session_state.pop("cond_selected_id", None)

            selected_cond_id = st.session_state.get("cond_selected_id")
            selected_cond = next((c for c in segments_for_sample if c.id == selected_cond_id), None)

            if selected_cond:
                st.markdown(f"**Edit conditioning segment #{selected_cond.id}**")
                # Condition type picker lives outside the form (same reason as the Add
                # form above): the "Other (specify)" text input only needs to appear
                # once the selectbox choice is known, which requires a rerun between
                # them - form-internal widgets don't rerun until submit.
                current_type = selected_cond.condition_type or ""
                edit_condition_choice = st.selectbox(
                    "Condition type *",
                    CONDITIONING_TYPES,
                    index=CONDITIONING_TYPES.index(current_type) if current_type in CONDITIONING_TYPES
                    else CONDITIONING_TYPES.index("Other (specify)"),
                    key=f"edit_cond_type_select_{selected_cond.id}",
                )
                edit_condition_other = None
                if edit_condition_choice == "Other (specify)":
                    edit_condition_other = st.text_input(
                        "Specify condition type",
                        value=current_type if current_type not in CONDITIONING_TYPES else "",
                        key=f"edit_cond_type_other_{selected_cond.id}",
                    )
                with st.form(f"edit_cond_{selected_cond.id}"):
                    ec1, ec2 = st.columns(2)
                    e_temp = ec1.number_input(
                        "Temperature (°C)", step=0.1, value=float(selected_cond.temperature_c or 0.0),
                        key=f"edit_cond_temp_{selected_cond.id}",
                    )
                    e_rh = ec2.number_input(
                        "Relative humidity (%)", min_value=0.0, max_value=100.0, step=1.0,
                        value=float(selected_cond.relative_humidity_pct or 0.0), key=f"edit_cond_rh_{selected_cond.id}",
                    )
                    e_start = combine_date_time(
                        "Segment start", f"edit_cond_start_{selected_cond.id}",
                        default_date=selected_cond.segment_start.date() if selected_cond.segment_start else None,
                        default_time=selected_cond.segment_start.time() if selected_cond.segment_start else None,
                    )
                    e_end = combine_date_time(
                        "Segment end", f"edit_cond_end_{selected_cond.id}",
                        default_date=selected_cond.segment_end.date() if selected_cond.segment_end else None,
                        default_time=selected_cond.segment_end.time() if selected_cond.segment_end else None,
                    )
                    e_notes = st.text_area("Notes", value=selected_cond.notes or "", key=f"edit_cond_notes_{selected_cond.id}")
                    if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
                        e_condition_type = (
                            (edit_condition_other or "").strip()
                            if edit_condition_choice == "Other (specify)"
                            else edit_condition_choice
                        )
                        if not e_condition_type:
                            st.error("Specify a condition type.")
                        elif e_end < e_start:
                            st.error("Segment end must not be before segment start.")
                        else:
                            selected_cond.condition_type = e_condition_type
                            selected_cond.temperature_c = e_temp or None
                            selected_cond.relative_humidity_pct = e_rh or None
                            selected_cond.segment_start = e_start
                            selected_cond.segment_end = e_end
                            selected_cond.notes = e_notes
                            session.commit()
                            st.success("Conditioning segment updated.")
                            st.rerun()

                def _do_delete_cond(_session=session, _id=selected_cond.id):
                    _session.query(ConditioningSegment).filter(ConditioningSegment.id == _id).delete(synchronize_session=False)
                    _session.commit()
                    st.session_state.pop("cond_selected_id", None)

                if page_usable:
                    delete_with_confirm(
                        f"conditioning segment #{selected_cond.id}", _do_delete_cond, key_prefix=f"cond_{selected_cond.id}",
                        extra_warning="This is a leaf record — deleting it has no other effects.",
                    )
                else:
                    st.caption("View-only access - deleting is restricted for your role.")

                if st.button("Clear selection", key=f"clear_cond_selection_{selected_sample.id}"):
                    st.session_state.pop("cond_selected_id", None)
                    st.rerun()

        if st.button("Clear selection", key="clear_sample_selection"):
            st.session_state.pop("sample_selected_id", None)
            st.session_state.pop("cond_selected_id", None)
            st.rerun()
