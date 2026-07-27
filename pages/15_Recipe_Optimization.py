"""Industrial Intelligence: Recipe Optimization

Recipe optimization means answering three questions a raw ingredient list
and a results table can't answer by themselves: what does this formulation
actually cost, what specifically changed between two versions, and which
ingredient's dosage is actually associated with the property outcome -
ranked and quantified, not eyeballed. PI3's recommendation is grounded in
those three answers rather than a plain text dump of ingredients and
averages (see the advisory boundary at the bottom of this page).
"""

import pandas as pd
import streamlit as st

import ai_assistant
from analytics import (
    pass_rate,
    property_results_dataframe,
    rank_component_actual_correlations,
    rank_component_correlations,
    recipe_version_cost,
    recipe_version_diff,
)
from auth import logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import page_setup, render_ask_pi3_section, render_pi3_docx_download

page_setup("Recipe Optimization")
init_db()
require_login()
logout_button()

st.title("Recipe Optimization")
st.caption(
    "Formulation cost, version-to-version differences, and which raw material's dosage is "
    "actually associated with each quality outcome - ranked and quantified, alongside the "
    "usual property-outcome comparison across recipe versions."
)
session = get_session()

grades = session.query(FoamGrade).all()
if not grades:
    st.warning("Add a foam grade and at least one recipe version first.")
    st.stop()

grade = st.selectbox("Foam grade", grades, format_func=lambda g: g.grade_name)
versions = sorted(grade.recipe_versions, key=lambda v: v.created_at)

if not versions:
    st.info("This foam grade has no recipe versions yet.")
    st.stop()

results_df = property_results_dataframe(session, foam_grade_id=grade.id)
available_properties = sorted(results_df["property_name"].dropna().unique()) if not results_df.empty else []

# Per-property summary tables, kept keyed by property name so the PI3
# recommendation prompt below can reuse them instead of recomputing.
property_summaries = {}

if results_df.empty:
    st.info("No quality test results recorded yet for this foam grade's production runs.")
else:
    st.subheader("Property outcomes by recipe version")
    for prop in available_properties:
        sub = results_df[results_df["property_name"] == prop]
        summary = (
            sub.groupby("recipe_version")
            .agg(
                avg_actual=("actual_value", "mean"),
                avg_target=("target_value", "mean"),
                results=("result_id", "count"),
                pass_rate=("pass_fail", pass_rate),
            )
            .reset_index()
        )
        summary["avg_actual"] = summary["avg_actual"].round(2)
        summary["avg_target"] = summary["avg_target"].round(2)
        property_summaries[prop] = summary
        with st.container(border=True):
            st.markdown(f"**{prop}**")
            st.dataframe(summary, hide_index=True, use_container_width=True)
            best = summary.dropna(subset=["pass_rate"]).sort_values("pass_rate", ascending=False)
            if not best.empty:
                st.caption(
                    f"Highest pass rate for {prop}: recipe {best.iloc[0]['recipe_version']} "
                    f"({best.iloc[0]['pass_rate']:.0%}, n={int(best.iloc[0]['results'])}). "
                    "Review against current raw materials and process conditions before reusing."
                )

# ---------------------------------------------------------------------------
# Formulation cost by version
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Formulation cost by version")
cost_by_version = {v.id: recipe_version_cost(session, v) for v in versions}
cost_rows = []
for v in versions:
    c = cost_by_version[v.id]
    coverage_pct = round((c["priced_php"] / c["total_php"]) * 100, 0) if c["total_php"] else None
    cost_rows.append(
        {
            "Version": v.version_label,
            "Status": v.approval_status,
            "Cost per 100 parts": c["total_cost"],
            "Cost coverage": f"{coverage_pct:.0f}%" if coverage_pct is not None else "—",
            "Materials missing cost": ", ".join(c["missing"]) if c["missing"] else "—",
        }
    )
cost_df = pd.DataFrame(cost_rows)
st.dataframe(cost_df, hide_index=True, use_container_width=True)
if any(c["missing"] for c in cost_by_version.values()):
    st.caption(
        "Costs shown are a lower-bound estimate where materials are missing a recorded cost/kg - "
        "add pricing on the Raw Materials page to complete these totals. Nothing here is invented."
    )

# ---------------------------------------------------------------------------
# Version-to-version diff
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Compare two versions")
st.caption("What specifically changed in the formulation between two recipe versions.")

diff_col1, diff_col2 = st.columns(2)
version_a = diff_col1.selectbox(
    "Version A",
    versions,
    index=max(len(versions) - 2, 0),
    format_func=lambda v: v.version_label,
    key=f"diff_a_{grade.id}",
)
version_b = diff_col2.selectbox(
    "Version B",
    versions,
    index=len(versions) - 1,
    format_func=lambda v: v.version_label,
    key=f"diff_b_{grade.id}",
)

if version_a.id == version_b.id:
    st.info("Choose two different versions to compare.")
else:
    diff_df = recipe_version_diff(version_a, version_b)
    if diff_df.empty:
        st.caption("Neither version has any components recorded.")
    else:
        show_unchanged = st.checkbox(
            "Show unchanged materials", value=False, key=f"diff_show_unchanged_{grade.id}"
        )
        display_diff = diff_df if show_unchanged else diff_df[diff_df["status"] != "Unchanged"]
        st.dataframe(
            display_diff.rename(
                columns={
                    "raw_material_name": "Raw material",
                    "role": "Role",
                    "php_a": f"php ({version_a.version_label})",
                    "php_b": f"php ({version_b.version_label})",
                    "delta": "Change (php)",
                    "delta_pct": "Change (%)",
                    "status": "Status",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )
        changed_count = (diff_df["status"] != "Unchanged").sum()
        st.caption(
            f"{changed_count} of {len(diff_df)} materials differ between {version_a.version_label} "
            f"and {version_b.version_label}."
        )

# ---------------------------------------------------------------------------
# Which ingredient actually drives the outcome
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Which ingredient drives each outcome")
st.caption(
    "Two different questions, both worth asking: does this run's ACTUAL metered dosage of a "
    "material line up with this run's actual outcome (the batch-to-batch metering variance a "
    "settled recipe will always have), and separately, does changing the PLANNED recipe from "
    "one version to the next line up with a shift in outcome. The first uses flow-meter "
    "readings per production run; the second uses the target php recorded on each recipe "
    "version - they can and often will point to different answers."
)
if not available_properties:
    st.info("No quality test results recorded yet - nothing to correlate ingredient dosage against.")
else:
    corr_property = st.selectbox(
        "Property", available_properties, key=f"corr_property_{grade.id}"
    )

    st.markdown("**By actual metered dosage (per production run)**")
    actual_ranked = rank_component_actual_correlations(session, grade.id, corr_property)
    if actual_ranked.empty:
        st.info(
            f"No raw-material stream has metered readings paired with {corr_property} results "
            "across at least 3 production runs yet - import Component Stream Readings for the "
            "Finalized phase of more runs to unlock this."
        )
    else:
        st.dataframe(
            actual_ranked.rename(
                columns={
                    "raw_material_name": "Raw material",
                    "n_runs": "Runs compared",
                    "correlation": "Correlation with outcome",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )
        top_actual = actual_ranked.iloc[0]
        st.caption(
            f"Strongest association for {corr_property}: **{top_actual['raw_material_name']}** "
            f"(correlation {top_actual['correlation']:+.3f} across "
            f"{int(top_actual['n_runs'])} production runs' metered dosage). Review applicability "
            "against current raw materials and process conditions before adjusting dosage."
        )

    st.markdown("**By planned recipe version**")
    if len(versions) < 3:
        st.info(
            f"This foam grade currently has {len(versions)} recipe version(s). Correlating the "
            "PLANNED php against outcomes needs at least 3 versions with varying php and "
            "recorded results - not enough version history yet to say whether changing the "
            "target formulation matters."
        )
    else:
        component_ranked = rank_component_correlations(session, grade.id, corr_property)
        if component_ranked.empty:
            st.info(
                f"No raw material appears (with a recorded php) across enough versions with "
                f"{corr_property} results to compute a correlation yet."
            )
        else:
            st.dataframe(
                component_ranked.rename(
                    columns={
                        "raw_material_name": "Raw material",
                        "n_versions": "Versions compared",
                        "correlation": "Correlation with outcome",
                    }
                ),
                hide_index=True,
                use_container_width=True,
            )
            top_component = component_ranked.iloc[0]
            st.caption(
                f"Strongest association for {corr_property}: **{top_component['raw_material_name']}** "
                f"(correlation {top_component['correlation']:+.3f} across "
                f"{int(top_component['n_versions'])} versions). Review applicability against current "
                "raw materials and process conditions before adjusting dosage."
            )

# ---------------------------------------------------------------------------
# Recipes (version controlled) - raw ingredient list per version
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Recipes (version controlled)")
st.caption("The raw materials, dosage (php), and role recorded for each recipe version.")
for v in versions:
    with st.expander(f"{v.version_label} — {v.approval_status} — {v.change_note or ''}"):
        if v.components:
            st.dataframe(
                [
                    {
                        "Raw material": c.raw_material_name,
                        "Supplier": c.supplier,
                        "php": c.php,
                        "Role": c.role_in_formulation,
                    }
                    for c in v.components
                ],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("No components recorded for this version yet.")

# ---------------------------------------------------------------------------
# PI3 recommendation, grounded in cost / diff / correlation data above
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Ask PI3 for a formulation recommendation")

plant_id = grade.product_family.plant_id if grade.product_family else None

if ai_assistant.is_enabled_for_plant(session, plant_id):
    st.caption(
        "PI3 reviews this foam grade's formulation cost, version-to-version differences, "
        "ingredient-outcome correlations, and quality-test history against the target "
        "properties below, and proposes a formulation for your technical team to evaluate and "
        "confirm. Prefilled from this foam grade's stored specification - add any other targets "
        "(resilience, tensile strength, ...) before asking."
    )
    default_targets = []
    if grade.target_density is not None:
        default_targets.append(f"Density {grade.target_density:g} kg/m3")
    if grade.target_hardness is not None:
        default_targets.append(f"Hardness {grade.target_hardness:g} (unit per test method)")
    if grade.quality_specification:
        default_targets.append(grade.quality_specification.strip())

    target_properties = st.text_area(
        "Target properties",
        value="\n".join(default_targets),
        placeholder=(
            "e.g. Density 28 kg/m3, Hardness (CLD 40%) 3.5-4.0 kPa, Resilience > 55%, "
            "Tensile strength > 100 kPa"
        ),
        key=f"recipe_opt_targets_{grade.id}",
    )
    if st.button(
        "Get PI3 recommendation",
        key=f"ask_pi3_recipe_opt_{grade.id}",
        disabled=not target_properties.strip(),
    ):
        composition_lines = [
            f"Version {v.version_label} ({v.approval_status}): "
            + ", ".join(f"{c.raw_material_name} {c.php} php ({c.role_in_formulation})" for c in v.components)
            for v in versions
            if v.components
        ]
        composition_summary = "\n".join(composition_lines) or "No formulation data recorded for any version."

        cost_lines = []
        for v in versions:
            c = cost_by_version[v.id]
            if c["total_cost"] is not None:
                note = "" if c["complete"] else f" (partial - missing cost for {', '.join(c['missing'])})"
                cost_lines.append(f"Version {v.version_label}: {c['total_cost']:.3f} per 100 parts{note}")
            else:
                cost_lines.append(f"Version {v.version_label}: no cost data recorded")
        cost_summary = "\n".join(cost_lines)

        diff_summary = "No version comparison available (fewer than 2 recipe versions)."
        if len(versions) >= 2:
            latest, previous = versions[-1], versions[-2]
            latest_diff = recipe_version_diff(previous, latest)
            changed = latest_diff[latest_diff["status"] != "Unchanged"]
            if changed.empty:
                diff_summary = f"No formulation change between {previous.version_label} and {latest.version_label}."
            else:
                diff_lines = [
                    f"{row['raw_material_name']}: {row['status']} "
                    f"({row['php_a']} -> {row['php_b']} php)"
                    for _, row in changed.iterrows()
                ]
                diff_summary = (
                    f"Changes from {previous.version_label} to {latest.version_label} (latest):\n"
                    + "\n".join(diff_lines)
                )

        actual_correlation_lines = []
        for prop in available_properties:
            ranked = rank_component_actual_correlations(session, grade.id, prop)
            if ranked.empty:
                continue
            top3 = ranked.head(3)
            actual_correlation_lines.append(
                f"{prop}: "
                + "; ".join(
                    f"{r['raw_material_name']} (r={r['correlation']:+.3f}, n={int(r['n_runs'])} runs)"
                    for _, r in top3.iterrows()
                )
            )
        actual_correlation_summary = (
            "\n".join(actual_correlation_lines)
            if actual_correlation_lines
            else "Not enough metered stream-reading data paired with quality results yet to correlate "
            "actual per-run dosage with outcomes."
        )

        planned_correlation_lines = []
        if len(versions) >= 3:
            for prop in available_properties:
                ranked = rank_component_correlations(session, grade.id, prop)
                if ranked.empty:
                    continue
                top3 = ranked.head(3)
                planned_correlation_lines.append(
                    f"{prop}: "
                    + "; ".join(
                        f"{r['raw_material_name']} (r={r['correlation']:+.3f}, n={int(r['n_versions'])} versions)"
                        for _, r in top3.iterrows()
                    )
                )
        planned_correlation_summary = (
            "\n".join(planned_correlation_lines)
            if planned_correlation_lines
            else "Not enough recipe version history yet to correlate the planned formulation with outcomes."
        )

        outcome_lines = []
        for prop, summary in property_summaries.items():
            for _, row in summary.iterrows():
                pass_rate_value = row["pass_rate"]
                pass_rate_text = f"{pass_rate_value:.0%}" if pd.notna(pass_rate_value) else "—"
                outcome_lines.append(
                    f"{prop} — version {row['recipe_version']}: avg actual {row['avg_actual']}, "
                    f"avg target {row['avg_target']}, pass rate {pass_rate_text}, "
                    f"n={int(row['results'])}"
                )
        outcome_summary = "\n".join(outcome_lines) or "No quality test results recorded yet."

        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam manufacturer "
            f"select a formulation direction for {grade.grade_name}. Below is this foam grade's "
            "recipe version history: formulation composition, formulation cost, the most recent "
            "version-to-version change, which ingredient's ACTUAL metered per-run dosage is "
            "statistically associated with each quality outcome, which ingredient's PLANNED "
            "recipe-version php is separately associated with each outcome, and quality test "
            "outcomes by version. The actual-dosage correlations reflect real batch-to-batch "
            "metering variance under the current recipe and are the stronger signal for "
            "day-to-day process guidance; the planned-version correlations only speak to what "
            "happens when the target formulation itself changes. Use this quantified data - not "
            "just the ingredient list - as the basis of your reasoning, plus any relevant expert "
            "notes or historical cases in the connected knowledge base, to propose a formulation "
            "that could meet the target properties given.\n\n"
            "Phrase this as a recommendation for the reviewer to evaluate and confirm through "
            "their own trial process, addressed directly to the target properties requested. "
            "Where you rely on a specific cost, diff, or correlation figure below, refer to it "
            "explicitly rather than restating the raw ingredient list.\n\n"
            f"Foam grade: {grade.grade_name}\n\n"
            f"Recipe versions and composition:\n{composition_summary}\n\n"
            f"Formulation cost by version:\n{cost_summary}\n\n"
            f"Most recent formulation change:\n{diff_summary}\n\n"
            f"Actual metered dosage vs. outcome correlations (top 3 per property, per production "
            f"run):\n{actual_correlation_summary}\n\n"
            f"Planned recipe-version php vs. outcome correlations (top 3 per property, where "
            f"enough version history exists):\n{planned_correlation_summary}\n\n"
            f"Quality test outcomes by version:\n{outcome_summary}\n\n"
            f"Target properties requested:\n{target_properties.strip()}\n"
        )
        with st.spinner("Using PI3..."):
            answer = ai_assistant.ask_assistant(prompt)
        if answer:
            st.session_state[f"recipe_opt_ai_answer_{grade.id}"] = answer

    ai_answer = st.session_state.get(f"recipe_opt_ai_answer_{grade.id}")
    if ai_answer:
        st.subheader("🤖 PI3 recommendation")
        st.caption(
            "Generated by PI3 from this foam grade's formulation cost, version differences, "
            "ingredient-outcome correlations, and quality-test history, plus expert notes and "
            "historical cases. For your technical team to evaluate and confirm before applying."
        )
        st.write(ai_answer)
        render_pi3_docx_download(
            session,
            plant_id,
            key_prefix=f"recipe_opt_fixed_{grade.id}",
            question_label=f"PI3 formulation recommendation for {grade.grade_name}",
            answer=ai_answer,
            foam_grade_id=grade.id,
        )
elif ai_assistant.availability_status(session, plant_id) == "not_configured":
    st.caption(
        "PI3 isn't configured for this deployment yet (missing API credentials) - contact "
        "your administrator."
    )
else:
    st.caption(
        "Enable PI3 connectivity for this plant (PI3 Connectivity, in Admin) to get a "
        "formulation recommendation here."
    )

st.divider()
render_ask_pi3_section(
    session,
    plant_id,
    default_foam_grade_id=grade.id,
    page_context=(
        f"The reviewer is on the Recipe Optimization page, looking at foam grade "
        f"'{grade.grade_name}' (id {grade.id})."
    ),
    sample_questions=[
        f"What does {grade.grade_name}'s current recipe cost per 100 parts?",
        f"Which ingredient's actual dosage correlates most with density for {grade.grade_name}?",
        f"What changed between the last two recipe versions of {grade.grade_name}?",
        f"Have there been any quality issues reported for {grade.grade_name} recently?",
    ],
    key_prefix=f"ask_pi3_freeform_recipe_{grade.id}",
)
