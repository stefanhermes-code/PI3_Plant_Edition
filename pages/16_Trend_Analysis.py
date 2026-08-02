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

import altair as alt
import pandas as pd
import streamlit as st

import ai_assistant
from access_control import can_use_page
from analytics import (
    capability_analysis,
    control_chart_analysis,
    cusum_analysis,
    property_results_dataframe,
    property_run_series,
    trend_test,
)
from auth import current_user, logout_button, require_login
from db import FoamGrade, QualityObservation, get_session, init_db
from tenant_scope import apply_scope, company_picker, grade_ids_for_company
from helpers import (
    CHART_ZOOM_HINT,
    analysis_unit_picker,
    page_setup,
    render_ask_pi3_section,
    render_data_table,
    render_function_action_intro,
    render_pi3_docx_download,
    render_save_to_expert_notes_button,
    view_only_notice,
)

page_setup("Trend Analysis")
init_db()
require_login()
logout_button()

st.title("Trend Analysis")
render_function_action_intro(
    function_text=(
        "Runs the standard SPC toolkit against one quality property's history for a foam grade (or "
        "a whole foam family pooled together): an individuals control chart with real control "
        "limits (catches a sudden shift), process capability (Cpk) against that property's own "
        "tolerance band (catches 'in control but too close to spec'), a CUSUM chart (catches a "
        "slow drift a control chart is bad at catching early - pump wear, catalyst degradation, an "
        "off-spec raw-material lot), and a formal trend test (replaces an eyeballed "
        "first-half-vs-second-half comparison with an actual significance test). PI3 is used only "
        "after these numbers exist, to help interpret a real flag against recipe changes, machine "
        "changes, and quality-issue history - never to guess whether a trend exists in the first "
        "place."
    ),
    action_text=(
        "Choose whether to analyze one foam grade or a whole foam family (its grades pooled "
        "together), pick the property you want to track (density, hardness/IFD, tensile, and so "
        "on). Read the control chart first for sudden shifts, then capability for how much margin "
        "there is to spec, then CUSUM for a slower drift the control chart might miss, and the "
        "trend test to confirm whether an apparent trend is statistically real. If something "
        "flags, use 'Ask PI3' to get it interpreted against recipe changes, machine changes, and "
        "quality-issue history before acting on it."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("trend_analysis", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice(action="using PI3 and saving to Expert Notes")
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="trend_company_filter"
)
active_company_id = company.id if company else None
scoped_grade_ids = grade_ids_for_company(session, active_company_id)

# Plain-language description for each control-chart rule name from
# analytics.control_chart_analysis() - the analysis itself keeps its
# precise statistical rule names (used elsewhere, e.g. the PI3 prompt
# below), but the on-screen table shouldn't require knowing what "3-sigma"
# or "2-of-3" mean, so this translates purely for display.
RULE_PLAIN_LABELS = {
    "Beyond 3-sigma control limit": "A single result landed well outside the normal run-to-run range",
    "Sustained shift (8+ consecutive points on one side)": (
        "8 or more runs in a row landed on the same side of the average - a real, sustained shift"
    ),
    "Sustained drift (6+ consecutive points trending)": (
        "6 or more runs in a row have been steadily rising or falling"
    ),
    "2-of-3 beyond 2-sigma warning line": (
        "Several recent results have been unusually far from the average, close together"
    ),
}


def _line_chart_no_zero(df, value_cols):
    """Same idea as st.line_chart(df[value_cols]), but without forcing the
    Y-axis down to zero. These properties normally sit in a narrow band
    (e.g. density around 30) with control limits only a little above and
    below - a zero-anchored axis squeezes all of that real variation into
    a thin sliver at the top of the chart, making it hard to actually see
    whether a line is moving. df's index is used as the X-axis (must be
    named or reset first). .interactive() adds the same scroll-to-zoom /
    click-drag-to-pan behaviour as the native st.line_chart, and the
    caption below the chart calls that out since it isn't otherwise
    obvious."""
    long_df = df.reset_index().melt(
        id_vars=df.index.name or "index", value_vars=value_cols, var_name="series", value_name="value"
    )
    chart = (
        alt.Chart(long_df)
        .mark_line()
        .encode(
            x=alt.X(f"{df.index.name or 'index'}:T", title=None),
            y=alt.Y("value:Q", title=None, scale=alt.Scale(zero=False)),
            color=alt.Color("series:N", title=None),
        )
        .interactive()
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption(CHART_ZOOM_HINT)


# Only offer a grade here if it actually has quality test results to trend -
# otherwise picking it just leads to a dead-end message (see Recipe
# Optimization's identical filter).
grades = [
    g for g in apply_scope(session.query(FoamGrade), FoamGrade.id, scoped_grade_ids).all()
    if not property_results_dataframe(session, foam_grade_id=g.id).empty
]
if not grades:
    st.warning("No foam grade yet has quality test results recorded - add these first before using Trend Analysis.")
    st.stop()

unit = analysis_unit_picker(grades, key_prefix="trend")
results_df = property_results_dataframe(session, foam_grade_id=unit["grade_ids"])

properties = sorted(results_df["property_name"].dropna().unique())
property_name = st.selectbox("Property", properties)

c1, c2 = st.columns(2)
recipe_versions = sorted(results_df["recipe_version"].dropna().unique())
recipe_filter = c1.selectbox("Recipe version filter", ["All"] + list(recipe_versions))
# Only offer a machine filter when this selection's runs actually span more
# than one machine - with a single machine (today's actual production
# state), "Machine filter: All" vs "Machine filter: <the only machine>"
# is the same noise problem the Company selector had: nothing to choose
# between, identical result set either way.
machines = sorted(m for m in results_df["machine"].dropna().unique())
if len(machines) > 1:
    machine_filter = c2.selectbox("Machine filter", ["All"] + list(machines))
else:
    machine_filter = "All"

pooling_grades = unit["mode"] == "family"
if pooling_grades:
    st.caption(
        f"Pooling {len(unit['grade_ids'])} grade(s) in foam family **{unit['label']}**: "
        f"{', '.join(unit['member_grade_names'])}. Because grades in a family can have different "
        f"target values for the same property, everything below is shown as **% of each run's own "
        f"target** (100% = exactly on target) instead of {property_name}'s raw unit - this is what "
        "keeps pooling grades together from reading a plain grade-to-grade target difference as a "
        "false shift, drift, or trend."
    )

series = property_run_series(session, unit["grade_ids"], property_name, normalize_pct_of_target=pooling_grades)
if recipe_filter != "All":
    series = series[series["recipe_version"] == recipe_filter]
if machine_filter != "All":
    series = series[series["machine"] == machine_filter]
series = series.reset_index(drop=True)

if series.empty:
    st.info("No results match these filters.")
    st.stop()

value_desc = f"{property_name} result" if not pooling_grades else f"{property_name} result (as % of target)"
st.caption(f"{len(series)} production run(s) with a {value_desc}, {series['tested_at'].min()} to {series['tested_at'].max()}.")

# ---------------------------------------------------------------------------
# Control chart
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Sudden changes check")
chart_result = control_chart_analysis(series)

if not chart_result["ready"]:
    st.info(
        f"Only {chart_result['n']} result(s) so far - checking for sudden changes needs at least 5 "
        "results to know what 'normal' looks like for this property. Add more production runs with "
        "this property tested to unlock this."
    )
else:
    chart_df = chart_result["chart_df"].set_index("tested_at")[
        ["actual_value", "center_line", "ucl", "lcl"]
    ]
    _line_chart_no_zero(chart_df, ["actual_value", "center_line", "ucl", "lcl"])
    st.caption(
        f"Based on how much this property normally varies run-to-run, results are expected to fall "
        f"between {chart_result['lcl']:.3g} and {chart_result['ucl']:.3g}, centered around "
        f"{chart_result['mean']:.3g}."
    )
    if chart_result["in_control"]:
        st.success("No unusual patterns found - this property has been behaving consistently across these runs.")
    else:
        st.warning(f"{len(chart_result['flags'])} unusual pattern(s) found:")
        flags_display = pd.DataFrame(
            [
                {
                    "What was seen": RULE_PLAIN_LABELS.get(f["rule"], f["rule"]),
                    "First seen": f["first_tested_at"],
                    "Run ID": f["first_run_id"],
                    "Points matching": f["points_matching"],
                }
                for f in chart_result["flags"]
            ]
        )
        render_data_table(flags_display)

# ---------------------------------------------------------------------------
# Process capability
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Margin to spec")
capability = capability_analysis(series)
if capability is None:
    st.info(
        "Not enough results, or no consistent target value recorded, to check margin to spec yet."
    )
else:
    cpk = capability["cpk"]
    if cpk >= 1.33:
        capability_read = "comfortable margin to spec"
    elif cpk >= 1.0:
        capability_read = "tight - some results will likely fall outside spec"
    else:
        capability_read = "not enough margin - this process routinely produces results outside spec"
    m1, m2, m3 = st.columns(3)
    m1.metric("Overall margin to spec", f"{cpk:.2f}")
    m2.metric("Margin to upper limit", f"{capability['cpu']:.2f}")
    m3.metric("Margin to lower limit", f"{capability['cpl']:.2f}")
    spec_unit = "% of target" if pooling_grades else ""
    st.caption(
        f"Spec range is {capability['lsl']:.3g}-{capability['usl']:.3g}{spec_unit} (target "
        f"{capability['target']:.3g}{spec_unit} +/-10%, this app's own pass/fail convention): "
        f"**{capability_read}**. As a guide: a margin score of 1.33 or higher means comfortable "
        "room to spec, 1.0-1.33 is tight (some risk of results drifting outside spec), and below "
        "1.0 means the process is likely already producing some out-of-spec results even without "
        "any further drift."
    )

# ---------------------------------------------------------------------------
# CUSUM - slow sustained drift
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Slow drift check")
cusum = cusum_analysis(series)
if cusum is None:
    st.info("Not enough results yet (need at least 8) to check for a slow drift.")
else:
    cusum_df = cusum["chart_df"].copy()
    cusum_df["upper_limit"] = cusum["h"]
    cusum_df["lower_limit"] = -cusum["h"]
    _line_chart_no_zero(
        cusum_df.set_index("tested_at")[["cusum_positive", "cusum_negative", "upper_limit", "lower_limit"]],
        ["cusum_positive", "cusum_negative", "upper_limit", "lower_limit"],
    )
    st.caption(
        f"Compares each run against the target value of {cusum['reference']:.3g}, adding up small "
        "persistent differences over time - this catches a slow drift building up long before a "
        "single result would look unusual on its own."
    )
    if cusum["breach_index"] is None:
        st.success("No slow drift detected - results have stayed close to target over time.")
    else:
        st.warning(
            f"A slow {cusum['breach_direction']} drift has been building up, first becoming clear "
            f"at run {cusum['breach_run_id']} ({cusum['breach_tested_at']})."
        )

# ---------------------------------------------------------------------------
# Trend test
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Is this a real trend?")
trend = trend_test(series)
if trend is None:
    st.info("Not enough results yet to tell whether this is a real trend or just noise.")
else:
    noise_chance_pct = trend["p_value"] * 100
    fit_pct = trend["r_squared"] * 100
    if trend["significant"]:
        st.info(
            f"Yes - this is a real, sustained {trend['direction']} trend, not just noise: changing "
            f"by about {trend['slope_per_run']:+.4g} per run on average, across {trend['n']} runs."
        )
    else:
        st.success(
            f"No - the apparent {trend['direction']} movement across {trend['n']} runs looks like "
            "normal run-to-run variation, not a real trend."
        )
    # Plain-language breakdown of the actual criteria and numbers behind the
    # verdict above, not just the yes/no - a straight line is fit through
    # these results in production order, and it's only called a real trend
    # when there's less than a 5% chance that line is just random noise
    # (the conventional p<0.05 threshold - see trend_test() in analytics.py).
    # This is deliberately the strictest of the four checks on this page:
    # the control chart and slow-drift check above are tuned to catch a
    # real problem early even from a handful of points, while this test's
    # job is the final, rigorous confirmation - so "No" is the common,
    # correct answer for anything short of a clean, sustained signal, and
    # becomes easier to confirm as more results accumulate or the
    # underlying noise is lower.
    st.caption(
        f"How this is decided: a straight line is fit through these {trend['n']} results in "
        f"production order. It's only called a real trend when there's less than a 5% chance "
        f"that a line this steep would appear just from random run-to-run noise - here, that "
        f"chance is {noise_chance_pct:.3g}%. That line accounts for {fit_pct:.3g}% of the "
        "run-to-run variation seen in these results (a low number means the results bounce "
        "around too much for a straight trend line to be a good description at all, regardless "
        "of direction). Fewer results or noisier data both make the 5% bar harder to clear, "
        "which is why a real, gradual drift can take a while to get confirmed here - the control "
        "chart and slow-drift check above are tuned to flag it earlier; this test is the final, "
        "stricter word on whether it's statistically real."
    )

# ---------------------------------------------------------------------------
# What else changed - machine switches and quality issues on the same timeline
#
# Recipe-version changes are deliberately NOT included here (removed
# 2026-08-02, per explicit request) - a recipe version replaces the
# previous one rather than coexisting with it (see Recipe Optimization's
# "current formulation" framing app-wide), so a version switch on this
# grade's timeline isn't a comparable "what else changed" event the same
# way a machine change or a quality issue is.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("What else changed on this timeline")
change_rows = []
prev_machine = None
for _, row in series.iterrows():
    if prev_machine is not None and row["machine"] != prev_machine:
        change_rows.append(
            {"Date": row["tested_at"], "Run ID": row["run_id"], "Change": f"Machine: {prev_machine} -> {row['machine']}"}
        )
    prev_machine = row["machine"]

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
    render_data_table(change_df)
    st.caption("Cross-reference these dates against any unusual pattern, slow drift, or trend flagged above.")
else:
    st.caption("No recipe-version changes, machine changes, or quality issues recorded across these runs.")

# ---------------------------------------------------------------------------
# Raw results
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Results")
results_columns = ["tested_at", "run_id", "machine", "actual_value", "target_value", "n_replicates"]
if pooling_grades:
    results_columns.insert(2, "foam_grade")
render_data_table(series[results_columns], max_height="400px")

# ---------------------------------------------------------------------------
# PI3 interpretation, grounded in the SPC results above
# ---------------------------------------------------------------------------
plant_id = unit["plant_id"]
subject_desc = (
    f"foam grade {unit['label']}" if unit["mode"] == "grade"
    else f"foam family {unit['label']} (pooling grades: {', '.join(unit['member_grade_names'])})"
)
docx_grade_id = unit["entity_id"] if unit["mode"] == "grade" else None
if ai_assistant.is_enabled_for_plant(session, plant_id):
    st.divider()
    st.subheader("Ask PI3 to interpret this pattern")
    if st.button(
        "Get PI3 interpretation",
        key=f"ask_pi3_trend_{unit['state_key']}_{property_name}",
        disabled=not page_usable,
    ):
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

        pooling_note = (
            ""
            if not pooling_grades
            else (
                "\n\nNote: this pools multiple foam grades from the same family together, and every "
                "number above is expressed as % of each run's own target (100% = on target), not the "
                "property's raw unit, precisely so grades with different target values can be pooled "
                "without a plain grade-to-grade target difference looking like a false shift or trend."
            )
        )
        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam manufacturer interpret "
            f"a statistical process control analysis of {property_name} for {subject_desc} "
            f"across {len(series)} production runs. All of the following was computed deterministically "
            "(control chart with control limits and Western Electric/Nelson run rules, process capability, "
            "CUSUM drift detection, and a linear-regression trend test) - your job is to interpret it, not "
            f"to re-derive it.{pooling_note}\n\n"
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
            answer = ai_assistant.ask_assistant(prompt, company_id=active_company_id)
        if answer:
            st.session_state[f"trend_ai_answer_{unit['state_key']}_{property_name}"] = answer
            st.session_state.pop(f"trend_fixed_{unit['state_key']}_{property_name}_saved_note_id", None)

    ai_answer = st.session_state.get(f"trend_ai_answer_{unit['state_key']}_{property_name}")
    if ai_answer:
        st.subheader("\U0001F916 PI3 interpretation")
        st.caption(
            "Generated by PI3 from the control chart, capability, CUSUM, and trend-test results above, "
            "plus expert notes and historical cases. Confirm through your own investigation before "
            "acting on it."
        )
        st.write(ai_answer)
        trend_question_label = f"PI3 interpretation of {property_name} for {subject_desc}"
        trend_dl_col, trend_save_col = st.columns([1, 1])
        with trend_dl_col:
            render_pi3_docx_download(
                session,
                plant_id,
                key_prefix=f"trend_fixed_{unit['state_key']}_{property_name}",
                question_label=trend_question_label,
                answer=ai_answer,
                foam_grade_id=docx_grade_id,
            )
        with trend_save_col:
            render_save_to_expert_notes_button(
                session,
                key_prefix=f"trend_fixed_{unit['state_key']}_{property_name}",
                answer=ai_answer,
                question_label=trend_question_label,
                link_type=unit["link_type"],
                entity_id=unit["entity_id"],
                disabled=not page_usable,
            )
elif user["is_platform_owner"]:
    # Only the platform owner sees why PI3 is unavailable here - a customer
    # whose subscription/plant simply doesn't have it enabled shouldn't be
    # shown a feature they don't know exists (see PI3 Gaps discussion,
    # 2026-08-01: "the customer does not know that they could have this
    # functionality").
    if ai_assistant.availability_status(session, plant_id) == "not_configured":
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
    default_foam_grade_id=docx_grade_id,
    page_context=(
        f"The reviewer is on the Trend Analysis page, looking at '{property_name}' for {subject_desc}."
    ),
    sample_questions=[
        f"Is there a real trend in {property_name} for {unit['label']}, or is it just noise?",
        f"Has {property_name} for {unit['label']} ever gone out of control before?",
        f"What changed around the time {property_name} started drifting for {unit['label']}?",
    ],
    note_link_type=unit["link_type"],
    note_entity_id=unit["entity_id"],
    key_prefix=f"ask_pi3_freeform_trend_{unit['state_key']}_{property_name}",
    disabled=not page_usable,
)
