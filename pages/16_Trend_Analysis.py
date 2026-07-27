"""Industrial Intelligence: Trend Analysis

A plot of actual-vs-target over time can't, by itself, tell a reviewer
whether a wobble is real or just noise, or when a real shift actually
started - that is what statistical process control (SPC) exists for. This
page runs the standard SPC toolkit for one property tracked over
production runs: an individuals control chart with real control limits and
the classic Western Electric/Nelson run rules (catches sudden shifts),
process capability (Cpk) against the property's own tolerance band
(catches "in control but too close to spec"), a CUSUM chart (catches slow
sustained drift a control chart is bad at catching early - pump wear,
catalyst degradation, an off-spec raw material lot), and a formal trend
test (replaces an eyeballed first-half-vs-second-half average with an
actual significance test). All four are deterministic - see analytics.py.
PI3 is used only downstream of these numbers, to help interpret a real
flag against recipe changes, machine changes, and quality issue history -
never to guess whether a trend exists in the first place.
"""

import pandas as pd
import streamlit as st

import ai_assistant
from analytics import (
    capability_analysis,
    control_chart_analysis,
    cusum_analysis,
    property_results_dataframe,
    property_run_series,
    trend_test,
)
from auth import logout_button, require_login
from db import FoamGrade, QualityObservation, get_session, init_db
from helpers import page_setup, render_ask_pi3_section

page_setup("Trend Analysis")
init_db()
require_login()
logout_button()

st.title("Trend Analysis")
st.caption(
    "Statistical process control for one property over a foam grade's production history: a "
    "control chart with real control limits and run-rule flags, process capability against the "
    "tolerance band, a CUSUM chart for slow sustained drift, and a formal trend test - not just a "
    "line chart, so a real shift is flagged instead of left for the reviewer to eyeball."
)
session = get_session()

grades = session.query(FoamGrade).all()
if not grades:
    st.warning("Add a foam grade first.")
    st.stop()

grade = st.selectbox("Foam grade", grades, format_func=lambda g: g.grade_name)
results_df = property_results_dataframe(session, foam_grade_id=grade.id)

if results_df.empty:
    st.info("No quality test results recorded yet for this foam grade.")
    st.stop()

properties = sorted(results_df["property_name"].dropna().unique())
property_name = st.selectbox("Property", properties)

c1, c2 = st.columns(2)
recipe_versions = sorted(results_df["recipe_version"].dropna().unique())
recipe_filter = c1.selectbox("Recipe version filter", ["All"] + list(recipe_versions))
machines = sorted(m for m in results_df["machine"].dropna().unique())
machine_filter = c2.selectbox("Machine filter", ["All"] + list(machines))

series = property_run_series(session, grade.id, property_name)
if recipe_filter != "All":
    series = series[series["recipe_version"] == recipe_filter]
if machine_filter != "All":
    series = series[series["machine"] == machine_filter]
series = series.reset_index(drop=True)

if series.empty:
    st.info("No results match these filters.")
    st.stop()

st.caption(f"{len(series)} production run(s) with a {property_name} result, {series['tested_at'].min()} to {series['tested_at'].max()}.")

# ---------------------------------------------------------------------------
# Control chart
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Control chart (individuals / moving range)")
chart_result = control_chart_analysis(series)

if not chart_result["ready"]:
    st.info(
        f"Only {chart_result['n']} result(s) so far - a control chart needs at least 5 to estimate "
        "meaningful control limits. Add more production runs with this property tested to unlock this."
    )
else:
    chart_df = chart_result["chart_df"].set_index("tested_at")[
        ["actual_value", "center_line", "ucl", "lcl"]
    ]
    st.line_chart(chart_df)
    st.caption(
        f"Center line {chart_result['mean']:.3g}, control limits [{chart_result['lcl']:.3g}, "
        f"{chart_result['ucl']:.3g}] (+/-3 sigma, sigma estimated from the run-to-run moving range: "
        f"{chart_result['sigma']:.3g})."
    )
    if chart_result["in_control"]:
        st.success("No control-chart rule violations - this property looks statistically stable across these runs.")
    else:
        st.warning(f"{len(chart_result['flags'])} rule violation(s) found:")
        flags_display = pd.DataFrame(
            [
                {
                    "Rule": f["rule"],
                    "First seen": f["first_tested_at"],
                    "Run ID": f["first_run_id"],
                    "Points matching": f["points_matching"],
                }
                for f in chart_result["flags"]
            ]
        )
        st.dataframe(flags_display, hide_index=True, use_container_width=True)

# ---------------------------------------------------------------------------
# Process capability
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Process capability (Cpk)")
capability = capability_analysis(series)
if capability is None:
    st.info(
        "Not enough results, or no consistent target value recorded, to compute process capability yet."
    )
else:
    cpk = capability["cpk"]
    if cpk >= 1.33:
        capability_read = "capable - comfortable margin to the tolerance band"
    elif cpk >= 1.0:
        capability_read = "marginal - some results will likely fall outside the tolerance band"
    else:
        capability_read = "not capable - this process routinely produces results outside the tolerance band"
    m1, m2, m3 = st.columns(3)
    m1.metric("Cpk", f"{cpk:.2f}")
    m2.metric("Cpu (upper side)", f"{capability['cpu']:.2f}")
    m3.metric("Cpl (lower side)", f"{capability['cpl']:.2f}")
    st.caption(
        f"Against a tolerance band of {capability['lsl']:.3g}-{capability['usl']:.3g} (target "
        f"{capability['target']:.3g} +/-10%, the app's own pass/fail convention): **{capability_read}**. "
        "Cpk >= 1.33 is generally considered capable, 1.0-1.33 marginal, below 1.0 not capable."
    )

# ---------------------------------------------------------------------------
# CUSUM - slow sustained drift
# ---------------------------------------------------------------------------
st.divider()
st.subheader("CUSUM (slow sustained drift)")
cusum = cusum_analysis(series)
if cusum is None:
    st.info("Not enough results yet (need at least 8) to run a CUSUM drift check.")
else:
    cusum_df = cusum["chart_df"].copy()
    cusum_df["upper_limit"] = cusum["h"]
    cusum_df["lower_limit"] = -cusum["h"]
    st.line_chart(
        cusum_df.set_index("tested_at")[["cusum_positive", "cusum_negative", "upper_limit", "lower_limit"]]
    )
    st.caption(f"Measured against a reference of {cusum['reference']:.3g} (this property's target value).")
    if cusum["breach_index"] is None:
        st.success("No sustained drift detected - the cumulative sum stays within its decision limits.")
    else:
        st.warning(
            f"Sustained {cusum['breach_direction']} drift detected, first crossing the decision limit "
            f"at run {cusum['breach_run_id']} ({cusum['breach_tested_at']})."
        )

# ---------------------------------------------------------------------------
# Trend test
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Trend test")
trend = trend_test(series)
if trend is None:
    st.info("Not enough results yet to test for a statistically real trend.")
else:
    if trend["significant"]:
        st.warning(
            f"Statistically significant {trend['direction']} trend (p={trend['p_value']:.4f}, "
            f"R²={trend['r_squared']:.2f}): {trend['slope_per_run']:+.4g} per run, across "
            f"{trend['n']} runs."
        )
    else:
        st.success(
            f"No statistically significant trend (p={trend['p_value']:.4f}) - the apparent "
            f"{trend['direction']} movement across {trend['n']} runs is not distinguishable from noise."
        )

# ---------------------------------------------------------------------------
# What else changed - recipe/machine switches and quality issues on the same timeline
# ---------------------------------------------------------------------------
st.divider()
st.subheader("What else changed on this timeline")
change_rows = []
prev_recipe, prev_machine = None, None
for _, row in series.iterrows():
    if prev_recipe is not None and row["recipe_version"] != prev_recipe:
        change_rows.append(
            {"Date": row["tested_at"], "Run ID": row["run_id"], "Change": f"Recipe version: {prev_recipe} -> {row['recipe_version']}"}
        )
    if prev_machine is not None and row["machine"] != prev_machine:
        change_rows.append(
            {"Date": row["tested_at"], "Run ID": row["run_id"], "Change": f"Machine: {prev_machine} -> {row['machine']}"}
        )
    prev_recipe, prev_machine = row["recipe_version"], row["machine"]

run_ids = [int(r) for r in series["run_id"].tolist()]
quality_issues = (
    session.query(QualityObservation)
    .filter(QualityObservation.production_run_id.in_(run_ids))
    .order_by(QualityObservation.observed_at)
    .all()
) if run_ids else []
for qi in quality_issues:
    change_rows.append(
        {
            "Date": qi.observed_at,
            "Run ID": qi.production_run_id,
            "Change": f"Quality issue: {qi.observation_type} ({qi.severity or 'severity n/a'})",
        }
    )

if change_rows:
    change_df = pd.DataFrame(change_rows).sort_values("Date")
    st.dataframe(change_df, hide_index=True, use_container_width=True)
    st.caption("Cross-reference these dates against any control-chart flag, CUSUM breach, or trend above.")
else:
    st.caption("No recipe-version changes, machine changes, or quality issues recorded across these runs.")

# ---------------------------------------------------------------------------
# Raw results
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Results")
st.dataframe(
    series[["tested_at", "run_id", "recipe_version", "machine", "actual_value", "target_value", "n_replicates"]],
    hide_index=True,
    use_container_width=True,
)

# ---------------------------------------------------------------------------
# PI3 interpretation, grounded in the SPC results above
# ---------------------------------------------------------------------------
plant_id = grade.product_family.plant_id if grade.product_family else None
if ai_assistant.is_enabled_for_plant(session, plant_id):
    st.divider()
    st.subheader("Ask PI3 to interpret this pattern")
    if st.button("Get PI3 interpretation", key=f"ask_pi3_trend_{grade.id}_{property_name}"):
        if chart_result["ready"]:
            if chart_result["in_control"]:
                control_summary = "In control - no control-chart rule violations."
            else:
                control_summary = "Rule violations found:\n" + "\n".join(
                    f"- {f['rule']}: first seen at run {f['first_run_id']} ({f['first_tested_at']}), "
                    f"{f['points_matching']} point(s) matching"
                    for f in chart_result["flags"]
                )
        else:
            control_summary = f"Not enough results yet ({chart_result['n']}) for a control chart."

        if capability is not None:
            capability_summary = (
                f"Cpk {capability['cpk']:.2f} (Cpu {capability['cpu']:.2f}, Cpl {capability['cpl']:.2f}) "
                f"against tolerance band {capability['lsl']:.3g}-{capability['usl']:.3g}."
            )
        else:
            capability_summary = "Not enough data for a capability index."

        if cusum is not None:
            cusum_summary = (
                "No sustained drift detected."
                if cusum["breach_index"] is None
                else f"Sustained {cusum['breach_direction']} drift, first crossing the decision limit at "
                f"run {cusum['breach_run_id']} ({cusum['breach_tested_at']})."
            )
        else:
            cusum_summary = "Not enough data for a CUSUM check."

        if trend is not None:
            trend_summary = (
                f"{'Statistically significant' if trend['significant'] else 'Not statistically significant'} "
                f"{trend['direction']} trend, p={trend['p_value']:.4f}, R²={trend['r_squared']:.2f}, "
                f"slope {trend['slope_per_run']:+.4g} per run."
            )
        else:
            trend_summary = "Not enough data for a trend test."

        changes_summary = (
            "\n".join(f"- {r['Date']}: {r['Change']}" for r in change_rows) if change_rows
            else "No recipe/machine changes or quality issues recorded across these runs."
        )

        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam manufacturer interpret "
            f"a statistical process control analysis of {property_name} for foam grade {grade.grade_name} "
            f"across {len(series)} production runs. All of the following was computed deterministically "
            "(control chart with control limits and Western Electric/Nelson run rules, process capability, "
            "CUSUM drift detection, and a linear-regression trend test) - your job is to interpret it, not "
            "to re-derive it.\n\n"
            f"Control chart: {control_summary}\n\n"
            f"Process capability: {capability_summary}\n\n"
            f"CUSUM (slow sustained drift): {cusum_summary}\n\n"
            f"Trend test: {trend_summary}\n\n"
            f"Other changes on the same timeline (recipe version, machine, quality issues):\n{changes_summary}\n\n"
            "Using this, plus any relevant expert notes or historical cases in the connected knowledge "
            "base, explain in plain language whether this property shows a real problem, what it might be "
            "connected to (cross-reference the dates above), and what the reviewer should look into next. "
            "This is a historical pattern for the reviewer's own investigation, not a directive - phrase it "
            "as observations and hypotheses, not instructions to change anything.\n\n"
            "Do not use statistical or technical jargon in your explanation - not Cpk, Cpu, Cpl, CUSUM, "
            "p-value, R-squared, sigma, control limit, moving range, or similar terms, even though they "
            "appear in the data above. Translate every finding into plain operational language a "
            "foam-plant technician without a statistics background would understand. For example: "
            "instead of \"Cpk 0.87\", say the process is running close to the edge of spec; instead of "
            "a CUSUM breach, say a slow drift has been building up since a certain point; instead of a "
            "p-value, say plainly whether the pattern looks like a real, sustained trend or just normal "
            "run-to-run variation. Dates, quantities, and other concrete facts are fine to state - it's "
            "the statistical vocabulary that should disappear, not the underlying facts."
        )
        with st.spinner("Using PI3..."):
            answer = ai_assistant.ask_assistant(prompt)
        if answer:
            st.session_state[f"trend_ai_answer_{grade.id}_{property_name}"] = answer

    ai_answer = st.session_state.get(f"trend_ai_answer_{grade.id}_{property_name}")
    if ai_answer:
        st.subheader("\U0001F916 PI3 interpretation")
        st.caption(
            "Generated by PI3 from the control chart, capability, CUSUM, and trend-test results above, "
            "plus expert notes and historical cases. Confirm through your own investigation before "
            "acting on it."
        )
        st.write(ai_answer)
elif ai_assistant.availability_status(session, plant_id) == "not_configured":
    st.caption(
        "PI3 isn't configured for this deployment yet (missing API credentials) - contact "
        "your administrator."
    )
else:
    st.caption(
        "Enable PI3 connectivity for this plant (PI3 Connectivity, in Admin) to get PI3's "
        "interpretation here."
    )

st.divider()
render_ask_pi3_section(
    session,
    plant_id,
    default_foam_grade_id=grade.id,
    page_context=(
        f"The reviewer is on the Trend Analysis page, looking at '{property_name}' for foam "
        f"grade '{grade.grade_name}' (id {grade.id})."
    ),
    sample_questions=[
        f"Is there a real trend in {property_name} for {grade.grade_name}, or is it just noise?",
        f"Has {property_name} for {grade.grade_name} ever gone out of control before?",
        f"What changed around the time {property_name} started drifting for {grade.grade_name}?",
    ],
    key_prefix=f"ask_pi3_freeform_trend_{grade.id}_{property_name}",
)
