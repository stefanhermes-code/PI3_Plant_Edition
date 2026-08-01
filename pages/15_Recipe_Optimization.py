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
from access_control import can_use_page
from analytics import (
    pass_rate,
    property_results_dataframe,
    rank_component_actual_correlations,
    recipe_version_cost,
    recipe_version_diff,
)
from auth import current_user, logout_button, require_login
from db import FoamGrade, get_session, init_db
from helpers import (
    page_setup,
    render_ask_pi3_section,
    render_data_table,
    render_function_action_intro,
    render_pi3_docx_download,
    render_save_to_expert_notes_button,
    view_only_notice,
)
from tenant_scope import apply_scope, company_picker, grade_ids_for_company

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
        "elongation, compression set and resilience. It then answers two questions per property, "
        "using only this recipe's own actual production history: given the current recipe, do the "
        "results we actually get line up with what's required; and where a property is missing "
        "target, does the actual metered dosage of a raw material explain why. Older recipe "
        "versions, cost history, and a side-by-side version diff are kept under 'Version history' "
        "at the bottom for audit purposes."
    ),
    action_text=(
        "Select the foam grade you want to review, then check the current formulation's cost per "
        "kg and ingredient list against the quality-outcome table below to spot any drift from "
        "target. Check 'Does the current recipe meet target?' for a straight achieved/not-achieved "
        "answer per property, then pick a property there to see which raw material's actual "
        "dosage correlates most strongly with a miss before adjusting anything on the floor. If "
        "the current formulation isn't meeting target, confirm the target properties further down "
        "and request a PI3 recommendation, then take that proposal to your technical team to trial "
        "and confirm before releasing it as a new recipe version."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("recipe_optimization", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice(action="using PI3 and saving to Expert Notes")
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="recipe_opt_company_filter"
)
active_company_id = company.id if company else None
scoped_grade_ids = grade_ids_for_company(session, active_company_id)


# Only offer a grade here if this page can actually do something useful with
# it - a recipe version (for cost/diff) and at least one quality test result
# (for the property-outcomes table and correlations) - rather than letting
# the reviewer pick a grade and then hit a dead end on every section.
grades = [
    g for g in apply_scope(session.query(FoamGrade), FoamGrade.id, scoped_grade_ids).all()
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
# Does the current recipe meet target, and if not, why?
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Does the current recipe meet target?")
st.caption(
    "Two questions, answered per property, using only production runs made under the CURRENT "
    "recipe (not older versions - which version was running earlier doesn't matter here): did we "
    "achieve the required property, and if not, does the actual metered dosage of a raw material "
    "explain why."
)

current_version_results = results_df[results_df["recipe_version_id"] == current_version.id]

if current_version_results.empty:
    st.info(
        f"No quality test results recorded yet for production runs made under the current recipe "
        f"({current_version.version_label}) - nothing to check against target yet."
    )
    expectation_summary = pd.DataFrame()
else:
    expectation_summary = (
        current_version_results.groupby("property_name")
        .agg(
            avg_actual=("actual_value", "mean"),
            avg_target=("target_value", "mean"),
            unit=("unit", "first"),
            pass_rate=("pass_fail", pass_rate),
            n=("result_id", "count"),
        )
        .reset_index()
    )
    expectation_summary["avg_actual"] = expectation_summary["avg_actual"].round(2)
    expectation_summary["avg_target"] = expectation_summary["avg_target"].round(2)
    expectation_summary["achieved"] = expectation_summary["pass_rate"].apply(
        lambda p: "Yes" if pd.notna(p) and p >= 1.0 else ("No" if pd.notna(p) else "—")
    )

    display_expectation = expectation_summary.copy()
    display_expectation["Pass rate"] = display_expectation["pass_rate"].apply(
        lambda p: f"{p:.0%}" if pd.notna(p) else "—"
    )
    display_expectation = display_expectation.rename(
        columns={
            "property_name": "Property",
            "avg_actual": "Avg actual (current recipe)",
            "avg_target": "Required (target)",
            "unit": "UOM",
            "achieved": "Achieved?",
            "n": "Runs",
        }
    )
    render_data_table(
        display_expectation[
            [
                "Property",
                "Avg actual (current recipe)",
                "Required (target)",
                "UOM",
                "Pass rate",
                "Achieved?",
                "Runs",
            ]
        ]
    )

st.markdown("**If a property is missing target, does actual dosage explain it?**")
if not available_properties:
    st.info("No quality test results recorded yet - nothing to check.")
else:
    corr_property = st.selectbox(
        "Property", available_properties, key=f"corr_property_{grade.id}"
    )

    prop_row = (
        expectation_summary[expectation_summary["property_name"] == corr_property]
        if not expectation_summary.empty
        else pd.DataFrame()
    )
    if prop_row.empty:
        st.info(
            f"No quality test results recorded yet for {corr_property} under the current recipe "
            f"({current_version.version_label})."
        )
    else:
        achieved = prop_row.iloc[0]["achieved"]
        pass_rate_text = (
            f"{prop_row.iloc[0]['pass_rate']:.0%}" if pd.notna(prop_row.iloc[0]["pass_rate"]) else "—"
        )
        if achieved == "Yes":
            st.success(
                f"Achieved: every {corr_property} result under the current recipe met target "
                f"({pass_rate_text} pass rate)."
            )
        else:
            st.warning(
                f"Not achieved: only {pass_rate_text} of {corr_property} results under the current "
                "recipe met target."
            )

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
            f"{int(top_actual['n_runs'])} production runs' metered dosage). A lead to investigate "
            "on the floor, not a confirmed cause - review against current raw materials and "
            "process conditions before adjusting dosage."
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
        "properties below are prefilled from this grade's target density/hardness plus any other "
        "target properties recorded on the Product Family & Foam Grade page - edit or add to them "
        "before requesting a recommendation."
    )
    # Built from foam_grades' own structured fields rather than free text:
    # target_density/target_hardness are dedicated columns every grade has,
    # and any other target property (resilience, tensile strength, ...) comes
    # from foam_grade_target_properties, entered on the Product Family & Foam
    # Grade page. A property listed there with no target_value yet ("value
    # not yet known") is still surfaced, just flagged as such.
    default_targets = []
    if grade.target_density is not None:
        default_targets.append(f"Density {grade.target_density:g} kg/m3")
    if grade.target_hardness is not None:
        default_targets.append(f"Hardness (40% ILD) {grade.target_hardness:g} N")
    for tp in grade.target_properties:
        unit_suffix = f" {tp.unit}" if tp.unit else ""
        if tp.target_value is not None:
            default_targets.append(f"{tp.property_name} {tp.target_value:g}{unit_suffix}")
        else:
            note_suffix = f" - {tp.notes}" if tp.notes else ""
            default_targets.append(f"{tp.property_name} (target value not yet known{note_suffix})")

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
        disabled=not target_properties.strip() or not page_usable,
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

        if not expectation_summary.empty:
            achieved_lines = [
                f"{row['property_name']}: avg actual {row['avg_actual']}, required {row['avg_target']}, "
                f"achieved={row['achieved']}, n={int(row['n'])} runs under this recipe"
                for _, row in expectation_summary.iterrows()
            ]
            achieved_summary = "\n".join(achieved_lines)
        else:
            achieved_summary = "No quality test results recorded yet under the current recipe."

        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam manufacturer "
            f"select a formulation direction for {grade.grade_name}. Below is this foam grade's "
            "recipe version history: formulation composition, formulation cost, the most recent "
            "version-to-version change, whether the CURRENT recipe achieves each required property "
            "(based only on production runs made under this recipe), which ingredient's ACTUAL "
            "metered per-run dosage is statistically associated with each quality outcome, and "
            "quality test outcomes by version. Use this quantified data - not just the ingredient "
            "list - as the basis of your reasoning, plus any relevant expert notes or historical "
            "cases in the connected knowledge base, to propose a formulation that could meet the "
            "target properties given.\n\n"
            "Phrase this as a recommendation for the reviewer to evaluate and confirm through "
            "their own trial process, addressed directly to the target properties requested. "
            "Where you rely on a specific cost, diff, or correlation figure below, refer to it "
            "explicitly rather than restating the raw ingredient list.\n\n"
            f"Foam grade: {grade.grade_name}\n\n"
            f"Recipe versions and composition:\n{composition_summary}\n\n"
            f"Formulation cost by version:\n{cost_summary}\n\n"
            f"Most recent formulation change:\n{diff_summary}\n\n"
            f"Does the current recipe achieve each required property (this recipe's own "
            f"production runs only):\n{achieved_summary}\n\n"
            f"Actual metered dosage vs. outcome correlations (top 3 per property, per production "
            f"run):\n{actual_correlation_summary}\n\n"
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
                disabled=not page_usable,
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
    disabled=not page_usable,
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
