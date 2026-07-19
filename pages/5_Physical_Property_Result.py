"""Screen 6: Physical Property Result"""

import datetime as dt

import streamlit as st

from auth import logout_button, require_login
from db import PhysicalPropertyResult, TrialRecord, get_session, init_db
from helpers import page_setup, show_advisory_footer

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

with st.expander("Add physical property result", expanded=False):
    with st.form("add_property_result"):
        trial = st.selectbox(
            "Trial *",
            trials,
            format_func=lambda t: f"Trial #{t.id} — {t.production_run.foam_grade.grade_name} ({t.status})",
        )
        property_name = st.selectbox("Property *", PROPERTY_NAMES)
        c1, c2, c3 = st.columns(3)
        target_value = c1.number_input("Target value", step=0.1)
        actual_value = c2.number_input("Actual value", step=0.1)
        unit = c3.text_input("Unit (e.g. kg/m3, N, kPa, %)")
        test_method = st.text_input("Test method")
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
                    property_name=property_name,
                    target_value=target_value or None,
                    actual_value=actual_value or None,
                    unit=unit,
                    pass_fail=pass_fail,
                    test_method=test_method,
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
                    "Method": r.test_method,
                    "Tested": r.tested_at,
                }
                for r in t.physical_property_results
            ],
            hide_index=True,
            use_container_width=True,
        )

show_advisory_footer()
