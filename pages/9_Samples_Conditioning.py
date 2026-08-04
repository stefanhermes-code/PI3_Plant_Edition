"""Screen: Production Samples

Restructured 2026-08-04 as part of the Samples & Trials nav-section
rework: this page used to be "Samples & Conditioning," covering all three
sample sources (production run, customer trial, optimization trial) plus
a per-sample conditioning history. Per user direction, the app is now
split so each source has its own home - production-run samples live here,
customer-trial samples live on Customer Trials & Samples (with their own
Create/Edit-Delete/CSV-import subtabs nested under that page's "Create
Trial" tab), and optimization-trial samples live the same way on
Optimization Trials & Samples. This page is now production-run-only.

Conditioning (the per-sample temperature/humidity history before testing)
was eliminated entirely in the same batch, per explicit user direction
("drop the conditioning, it is irrelevant") - not just removed from this
page's UI, but the ConditioningSegment model and its conditioning_segments
table are gone from the app and the database. A lab result's traceability
now stops at which sample/zone it came from; conditioning history is no
longer tracked.

Kept as the samples_conditioning page-access-control key (unchanged) so
every role's existing permissions carry over without reconfiguration -
only the page's title, scope, and content changed, not its identity in
the User Roles matrix.
"""

import datetime as dt

import pandas as pd
import streamlit as st

import reports
from access_control import can_use_page
from auth import current_user, logout_button, require_login
from db import (
    ZONE_LABELS,
    PhysicalPropertyResult,
    ProductionPhase,
    ProductionRun,
    Sample,
    get_session,
    init_db,
)
from helpers import (
    clickable_table,
    combine_date_time,
    csv_excel_uploader,
    dedupe_import_rows,
    delete_with_confirm,
    log_export_click,
    page_setup,
    render_data_table,
    render_function_action_intro,
    set_pending_banner,
    show_pending_banner,
    view_only_notice,
)
from tenant_scope import apply_scope, company_picker, run_ids_for_company

SAMPLE_REQUIRED_COLUMNS = ["production_run_id", "zone_label"]
SAMPLE_OPTIONAL_COLUMNS = ["sample_ts", "notes"]

page_setup("Production Samples")
init_db()
require_login()
logout_button()

st.title("Production Samples")
render_function_action_intro(
    function_text=(
        "Records where in the block a production-run sample was taken, for traceability back to "
        "a lab result. Customer Trial and Optimization Trial samples live on their own pages now "
        "(Customer Trials & Samples / Optimization Trials & Samples), alongside the trial itself."
    ),
    action_text=(
        "Pick the production run this sample came from, then record its block zone and creation "
        "time. Use the CSV/Excel import tab to bulk-load a batch of samples at once instead of "
        "entering them one by one."
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

runs = (
    apply_scope(session.query(ProductionRun), ProductionRun.id, run_ids)
    .order_by(ProductionRun.created_at.desc())
    .all()
)
if not runs:
    st.warning("Create a production run first (Production Run page).")
    st.stop()

tab_create, tab_edit_delete, tab_import, tab_report = st.tabs(
    ["Create Sample", "Edit/Delete Sample", "CSV / Excel import", "Sample Report"]
)

with tab_create:
    if not page_usable:
        st.caption("View-only access - adding a sample is restricted for your role.")
    else:
        # Run picker lives outside the form: the creation-time validation
        # below depends on which run is picked, and form-internal widgets
        # don't rerun until submit.
        run = st.selectbox(
            "Production run *", runs,
            format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
            key="sample_run_select",
        )
        with st.form("add_sample"):
            zone_label = st.selectbox("Zone *", ZONE_LABELS)
            sample_ts = combine_date_time("Sample creation time", "sample_ts")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save sample")
            if submitted:
                phases_for_run = (
                    session.query(ProductionPhase)
                    .filter(ProductionPhase.production_run_id == run.id).all()
                )
                earliest_start = min(
                    (p.phase_start for p in phases_for_run if p.phase_start), default=None
                )
                if earliest_start and sample_ts < earliest_start:
                    st.error(
                        f"Sample creation time ({sample_ts:%Y-%m-%d %H:%M}) is before this run started "
                        f"({earliest_start:%Y-%m-%d %H:%M}). Check the date/time."
                    )
                else:
                    session.add(
                        Sample(production_run_id=run.id, zone_label=zone_label, sample_ts=sample_ts, notes=notes)
                    )
                    session.commit()
                    st.success("Sample saved.")
                    st.rerun()

with tab_import:
    show_pending_banner("sample_import_msg")
    st.caption(
        "Required columns: production_run_id, zone_label. Optional columns: sample_ts, notes. "
        "production_run_id must be one of your production runs."
    )
    sample_df, sample_filename = csv_excel_uploader(
        SAMPLE_REQUIRED_COLUMNS, SAMPLE_OPTIONAL_COLUMNS, key="sample_upload"
    )
    if sample_df is not None:
        import_run_ids = {r.id for r in runs}

        good_rows, bad_rows = [], []
        for _, row in sample_df.iterrows():
            try:
                run_id_val = int(row.get("production_run_id"))
            except (TypeError, ValueError):
                run_id_val = None
            if run_id_val in import_run_ids and str(row.get("zone_label", "")).strip():
                good_rows.append(row)
            else:
                bad_rows.append(row)

        st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged as invalid: **{len(bad_rows)}**")
        if bad_rows:
            st.warning("These rows are missing zone_label, or production_run_id isn't one of your production runs.")
            render_data_table(pd.DataFrame(bad_rows), max_height="300px")

        if good_rows and st.button("Confirm import", key="confirm_sample_import", disabled=not page_usable):
            existing_keys = {
                (s.production_run_id, (s.zone_label or "").strip().lower())
                for s in session.query(Sample).filter(Sample.production_run_id.in_(import_run_ids)).all()
            }

            def _dedupe_key(row):
                return (int(row["production_run_id"]), str(row["zone_label"]).strip().lower())

            new_rows, dup_rows = dedupe_import_rows(good_rows, existing_keys, key_func=_dedupe_key)
            for row in new_rows:
                session.add(
                    Sample(
                        production_run_id=int(row["production_run_id"]),
                        zone_label=str(row["zone_label"]).strip(),
                        sample_ts=pd.to_datetime(row.get("sample_ts"), errors="coerce"),
                        notes=str(row.get("notes", "") or ""),
                    )
                )
            session.commit()
            msg = f"Imported {len(new_rows)} sample(s) from {sample_filename}."
            if dup_rows:
                msg += f" Skipped {len(dup_rows)} row(s) already recorded for their run + zone (likely a repeat click)."
            set_pending_banner("sample_import_msg", msg)
            st.rerun()

with tab_edit_delete:
    samples = (
        session.query(Sample)
        .filter(Sample.production_run_id.in_([r.id for r in runs]))
        .order_by(Sample.id.desc())
        .all()
    )
    if not samples:
        st.info("No samples recorded yet.")
    else:
        sample_rows = [
            {
                "Sample ID": s.id,
                "Run": f"Run #{s.production_run_id} — {s.production_run.foam_grade.grade_name}" if s.production_run else f"Run #{s.production_run_id}",
                "Zone": s.zone_label,
                "Sampled": s.sample_ts,
            }
            for s in samples
        ]
        st.caption("Click a row to edit (and optionally delete) that sample.")
        idx = clickable_table(sample_rows, key="samples_table")
        if idx is not None:
            st.session_state["sample_selected_id"] = samples[idx].id
        else:
            st.session_state.pop("sample_selected_id", None)

        selected_sample_id = st.session_state.get("sample_selected_id")
        selected_sample = next((s for s in samples if s.id == selected_sample_id), None)

        if selected_sample:
            st.divider()
            st.markdown(f"**Edit sample #{selected_sample.id}** — Run #{selected_sample.production_run_id}")
            st.caption("Which run a sample belongs to can't be changed here - delete and re-add it under the correct run instead.")
            with st.form(f"edit_sample_{selected_sample.id}"):
                e_zone = st.selectbox(
                    "Zone *", ZONE_LABELS,
                    index=ZONE_LABELS.index(selected_sample.zone_label) if selected_sample.zone_label in ZONE_LABELS else 0,
                    key=f"edit_sample_zone_{selected_sample.id}",
                )
                e_sample_ts = combine_date_time(
                    "Sample creation time", f"edit_sample_ts_{selected_sample.id}",
                    default_date=selected_sample.sample_ts.date() if selected_sample.sample_ts else None,
                    default_time=selected_sample.sample_ts.time() if selected_sample.sample_ts else None,
                )
                e_notes = st.text_area("Notes", value=selected_sample.notes or "", key=f"edit_sample_notes_{selected_sample.id}")
                if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
                    phases_for_edit_run = (
                        session.query(ProductionPhase)
                        .filter(ProductionPhase.production_run_id == selected_sample.production_run_id).all()
                    )
                    earliest_start = min(
                        (p.phase_start for p in phases_for_edit_run if p.phase_start), default=None
                    )
                    if earliest_start and e_sample_ts < earliest_start:
                        st.error(
                            f"Sample creation time ({e_sample_ts:%Y-%m-%d %H:%M}) is before this run started "
                            f"({earliest_start:%Y-%m-%d %H:%M}). Check the date/time."
                        )
                    else:
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
            warning = (
                f"{result_count} quality test result(s) will be unlinked from this sample (kept, sample reference cleared)."
                if result_count else "No related records — deleting it is safe."
            )

            def _do_delete_sample(_session=session, _id=selected_sample.id):
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

            if st.button("Clear selection", key="clear_sample_selection"):
                st.session_state.pop("sample_selected_id", None)
                st.rerun()

with tab_report:
    st.caption(
        "Reports on samples currently in scope (your company's production runs). Narrow by zone "
        "and/or creation date below, then download - charts only, no raw sample list (use the "
        "Edit/Delete Sample tab's table for that)."
    )
    all_samples = (
        session.query(Sample)
        .filter(Sample.production_run_id.in_([r.id for r in runs]))
        .order_by(Sample.id.desc())
        .all()
    )
    if not all_samples:
        st.info("No samples recorded yet.")
    else:
        zone_options = sorted({s.zone_label for s in all_samples if s.zone_label})
        zone_filter = st.multiselect("Zone", zone_options, default=zone_options, key="sample_report_zone")
        rc1, rc2 = st.columns(2)
        date_from = rc1.date_input("Sampled from (optional)", value=None, key="sample_report_from")
        date_to = rc2.date_input("Sampled to (optional)", value=None, key="sample_report_to")

        filtered_samples = [
            s for s in all_samples
            if (s.zone_label in zone_filter)
            and (date_from is None or (s.sample_ts and s.sample_ts.date() >= date_from))
            and (date_to is None or (s.sample_ts and s.sample_ts.date() <= date_to))
        ]

        if zone_filter == zone_options:
            zone_label_text = "All zones"
        elif zone_filter:
            zone_label_text = ", ".join(zone_filter)
        else:
            zone_label_text = "None selected"
        date_label_text = (
            f"{date_from or 'earliest'} to {date_to or 'latest'}" if (date_from or date_to) else "All dates"
        )
        selection_label = f"Zone: {zone_label_text} · Sampled: {date_label_text} · {len(runs)} production run(s) in scope"
        st.caption(selection_label)

        report_data = reports.build_sample_report_data(
            session, "Production Run", [s.id for s in filtered_samples], {"selection_label": selection_label}
        )
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Samples in selection", report_data["total_samples"])
        rc2.metric("Coverage", f"{report_data['coverage_pct']}%" if report_data["coverage_pct"] is not None else "—")
        rc3.metric("Pass rate (linked results)", f"{report_data['pass_rate']}%" if report_data["pass_rate"] is not None else "—")

        if report_data["zone_breakdown"]:
            st.bar_chart(pd.DataFrame(report_data["zone_breakdown"]).set_index("Zone"))

        dl1, dl2 = st.columns(2)
        dl1.download_button(
            "Download PDF", data=reports.render_sample_report_pdf(report_data),
            file_name="production_samples_report.pdf", mime="application/pdf",
            key="sample_report_pdf", disabled=report_data["total_samples"] == 0,
            on_click=log_export_click, args=("sample_report_pdf",), kwargs={"description": selection_label},
        )
        dl2.download_button(
            "Download Excel", data=reports.render_sample_report_excel(report_data),
            file_name="production_samples_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="sample_report_excel", disabled=report_data["total_samples"] == 0,
            on_click=log_export_click, args=("sample_report_excel",), kwargs={"description": selection_label},
        )
