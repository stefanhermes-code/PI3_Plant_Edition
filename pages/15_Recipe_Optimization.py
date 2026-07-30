"""Industrial Intelligence: Recipe Optimization

Recipe optimization means answering questions a raw ingredient list and a
results table can't answer by themselves: what does the current formulation
actually cost, and which ingredient's dosage is actually associated with
the property outcome - ranked and quantified, not eyeballed. PI3's
recommendation is grounded in those answers rather than a plain text dump
of ingredients and averages (see the advisory boundary at the bottom of
this page).

A recipe version replaces the previous one in production rather than
coexisting with it, so the page leads with the CURRENT version only.
Cost-by-version comparison, the version-diff tool, and older versions'
ingredient lists are all still available, just moved into "Version
history" at the bottom, since that's occasional-audit territory, not
day-to-day use.
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
from helpers import (
    page_setup,
    render_ask_pi3_section,
    render_data_table,
    render_function_action_intro,
    render_pi3_docx_download,
    render_save_to_expert_notes_button,
)

page_setup("Recipe Optimization")
init_db()
require_login()
logout_button()

st.title("Recipe Optimization")
render_function_action_intro(
    function_text=(
        "This page shows the formulation currently running in production for the selected foam "
        "grade: its full raw-material list with php dosage and role (base polyol, isocyanate, "
        "surfactant, catalyst, crosslinker, and so on), its cost per kg, and how its quality-test "
        "results compare to this grade's target density, hardness (IFD), tensile strength, "
        "elongation, compression set and resilience. It also ranks which raw material's dosage - "
        "either the actual metered flow-meter reading per production run, or the planned php on "
        "the recipe card - correlates most strongly with a chosen result, so you can see which "
        "ingredient is actually moving an outcome instead of reading it off the ingredient list. "
        "Older recipe versions, cost history, and a side-by-side version diff are kept under "
        "'Version history' at the bottom for audit purposes."
    ),
    action_text=(
        "Select the foam grade you want to review, then check the current formulation's cost per "
        "kg and ingredient list against the quality-outcome table below to spot any drift from "
        "target. Pick a property under 'Which ingredient drives each outcome' to see which raw "
        "material's dosage correlates most strongly with that result before adjusting any dosage "
        "on the floor. If the current formulation isn't meeting target, confirm the target "
        "properties further down and request a PI3 recommendation, then take that proposal to "
        "your technical team to trial and confirm before releasing it as a new recipe version."
    ),
)
session = get_session()


# Only offer a grade here if this page can actually do something useful with
# it - a recipe version (for cost/diff) and at least one quality test result
# (for the property-outcomes table and correlations) - rather than letting
# the reviewer pick a grade and then hit a dead end on every section.
grades = [
    g for g in session.query(FoamGrade).all()
    if g.recipe_versions and not property_results_dataframe(session, foam_grade_id=g.id).empty
]
if not grades:
    st.warning(
        "No foam grade yet has both a recipe version and quality test results recorded - "
        "add these first before using Recipe Optimization."
    )
    st.stop()

grade = st.selectbox("Foam grade", grades, format_func=lambda g: g.grade_name)
versions = sorted(grade.recipe_versions, key=lambda v: v.created_at)

if not versions:
    st.info("This foam grade has no recipe versions yet.")
    st.stop()

# A new recipe version replaces the previous one in production - versions
# don't normally coexist, so the current (active) one is what the page
# leads with. Older versions are still fully available, just moved to the
# "Version history" section at the bottom instead of competing for
# attention with equal-weight sections up top. Falls back to the most
# recently created version for legacy data recorded before is_active
# existed (everything defaults to True at the DB level, so this only
# matters if a grade somehow ended up with none or several marked active).
current_version = next((v for v in versions if v.is_active), versions[-1])

results_df = property_results_dataframe(session, foam_grade_id=grade.id)
available_properties = sorted(results_df["property_name"].dropna().unique()) if not results_df.empty else []

if results_df.empty:
    st.info("No quality test results recorded yet for this foam grade's production runs.")
else:
    st.subheader("Physical properties")
    overall_summary = (
        results_df.groupby("property_name")
        .agg(
            avg_target=("target_value", "mean"),
            avg_actual=("actual_value", "mean"),
            unit=("unit", "first"),
            pass_rate=("pass_fail", pass_rate),
        )
        .reset_index()
        .rename(
            columns={
                "property_name": "Property",
                "avg_target": "Avg target",
                "avg_actual": "Avg actual",
                "unit": "UOM",
            }
        )
    )
    overall_summary["Avg target"] = overall_summary["Avg target"].round(2)
    overall_summary["Avg actual"] = overall_summary["Avg actual"].round(2)
    overall_summary["UOM"] = overall_summary["UOM"].fillna("—")
    overall_summary["Pass rate"] = overall_summary["pass_rate"].apply(
        lambda p: f"{p:.0%}" if pd.notna(p) else "—"
    )
    overall_summary = overall_summary[["Property", "Avg target", "Avg actual", "UOM", "Pass rate"]]
    render_data_table(overall_summary)

# Per-property, per-version summary tables - not shown on screen (see the
# consolidated table above), but kept keyed by property name so the PI3
# recommendation prompt below can still reference which specific recipe
# version each result belongs to.
property_summaries = {}
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

def _cost_per_kg(cost: dict):
    """Converts recipe_version_cost()'s php-based total (cost for the mix
    represented by total_php parts - the standard costing basis in this
    industry) into a straightforward cost per kg, treating 1 php part as
    1 kg once a recipe is scaled up to an actual production batch."""
    if cost["total_cost"] is None or not cost["total_php"]:
        return None
    return round(cost["total_cost"] / cost["total_php"], 2)


# ---------------------------------------------------------------------------
# Current formulation - the one version actually in production use
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Current formulation")
cost_by_version = {v.id: recipe_version_cost(session, v) for v in versions}
current_cost = cost_by_version[current_version.id]
label_bits = [current_version.version_label, current_version.approval_status]
if current_version.change_note:
    label_bits.append(current_version.change_note)
st.caption(" — ".join(label_bits))

if current_version.components:
    render_data_table(
        pd.DataFrame(
            [
                {
                    "Raw material": c.raw_material_name,
                    "Supplier": c.supplier,
                    "php": c.php,
                    "Role": c.role_in_formulation,
                }
                for c in current_version.components
            ]
        )
    )
    coverage_pct = (
        round((current_cost["priced_php"] / current_cost["total_php"]) * 100, 0)
        if current_cost["total_php"] else None
    )
    current_cost_per_kg = _cost_per_kg(current_cost)
    if current_cost_per_kg is not None:
        st.write(
            f"**Cost per kg: {current_cost_per_kg:.2f} USD** "
            f"(coverage {coverage_pct:.0f}%)" if coverage_pct is not None else
            f"**Cost per kg: {current_cost_per_kg:.2f} USD**"
        )
    else:
        st.caption("No cost data recorded for any material in this version yet.")
    if current_cost["missing"]:
        st.caption(
            "Cost shown is a lower-bound estimate - missing a recorded cost/kg for: "
            f"{', '.join(current_cost['missing'])}. Add pricing on the Raw Materials page to "
            "complete this total."
        )
else:
    st.caption("No components recorded for this version yet.")

st.caption(
    "This is the formulation currently in production use for this grade - a new version "
    "replaces the previous one rather than running alongside it. For cost comparison, "
    "what changed at the last revision, or an older version's ingredient list, see "
    "'Version history' at the bottom of this page."
)

# ---------------------------------------------------------------------------
# Which ingredient actually drives the outcome
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Which ingredient drives each outcome")
st.caption(
    "Ranks each raw material by how strongly its dosage lines up with the selected property, two "
    "ways: by ACTUAL metered dosage per production run (from Finalized-phase flow-meter readings), "
    "which reflects normal batch-to-batch metering variance in the current recipe; and by PLANNED "
    "php on each recipe version, which reflects what happens when the target formulation itself "
    "changes. These can rank differently, since one tracks metering drift and the other tracks "
    "formulation change - use the actual-dosage ranking for day-to-day process troubleshooting, "
    "and the planned-version ranking when evaluating a formulation change."
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
        render_data_table(
            actual_ranked.rename(
                columns={
                    "raw_material_name": "Raw material",
                    "n_runs": "Runs compared",
                    "correlation": "Correlation with outcome",
                }
            )
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
            render_data_table(
                component_ranked.rename(
                    columns={
                        "raw_material_name": "Raw material",
                        "n_versions": "Versions compared",
                        "correlation": "Correlation with outcome",
                    }
                )
            )
            top_component = component_ranked.iloc[0]
            st.caption(
                f"Strongest association for {corr_property}: **{top_component['raw_material_name']}** "
                f"(correlation {top_component['correlation']:+.3f} across "
                f"{int(top_component['n_versions'])} versions). Review applicability against current "
                "raw materials and process conditions before adjusting dosage."
            )

# ---------------------------------------------------------------------------
# PI3 recommendation, grounded in cost / diff / correlation data above
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Ask PI3 for a formulation recommendation")

plant_id = grade.product_family.plant_id if grade.product_family else None

if ai_assistant.is_enabled_for_plant(session, plant_id):
    st.caption(
        "Asks PI3 to propose a reformulation direction, using the cost, version-diff, and "
        "correlation data above as its basis rather than just the ingredient list. Target "
        "properties below are prefilled from this grade's stored specification - edit or add to "
        "them (resilience, tensile strength, ...) before requesting a recommendation."
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
            v_cost_per_kg = _cost_per_kg(c)
            if v_cost_per_kg is not None:
                note = "" if c["complete"] else f" (partial - missing cost for {', '.join(c['missing'])})"
                cost_lines.append(f"Version {v.version_label}: {v_cost_per_kg:.2f} USD per kg{note}")
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
            st.session_state.pop(f"recipe_opt_fixed_{grade.id}_saved_note_id", None)

    ai_answer = st.session_state.get(f"recipe_opt_ai_answer_{grade.id}")
    if ai_answer:
        st.subheader("🤖 PI3 recommendation")
        st.caption(
            "Generated by PI3 from this foam grade's formulation cost, version differences, "
            "ingredient-outcome correlations, and quality-test history, plus expert notes and "
            "historical cases. For your technical team to evaluate and confirm before applying."
        )
        st.write(ai_answer)
        ro_question_label = f"PI3 formulation recommendation for {grade.grade_name}"
        ro_dl_col, ro_save_col = st.columns([1, 1])
        with ro_dl_col:
            render_pi3_docx_download(
                session,
                plant_id,
                key_prefix=f"recipe_opt_fixed_{grade.id}",
                question_label=ro_question_label,
                answer=ai_answer,
                foam_grade_id=grade.id,
            )
        with ro_save_col:
            render_save_to_expert_notes_button(
                session,
                key_prefix=f"recipe_opt_fixed_{grade.id}",
                answer=ai_answer,
                question_label=ro_question_label,
                link_type="foam_grade",
                entity_id=grade.id,
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
        f"What does {grade.grade_name}'s current recipe cost per kg?",
        f"Which ingredient's actual dosage correlates most with density for {grade.grade_name}?",
        f"What changed between the last two recipe versions of {grade.grade_name}?",
        f"Have there been any quality issues reported for {grade.grade_name} recently?",
    ],
    key_prefix=f"ask_pi3_freeform_recipe_{grade.id}",
)

# ---------------------------------------------------------------------------
# Version history - reference only. A new version replaces the previous one
# in production, so this is for occasional audit (cost comparison, what
# changed at the last revision, an older version's ingredient list) rather
# than routine use - kept out of the way at the bottom instead of competing
# with the current formulation above.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Version history")
st.caption(
    "Reference only - recipe versions don't normally coexist in production, so this is for "
    "occasional audit rather than day-to-day use."
)

with st.expander("Formulation cost by version"):
    cost_rows = []
    for v in versions:
        c = cost_by_version[v.id]
        coverage_pct = round((c["priced_php"] / c["total_php"]) * 100, 0) if c["total_php"] else None
        cost_rows.append(
            {
                "Version": v.version_label,
                "Active": "Yes" if v.is_active else "No",
                "Status": v.approval_status,
                "Cost per kg (USD)": _cost_per_kg(c),
                "Cost coverage": f"{coverage_pct:.0f}%" if coverage_pct is not None else "—",
                "Materials missing cost": ", ".join(c["missing"]) if c["missing"] else "—",
            }
        )
    render_data_table(pd.DataFrame(cost_rows))
    if any(c["missing"] for c in cost_by_version.values()):
        st.caption(
            "Costs shown are a lower-bound estimate where materials are missing a recorded "
            "cost/kg - add pricing on the Raw Materials page to complete these totals."
        )

with st.expander("Compare two versions"):
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
            render_data_table(
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
                )
            )
            changed_count = (diff_df["status"] != "Unchanged").sum()
            st.caption(
                f"{changed_count} of {len(diff_df)} materials differ between "
                f"{version_a.version_label} and {version_b.version_label}."
            )

with st.expander("All recipe versions"):
    for v in versions:
        active_tag = " — 🟢 Active" if v.is_active else ""
        st.markdown(
            f"**{v.version_label} — {v.approval_status}**{active_tag}"
            + (f" — {v.change_note}" if v.change_note else "")
        )
        if v.components:
            render_data_table(
                pd.DataFrame(
                    [
                        {
                            "Raw material": c.raw_material_name,
                            "Supplier": c.supplier,
                            "php": c.php,
                            "Role": c.role_in_formulation,
                        }
                        for c in v.components
                    ]
                )
            )
        else:
            st.caption("No components recorded for this version yet.")
        st.markdown("---")
