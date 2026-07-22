"""Screen 6: Quality Test Result

Extended with sample and conditioning capture per the Mandatory-tier
recommendation in "Expanding PI3 Plant Edition Production-Trial Data
Capture": a lab result is only comparable if it is tied to where in the
block the sample came from, its cure age, and its conditioning history —
not analyzed as a bare number.

Keyed primarily to the production run (every batch gets quality results,
trial or not). Linking to a trial is optional and only relevant when the
result is part of a formal experiment's evidence trail.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from auth import logout_button, require_login
from db import (
    CONDITIONING_TYPE_DEFAULTS,
    CONDITIONING_TYPES,
    ZONE_LABELS,
    ConditioningSegment,
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
from helpers import combine_date_time, csv_excel_uploader, page_setup

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
session = get_session()

runs = session.query(ProductionRun).order_by(ProductionRun.created_at.desc()).all()
if not runs:
    st.warning("Create a production run first (Production Run page).")
    st.stop()

# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------
st.subheader("🧊 Samples")
st.caption(
    "Where in the block a sample was taken, and when. Lab results should be linked to a sample "
    "rather than analyzed as a bare number, so density/compression can be mapped back to location "
    "and cure age."
)

with st.expander("Add sample", expanded=False):
    with st.form("add_sample"):
        run_for_sample = st.selectbox(
            "Production run *",
            runs,
            format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
            key="sample_run_select",
        )
        zone_label = st.selectbox("Zone *", ZONE_LABELS)
        c1, c2, c3 = st.columns(3)
        x_mm = c1.number_input("X position (mm)", step=1.0)
        y_mm = c2.number_input("Y position (mm)", step=1.0)
        z_mm = c3.number_input("Z position (mm)", step=1.0)
        sample_ts = combine_date_time("Sample extraction time", "sample_ts")
        cure_age_hours = st.number_input("Cure age at sampling (hours)", min_value=0.0, step=0.5)
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save sample")
        if submitted:
            session.add(
                Sample(
                    production_run_id=run_for_sample.id,
                    sample_ts=sample_ts,
                    zone_label=zone_label,
                    x_mm=x_mm or None,
                    y_mm=y_mm or None,
                    z_mm=z_mm or None,
                    cure_age_hours=cure_age_hours or None,
                    notes=notes,
                )
            )
            session.commit()
            st.success("Sample saved.")
            st.rerun()

samples = session.query(Sample).order_by(Sample.id.desc()).all()
if samples:
    with st.expander(f"Existing samples ({len(samples)})"):
        st.dataframe(
            [
                {
                    "Sample ID": s.id,
                    "Run": s.production_run_id,
                    "Zone": s.zone_label,
                    "X/Y/Z (mm)": f"{s.x_mm or '—'}/{s.y_mm or '—'}/{s.z_mm or '—'}",
                    "Cure age (h)": s.cure_age_hours,
                    "Sampled": s.sample_ts,
                }
                for s in samples
            ],
            hide_index=True,
            use_container_width=True,
        )

st.divider()
st.subheader("🌡️ Conditioning")
st.caption("Conditioning history for a sample before testing (e.g. Standard 23°C/50%RH, 24h).")

if not samples:
    st.info("Add a sample above before recording conditioning.")
else:
    with st.expander("Add conditioning segment", expanded=False):
        sample_for_cond = st.selectbox(
            "Sample *",
            samples,
            format_func=lambda s: f"Sample #{s.id} — {s.zone_label} (run {s.production_run_id})",
            key="cond_sample_select",
        )
        condition_choice = st.selectbox(
            "Condition type *",
            CONDITIONING_TYPES,
            key="cond_type_select",
        )
        condition_other = None
        if condition_choice == "Other (specify)":
            condition_other = st.text_input("Specify condition type", key="cond_type_other")
        default_temp, default_rh = CONDITIONING_TYPE_DEFAULTS[condition_choice]

        with st.form("add_conditioning"):
            c1, c2 = st.columns(2)
            temperature_c = c1.number_input(
                "Temperature (°C)", step=0.1, value=default_temp if default_temp is not None else 0.0,
                help="Prefilled from the condition type's nominal value - adjust to the actual chamber reading.",
            )
            relative_humidity_pct = c2.number_input(
                "Relative humidity (%)", min_value=0.0, max_value=100.0, step=1.0,
                value=default_rh if default_rh is not None else 0.0,
            )
            segment_start = combine_date_time("Segment start", "cond_start")
            segment_end = combine_date_time("Segment end", "cond_end")
            notes = st.text_area("Notes", key="cond_notes")
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
                            sample_id=sample_for_cond.id,
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

    recent_conditioning = (
        session.query(ConditioningSegment).order_by(ConditioningSegment.id.desc()).limit(30).all()
    )
    if recent_conditioning:
        with st.expander(f"Recent conditioning segments ({len(recent_conditioning)} shown, max 30)"):
            st.dataframe(
                [
                    {
                        "Sample": c.sample_id,
                        "Condition": c.condition_type,
                        "Temp (°C)": c.temperature_c,
                        "RH (%)": c.relative_humidity_pct,
                        "Start": c.segment_start,
                        "End": c.segment_end,
                    }
                    for c in recent_conditioning
                ],
                hide_index=True,
                use_container_width=True,
            )

# ---------------------------------------------------------------------------
# Physical property results
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📏 Quality test results")

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
                pass_fail = None
                if target_value and actual_value:
                    # simple +/-10% band as a working default; refine with real specs later
                    lower, upper = target_value * 0.9, target_value * 1.1
                    pass_fail = "Pass" if lower <= actual_value <= upper else "Fail"
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
    st.caption(
        "property_name must match a name in the physical property master list (case-insensitive). "
        "test_method and unit are stored as typed — they don't need to match an existing method/UOM."
    )
    result_df, result_filename = csv_excel_uploader(
        RESULT_REQUIRED_COLUMNS, RESULT_OPTIONAL_COLUMNS, key="result_upload"
    )
    if result_df is not None:
        run_ids = {r.id for r in runs}
        defs_by_name = {p.name.strip().lower(): p for p in property_defs}
        samples_all = {s.id: s for s in session.query(Sample).all()}
        trials_all = {t.id: t for t in session.query(TrialRecord).all()}

        good_rows, bad_rows = [], []
        for _, row in result_df.iterrows():
            try:
                prop_def = defs_by_name.get(str(row.get("property_name", "")).strip().lower())
                run_ok = row.get("production_run_id") in run_ids
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
            st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

        if good_rows and st.button("Confirm import", key="confirm_result_import"):
            for row in good_rows:
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
                pass_fail = None
                if not pd.isna(target_val) and not pd.isna(actual_val) and target_val:
                    lower, upper = target_val * 0.9, target_val * 1.1
                    pass_fail = "Pass" if lower <= actual_val <= upper else "Fail"
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
            st.success(f"Imported {len(good_rows)} quality test result(s) from {result_filename}.")
            st.rerun()

st.divider()
st.subheader("Results by production run")

for r_run in runs:
    results = (
        session.query(PhysicalPropertyResult)
        .filter(PhysicalPropertyResult.production_run_id == r_run.id)
        .all()
    )
    if not results:
        continue
    with st.container(border=True):
        st.markdown(f"**Run #{r_run.id}** — {r_run.foam_grade.grade_name} · {r_run.run_date}")
        st.dataframe(
            [
                {
                    "Property": r.property_name,
                    "Target": r.target_value,
                    "Actual": r.actual_value,
                    "Unit": r.unit,
                    "Pass/Fail": r.pass_fail,
                    "Sample": f"#{r.sample_id} ({r.sample.zone_label})" if r.sample else "—",
                    "Trial": f"#{r.trial_record_id}" if r.trial_record_id else "—",
                    "Method": r.test_method,
                    "Rev.": r.method_revision,
                    "Replicate": r.replicate_no,
                    "Tested": r.tested_at,
                    "Notes": r.notes,
                }
                for r in results
            ],
            hide_index=True,
            use_container_width=True,
        )

