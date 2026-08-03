"""Industrial Intelligence: Root-Cause Assistant

Given a quality observation, surfaces what was different about that run
compared to the most recent prior run of the same foam grade - recipe
version, machine, or Finalized-phase process settings - as a starting
point for the reviewer's own investigation. Historical comparison for
technical review (see the advisory boundary at the bottom of this page).
"""

import streamlit as st

import ai_assistant
from access_control import can_use_page
from analytics import PHASE_SETTING_LABELS, run_settings_dataframe
from auth import current_user, logout_button, require_login
from db import QualityObservation, get_session, init_db
from tenant_scope import apply_scope, company_picker, run_ids_for_company
from helpers import (
    page_setup,
    render_ask_pi3_section,
    render_function_action_intro,
    render_pi3_docx_download,
    render_save_to_expert_notes_button,
    view_only_notice,
)

page_setup("Root-Cause Assistant")
init_db()
require_login()
logout_button()

st.title("Root-Cause Assistant")
render_function_action_intro(
    function_text=(
        "Given a logged quality issue, compares that run against the most recent prior run of the "
        "same foam grade and lists what was different - recipe version, machine, or Finalized-"
        "phase process settings (mixer rpm, ratio/index, air pressure, and so on) - as a starting "
        "point for your own investigation, not a diagnosis. PI3 can then help interpret that diff "
        "against expert notes and similar past cases."
    ),
    action_text=(
        "Select the quality issue you're investigating, review the run-vs-prior-run diff shown, "
        "and check whether any of the flagged differences line up with a plausible cause. Use "
        "'Ask PI3' if you want that diff interpreted alongside historical expert notes and "
        "similar past cases before you commit to a root cause."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("root_cause_assistant", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice(action="using PI3 and saving to Expert Notes")
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="rca_company_filter"
)
active_company_id = company.id if company else None
scoped_run_ids = run_ids_for_company(session, active_company_id)

observations = (
    apply_scope(session.query(QualityObservation), QualityObservation.production_run_id, scoped_run_ids)
    # Root-Cause Assistant compares a run against its most recent prior run's
    # machine/process settings - a lab trial (Customer Trial / Optimization
    # Trial, added 2026-08-03) has neither, so a trial-sourced quality issue
    # (production_run_id NULL) is excluded here unconditionally rather than
    # offered as a dead-end pick. See analytics.py's property_results_dataframe
    # docstring for why this page never accepts an include_trials toggle.
    .filter(QualityObservation.production_run_id.isnot(None))
    .order_by(QualityObservation.observed_at.desc())
    .all()
)
if not observations:
    st.info("No quality issues recorded yet.")
    st.stop()

obs = st.selectbox(
    "Quality issue",
    observations,
    format_func=lambda o: (
        f"{o.observation_type} — {o.production_run.foam_grade.grade_name} "
        f"(run #{o.production_run_id}, {o.observed_at}) · {o.severity}/{o.frequency}"
    ),
)

run = obs.production_run
grade = run.foam_grade

st.divider()
st.subheader(f"{obs.observation_type} on run #{run.id} ({run.run_date})")
c1, c2 = st.columns(2)
c1.metric("Severity", obs.severity)
c2.metric("Frequency", obs.frequency)
if obs.suspected_cause:
    st.caption(f"Logged suspected cause: {obs.suspected_cause}")

settings_df = run_settings_dataframe(session, foam_grade_id=grade.id)
settings_df = settings_df.sort_values("run_date")

current_rows = settings_df[settings_df["run_id"] == run.id]
if current_rows.empty:
    st.warning("No Finalized-phase settings recorded for this run yet — nothing to compare.")
    st.stop()
current = current_rows.iloc[0]

prior_rows = settings_df[settings_df["run_date"] < run.run_date]
if prior_rows.empty:
    st.info(f"No earlier production run of {grade.grade_name} to compare against.")
    st.stop()
prior = prior_rows.iloc[-1]

st.markdown(f"**Compared against run #{int(prior['run_id'])}** ({prior['run_date']})")

changes = []
if current["recipe_version"] != prior["recipe_version"]:
    changes.append(f"Recipe version changed: {prior['recipe_version']} → {current['recipe_version']}")
if current["machine"] != prior["machine"]:
    changes.append(f"Machine changed: {prior['machine'] or '—'} → {current['machine'] or '—'}")

for field, label in PHASE_SETTING_LABELS.items():
    prev_val, cur_val = prior.get(field), current.get(field)
    if prev_val is None or cur_val is None:
        continue
    if prev_val == 0:
        continue
    pct_change = (cur_val - prev_val) / abs(prev_val)
    if abs(pct_change) >= 0.02:
        changes.append(f"{label} shifted {pct_change:+.2%}: {prev_val:g} → {cur_val:g}")

if changes:
    st.write("**What was different:**")
    for c in changes:
        st.write(f"- {c}")
else:
    st.info(
        "No meaningful difference found in recipe, machine, or recorded process settings between "
        "these two runs — the cause may lie outside what this app currently captures (raw material "
        "lot variation, ambient conditions, downstream handling)."
    )

if ai_assistant.is_enabled_for_plant(session, run.plant_id):
    st.divider()
    if st.button(
        "Use PI3 to reason about this",
        key=f"ask_pi3_root_cause_{obs.id}",
        disabled=not page_usable,
    ):
        change_summary = (
            "\n".join(f"- {c}" for c in changes)
            if changes
            else (
                "No meaningful difference was found in recipe, machine, or recorded process "
                "settings between these two runs."
            )
        )
        prompt = (
            "You are helping a technical reviewer at a flexible slabstock foam manufacturer "
            "investigate a quality issue. Below is a deterministic comparison between the "
            "flagged production run and the most recent prior run of the same foam grade. "
            "Using this comparison plus any relevant expert notes or historical cases in the "
            "connected knowledge base, suggest possible hypotheses for the root cause.\n\n"
            "IMPORTANT: this is a starting point for investigation, not a diagnosis. Never "
            "phrase your answer as a directive or a definitive cause (e.g. do not say "
            "'increase TDI by X' or 'the cause is Y'). Always phrase it as hypotheses for the "
            "reviewer to investigate and confirm themselves.\n\n"
            "DISCIPLINE REQUIRED IN YOUR REASONING (a prior answer from this feature was "
            "reviewed by a formulation expert and found too quick to rank causes without "
            "enough evidence - follow these rules to avoid repeating that):\n"
            "1. State any percentage change to at least two decimal places, exactly as given "
            "below - never round '-3.47%' down to '-3%'.\n"
            "2. Before proposing any ranking, first ask what is actually known about the "
            "failure itself: did it collapse while still rising, settle immediately at peak "
            "rise, or shrink later during cooling? Was it localized or across the whole bun? "
            "What did the cell structure look like (coarse, ruptured, tight, normal)? If this "
            "app's data does not capture that, say so explicitly and list it as the first, "
            "still-open investigation item - do not silently assume a failure mode.\n"
            "3. Do not call any subset of hypotheses 'leading' or imply a priority ordering "
            "unless you can point to a specific piece of evidence in the comparison or the "
            "knowledge base that favors one hypothesis over another. If no such evidence "
            "exists, present hypotheses as an unranked candidate set and say the ranking "
            "requires more evidence (morphology, timing, actual-vs-setpoint deliveries).\n"
            "4. Give equal analytical weight to overall stoichiometry/index and mixing "
            "factors (water dosage, isocyanate/TDI delivery and index, component "
            "temperatures, mixing energy or pressure, polyol-blend homogeneity) alongside "
            "additive-level factors (silicone, catalysts). The fact that air injection is "
            "the only recorded numeric difference does not mean other factors are less "
            "likely - it only means this app didn't capture a difference in them for this "
            "comparison. Do not let one recorded change anchor the whole explanation.\n"
            "5. When discussing amine catalysts, do not say 'excessive amine' as a blanket "
            "statement. Amine catalysts vary - some are blow-selective, some gel-selective, "
            "some balanced. Frame this hypothesis as 'excessive effective blow catalysis "
            "relative to gel development', and note it could come from over-delivery of a "
            "blow-selective amine, under-delivery/deactivation of tin, excess water, a low "
            "index, temperature-driven acceleration, or a wrong catalyst grade/concentration.\n"
            "6. Keep 'reduced/wrong silicone performance' (wrong grade, degraded, incompatible, "
            "under- or over-dosed, poor blending) analytically separate from 'foreign-material "
            "contamination' (mold-release agent, external oil/grease, defoamer, cleaning "
            "residue, water). Do not describe silicone itself as a contaminant - it is a "
            "deliberate formulation component. Note that too much silicone/stabilization can "
            "also cause tight cells and shrinkage, not just too little causing collapse.\n"
            "7. Comment on whether the comparison run is actually a sound baseline (same "
            "formulation, comparable density, similar raw-material lots, comparable "
            "temperatures, same equipment configuration, no intervening maintenance) - if "
            "that cannot be confirmed from what's given, flag it as a caveat and suggest "
            "also checking the nearest stable run immediately before and after the flagged "
            "run, not only this one historical comparison.\n"
            "8. Close with a short, appropriately hedged synthesis: state plainly what is "
            "directly known (the recorded change) versus what is inferred, and avoid "
            "presenting a narrow additive-focused explanation as the most coherent one "
            "before the failure's timing and morphology are established.\n\n"
            f"Quality issue: {obs.observation_type} on run #{run.id} ({grade.grade_name}), "
            f"{obs.severity}/{obs.frequency}\n"
            + (f"Logged suspected cause: {obs.suspected_cause}\n" if obs.suspected_cause else "")
            + f"Compared against prior run #{int(prior['run_id'])} ({prior['run_date']})\n\n"
            f"What was different:\n{change_summary}\n"
        )
        with st.spinner("Using PI3..."):
            answer = ai_assistant.ask_assistant(prompt, company_id=active_company_id)
        if answer:
            st.session_state[f"root_cause_ai_answer_{obs.id}"] = answer
            st.session_state.pop(f"root_cause_fixed_{obs.id}_saved_note_id", None)

    ai_answer = st.session_state.get(f"root_cause_ai_answer_{obs.id}")
    if ai_answer:
        st.subheader("🤖 PI3 hypothesis")
        st.caption(
            "Generated by PI3 from the comparison above plus expert notes and historical "
            "cases. Confirm any hypothesis through your own investigation before acting on it."
        )
        st.write(ai_answer)

        rc_question_label = f"PI3 root-cause hypothesis for {obs.observation_type} on run #{run.id} ({grade.grade_name})"
        rc_dl_col, rc_save_col = st.columns([1, 1])
        with rc_dl_col:
            render_pi3_docx_download(
                session,
                run.plant_id,
                key_prefix=f"root_cause_fixed_{obs.id}",
                question_label=rc_question_label,
                answer=ai_answer,
                foam_grade_id=grade.id,
            )
        with rc_save_col:
            render_save_to_expert_notes_button(
                session,
                key_prefix=f"root_cause_fixed_{obs.id}",
                answer=ai_answer,
                question_label=rc_question_label,
                link_type="production_run",
                entity_id=run.id,
                disabled=not page_usable,
            )

st.divider()
render_ask_pi3_section(
    session,
    run.plant_id,
    default_foam_grade_id=grade.id,
    page_context=(
        f"The reviewer is on the Root-Cause Assistant page, investigating "
        f"'{obs.observation_type}' ({obs.severity}/{obs.frequency}) on run #{run.id} of foam "
        f"grade '{grade.grade_name}', compared against prior run #{int(prior['run_id'])}."
    ),
    sample_questions=[
        f"What raw material lots were used on run #{run.id} versus run #{int(prior['run_id'])}?",
        f"Has {obs.observation_type} happened before on {grade.grade_name}, and what was found?",
        f"Were there any maintenance events or interventions logged around run #{run.id}?",
    ],
    key_prefix=f"ask_pi3_freeform_root_cause_{obs.id}",
    note_link_type="production_run",
    note_entity_id=run.id,
    disabled=not page_usable,
)

