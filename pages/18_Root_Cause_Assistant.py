"""Industrial Intelligence: Root-Cause Assistant

Given a quality observation, surfaces what was different about that run
compared to the most recent prior run of the same foam grade - recipe
version, machine, or Finalized-phase process settings - as a starting
point for the reviewer's own investigation. Historical comparison for
technical review (see the advisory boundary at the bottom of this page).
"""

import streamlit as st

import ai_assistant
from analytics import PHASE_SETTING_LABELS, run_settings_dataframe
from auth import logout_button, require_login
from db import QualityObservation, get_session, init_db
from helpers import page_setup

page_setup("Root-Cause Assistant")
init_db()
require_login()
logout_button()

st.title("Root-Cause Assistant")
st.caption(
    "Compares the flagged run against the most recent prior run of the same foam grade, and "
    "lists what was different - recipe version, machine, or process settings - as a starting "
    "point for the reviewer's own investigation."
)
session = get_session()

observations = session.query(QualityObservation).order_by(QualityObservation.observed_at.desc()).all()
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
        changes.append(f"{label} shifted {pct_change:+.0%}: {prev_val:g} → {cur_val:g}")

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
    if st.button("Use PI3 to reason about this", key=f"ask_pi3_root_cause_{obs.id}"):
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
            f"Quality issue: {obs.observation_type} on run #{run.id} ({grade.grade_name}), "
            f"{obs.severity}/{obs.frequency}\n"
            + (f"Logged suspected cause: {obs.suspected_cause}\n" if obs.suspected_cause else "")
            + f"Compared against prior run #{int(prior['run_id'])} ({prior['run_date']})\n\n"
            f"What was different:\n{change_summary}\n"
        )
        with st.spinner("Using PI3..."):
            answer = ai_assistant.ask_assistant(prompt)
        if answer:
            st.session_state[f"root_cause_ai_answer_{obs.id}"] = answer

    ai_answer = st.session_state.get(f"root_cause_ai_answer_{obs.id}")
    if ai_answer:
        st.subheader("🤖 PI3 hypothesis")
        st.caption(
            "Generated by PI3 from the comparison above plus expert notes and historical "
            "cases. Confirm any hypothesis through your own investigation before acting on it."
        )
        st.write(ai_answer)

