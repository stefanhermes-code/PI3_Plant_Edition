"""Industrial Intelligence: Machine Settings Optimization

Buckets a process setting (mixer rpm, ratio/index, air pressure, ...) into
low/medium/high ranges across every run of a foam grade, and shows which
range has historically landed closest to the property's target - a
starting range for technical review, not an automatic setpoint change.
"""

import pandas as pd
import streamlit as st

from analytics import PHASE_SETTING_FIELDS, PHASE_SETTING_LABELS, merged_run_property_dataframe, property_results_dataframe
from auth import logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import page_setup, show_advisory_footer

page_setup("Machine Settings Optimization")
init_db()
require_login()
logout_button()

st.title("Machine Settings Optimization")
st.caption(
    "Groups a process setting into low/medium/high ranges across a foam grade's production "
    "runs, and shows which range has historically landed closest to the property's target — a "
    "starting range for review, not an automatic setpoint change."
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

grade_results_df = property_results_dataframe(session, foam_grade_id=grade.id)
available_properties = (
    sorted(grade_results_df["property_name"].dropna().unique()) if not grade_results_df.empty else []
)
if not available_properties:
    st.info("No physical property results recorded yet for this foam grade.")
    st.stop()

property_name = c3.selectbox("Property", available_properties)

merged = merged_run_property_dataframe(session, grade.id, property_name)
merged = merged.dropna(subset=[setting_field, "actual_value"])

if len(merged) < 3:
    st.info("Need at least 3 runs with both this setting and this property recorded to compare ranges.")
    st.stop()

merged = merged.copy()
merged["deviation_pct"] = ((merged["actual_value"] - merged["target_value"]) / merged["target_value"]).abs()
merged.loc[merged["target_value"].isna() | (merged["target_value"] == 0), "deviation_pct"] = float("nan")

merged["range"] = None
for q, labels in ((3, ["Low", "Medium", "High"]), (2, ["Low", "High"])):
    try:
        merged["range"] = pd.qcut(merged[setting_field], q=q, labels=labels, duplicates="drop")
        break
    except ValueError:
        continue

if merged["range"].isna().all() or merged["range"].nunique(dropna=True) < 2:
    st.info(
        f"Not enough variation in {PHASE_SETTING_LABELS.get(setting_field, setting_field)} across these "
        "runs yet to split into ranges — showing the raw data instead."
    )
    st.dataframe(
        merged[["run_id", "run_date", setting_field, "actual_value", "target_value"]],
        hide_index=True,
        use_container_width=True,
    )
else:
    summary = (
        merged.groupby("range", observed=True)
        .agg(
            setting_range=(setting_field, lambda s: f"{s.min():g}–{s.max():g}"),
            avg_actual=("actual_value", "mean"),
            avg_target=("target_value", "mean"),
            avg_abs_deviation_pct=("deviation_pct", "mean"),
            runs=("run_id", "count"),
        )
        .reset_index()
    )
    summary["avg_actual"] = summary["avg_actual"].round(2)
    summary["avg_target"] = summary["avg_target"].round(2)
    summary["avg_abs_deviation_pct"] = (summary["avg_abs_deviation_pct"] * 100).round(1)

    st.dataframe(summary, hide_index=True, use_container_width=True)

    with_deviation = summary.dropna(subset=["avg_abs_deviation_pct"])
    if not with_deviation.empty:
        best = with_deviation.sort_values("avg_abs_deviation_pct").iloc[0]
        st.caption(
            f"Closest to target historically: **{best['range']}** range "
            f"({PHASE_SETTING_LABELS.get(setting_field, setting_field)} {best['setting_range']}), "
            f"averaging {best['avg_abs_deviation_pct']:.1f}% deviation from target across "
            f"{int(best['runs'])} run(s). Review applicability against current raw materials and "
            "process conditions before adjusting settings."
        )

    st.scatter_chart(
        merged.rename(columns={setting_field: PHASE_SETTING_LABELS.get(setting_field, setting_field)}),
        x=PHASE_SETTING_LABELS.get(setting_field, setting_field),
        y="actual_value",
    )

show_advisory_footer()
