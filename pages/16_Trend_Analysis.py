"""Industrial Intelligence: Trend Analysis

Tracks a physical property over time for a foam grade (optionally filtered
to one recipe version or machine), against its target value, so drift
shows up before it becomes a recurring quality observation.
"""

import streamlit as st

from analytics import property_results_dataframe
from auth import logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import page_setup, show_advisory_footer

page_setup("Trend Analysis")
init_db()
require_login()
logout_button()

st.title("Trend Analysis")
st.caption(
    "Plots a property's actual results over time against its target, so drift is visible "
    "before it turns into a recurring quality observation."
)
session = get_session()

grades = session.query(FoamGrade).all()
if not grades:
    st.warning("Add a foam grade first.")
    st.stop()

grade = st.selectbox("Foam grade", grades, format_func=lambda g: g.grade_name)
results_df = property_results_dataframe(session, foam_grade_id=grade.id)

if results_df.empty:
    st.info("No physical property results recorded yet for this foam grade.")
    st.stop()

properties = sorted(results_df["property_name"].dropna().unique())
property_name = st.selectbox("Property", properties)

c1, c2 = st.columns(2)
recipe_versions = sorted(results_df["recipe_version"].dropna().unique())
recipe_filter = c1.selectbox("Recipe version filter", ["All"] + list(recipe_versions))
machines = sorted(m for m in results_df["machine"].dropna().unique())
machine_filter = c2.selectbox("Machine filter", ["All"] + list(machines))

filtered = results_df[results_df["property_name"] == property_name].copy()
if recipe_filter != "All":
    filtered = filtered[filtered["recipe_version"] == recipe_filter]
if machine_filter != "All":
    filtered = filtered[filtered["machine"] == machine_filter]
filtered = filtered.dropna(subset=["tested_at"]).sort_values("tested_at")

if filtered.empty:
    st.info("No results match these filters.")
else:
    chart_df = filtered.set_index("tested_at")[["actual_value", "target_value"]]
    st.line_chart(chart_df)

    n = len(filtered)
    if n >= 2:
        mid = n // 2
        first_half_avg = filtered["actual_value"].iloc[:mid].mean()
        second_half_avg = filtered["actual_value"].iloc[mid:].mean()
        direction = "up" if second_half_avg > first_half_avg else ("down" if second_half_avg < first_half_avg else "flat")
        st.caption(
            f"{n} results. Earlier-period average: {first_half_avg:.2f}. "
            f"Later-period average: {second_half_avg:.2f} (trending {direction})."
        )

    st.dataframe(
        filtered[["tested_at", "recipe_version", "machine", "actual_value", "target_value", "pass_fail"]],
        hide_index=True,
        use_container_width=True,
    )

show_advisory_footer()
