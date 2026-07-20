"""Screen 6: Physical Property Result

Extended with sample and conditioning capture per the Mandatory-tier
recommendation in "Expanding PI3 Plant Edition Production-Trial Data
Capture": a lab result is only comparable if it is tied to where in the
block the sample came from, its cure age, and its conditioning history —
not analyzed as a bare number.
"""

import datetime as dt

import streamlit as st

from auth import logout_button, require_login
from db import ZONE_LABELS, ConditioningSegment, PhysicalPropertyResult, Sample, TrialRecord, get_session, init_db
from helpers import combine_date_time, page_setup, show_advisory_footer

page_setup("Physical Property Result")
init_db()
require_login()
logout_button()

st.title("Physical Property Result")
session = get_session()

trials = session.query(TrialRecord).order_by(TrialRecord.created_at.desc()).all()
if not trials:
    st.warning("Create a trial first (Production Run / Trial Record page).")
    st.stop()

PROPERTY_NAMES = ["Density", "Hardness", "Tensile strength", "Elongation", "Compression set", "Airflow", "Other"]

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
        trial_for_sample = st.selectbox(
            "Trial *",
            trials,
            format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name} ({t.status})",
            key="sample_trial_select",
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
                    production_run_id=trial_for_sample.production_run_id,
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
        with st.form("add_conditioning"):
            sample_for_cond = st.selectbox(
                "Sample *",
                samples,
                format_func=lambda s: f"Sample #{s.id} — {s.zone_label} (run {s.production_run_id})",
            )
            condition_type = st.text_input("Condition type * (e.g. Standard 23°C/50%RH)")
            c1, c2 = st.columns(2)
            temperature_c = c1.number_input("Temperature (°C)", step=0.1)
            relative_humidity_pct = c2.number_input("Relative humidity (%)", min_value=0.0, max_value=100.0, step=1.0)
            segment_start = combine_date_time("Segment start", "cond_start")
            segment_end = combine_date_time("Segment end", "cond_end")
            notes = st.text_area("Notes", key="cond_notes")
            submitted = st.form_submit_button("Save conditioning segment")
            if submitted:
                if not condition_type:
                    st.error("Condition type is required.")
                elif segment_end < segment_start:
                    st.error("Segment end must not be before segment start.")
                else:
                    session.add(
                        ConditioningSegment(
                            sample_id=sample_for_cond.id,
                            condition_type=condition_type,
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
st.subheader("📏 Physical property results")

with st.expander("Add physical property result", expanded=False):
    with st.form("add_property_result"):
        trial = st.selectbox(
            "Trial *",
            trials,
            format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name} ({t.status})",
            key="result_trial_select",
        )
        samples_for_run = (
            session.query(Sample).filter(Sample.production_run_id == trial.production_run_id).all()
            if trial
            else []
        )
        sample = st.selectbox(
            "Sample (optional, but recommended for comparability)",
            [None] + samples_for_run,
            format_func=lambda s: "— not linked to a sample —" if s is None else f"Sample #{s.id} — {s.zone_label}",
        )
        property_name = st.selectbox("Property *", PROPERTY_NAMES)
        c1, c2, c3 = st.columns(3)
        target_value = c1.number_input("Target value", step=0.1)
        actual_value = c2.number_input("Actual value", step=0.1)
        unit = c3.text_input("Unit (e.g. kg/m3, N, kPa, %)")
        c4, c5, c6 = st.columns(3)
        test_method = c4.text_input("Test method (e.g. ASTM D3574, ISO 845)")
        method_revision = c5.text_input("Method revision (e.g. 2017)")
        replicate_no = c6.number_input("Replicate no.", min_value=1, step=1, value=1)
        tested_at = st.date_input("Tested on", value=dt.date.today())
        submitted = st.form_submit_button("Save result")
        if submitted:
            pass_fail = None
            if target_value and actual_value:
                # simple +/-10% band as a working default; refine with real specs later
                lower, upper = target_value * 0.9, target_value * 1.1
                pass_fail = "Pass" if lower <= actual_value <= upper else "Fail"
            session.add(
                PhysicalPropertyResult(
                    trial_record_id=trial.id,
                    sample_id=sample.id if sample else None,
                    property_name=property_name,
                    target_value=target_value or None,
                    actual_value=actual_value or None,
                    unit=unit,
                    pass_fail=pass_fail,
                    test_method=test_method,
                    method_revision=method_revision,
                    replicate_no=int(replicate_no),
                    tested_at=tested_at,
                )
            )
            session.commit()
            st.success("Physical property result saved.")
            st.rerun()

st.divider()
st.subheader("Results by trial")

for t in trials:
    if not t.physical_property_results:
        continue
    with st.container(border=True):
        st.markdown(f"**Trial #{t.id}** — {t.production_run.foam_grade.grade_name}")
        st.dataframe(
            [
                {
                    "Property": r.property_name,
                    "Target": r.target_value,
                    "Actual": r.actual_value,
                    "Unit": r.unit,
                    "Pass/Fail": r.pass_fail,
                    "Sample": f"#{r.sample_id} ({r.sample.zone_label})" if r.sample else "—",
                    "Method": r.test_method,
                    "Rev.": r.method_revision,
                    "Replicate": r.replicate_no,
                    "Tested": r.tested_at,
                }
                for r in t.physical_property_results
            ],
            hide_index=True,
            use_container_width=True,
        )

show_advisory_footer()
