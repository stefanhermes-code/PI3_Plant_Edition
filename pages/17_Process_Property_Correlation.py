"""Industrial Intelligence: Process-Property Correlation

Cross-references a machine/process setting (Finalized-phase mixer rpm,
ratio/index, air pressure, ...) against a physical property outcome for
the same production runs, to surface which settings actually move the
needle on quality - the direct answer to "does this setting matter, and
how much".
"""

import streamlit as st

from analytics import (
    PHASE_SETTING_FIELDS,
    PHASE_SETTING_LABELS,
    merged_run_property_dataframe,
    property_results_dataframe,
)
from auth import logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import page_setup

page_setup("Process-Property Correlation")
init_db()
require_login()
logout_button()

st.title("Process-Property Correlation")
st.caption(
    "Cross-references a machine/process setting against a physical property outcome for the "
    "same production runs, to show whether - and how strongly - the setting is associated with "
    "the result."
)
session = get_session()

grades = session.query(FoamGrade).all()
if not grades:
    st.warning("Add a foam grade first.")
    st.stop()

c1, c2, c3 = st.columns(3)
grade = c1.selectbox("Foam grade", grades, format_func=lambda g: g.grade_name)
setting_field = c2.selectbox(
    "Process setting", PHASE_SETTING_FIELDS, format_func=lambda f: PHASE_SETTING_LABELS.get(f, f)
)

# property choice depends on what's actually been recorded for this grade
grade_results_df = property_results_dataframe(session, foam_grade_id=grade.id)
available_properties = (
    sorted(grade_results_df["property_name"].dropna().unique()) if not grade_results_df.empty else []
)

if not available_properties:
    st.info("No quality test results recorded yet for this foam grade.")
    st.stop()

property_name = c3.selectbox("Property", available_properties)

merged = merged_run_property_dataframe(session, grade.id, property_name)
merged = merged.dropna(subset=[setting_field, "actual_value"])

if len(merged) < 2:
    st.info(
        "Not enough runs with both this process setting and this property recorded yet to "
        "compare (need at least 2)."
    )
else:
    chart_df = merged[[setting_field, "actual_value"]].rename(
        columns={setting_field: PHASE_SETTING_LABELS.get(setting_field, setting_field), "actual_value": property_name}
    )
    st.scatter_chart(chart_df, x=PHASE_SETTING_LABELS.get(setting_field, setting_field), y=property_name)

    if len(merged) >= 3:
        corr = merged[setting_field].corr(merged["actual_value"])
        direction = "positive" if corr > 0 else ("negative" if corr < 0 else "no")
        st.metric(f"Correlation ({PHASE_SETTING_LABELS.get(setting_field, setting_field)} vs {property_name})", f"{corr:.2f}")
        st.caption(
            f"A {direction} association across {len(merged)} runs. Historical pattern for technical "
            "review - confirm against current raw materials and process conditions before treating "
            "it as causal."
        )
    else:
        st.caption(f"Only {len(merged)} runs available - too few for a reliable correlation figure yet.")

    st.dataframe(
        merged[["run_id", "run_date", "recipe_version", "machine", setting_field, "actual_value", "target_value"]],
        hide_index=True,
        use_container_width=True,
    )

