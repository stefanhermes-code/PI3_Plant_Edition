"""Screen 5: Production Run / Trial Record

Includes the Mandatory-tier process-data capture recommended in "Expanding
PI3 Plant Edition Production-Trial Data Capture for Polyurethane Foaming
Lines": phase timestamps, per-stream setpoint/actual flow-pressure-temp
statistics, machine settings (mixer/conveyor/laydown/sidewall), an
alarm/intervention/grade-change event log, and raw-material lot tracking.
No live PLC/OPC UA/MQTT connection exists yet, so every section supports
both manual entry and CSV/Excel import (same pattern as Runtime Data).
"""

import datetime as dt

import pandas as pd
import streamlit as st

from auth import logout_button, require_login
from db import (
    EVENT_TYPES,
    PHASE_NAMES,
    SEVERITIES,
    ComponentStreamReading,
    FoamGrade,
    ProductionEvent,
    ProductionPhase,
    ProductionRun,
    RawMaterialLotUse,
    RecipeVersion,
    RuntimeDataRecord,
    TrialRecord,
    get_session,
    init_db,
)
from helpers import combine_date_time, page_setup, parse_bool, parse_dt, show_advisory_footer

RUNTIME_REQUIRED_COLUMNS = ["production_run_id"]
RUNTIME_OPTIONAL_COLUMNS = [
    "line_speed",
    "pump_speed_or_flow_data",
    "temperature_data",
    "pressure_data",
    "ambient_temperature",
    "ambient_humidity",
    "rise_time",
    "curing_notes",
]

PHASE_REQUIRED_COLUMNS = ["production_run_id", "phase_name"]
PHASE_OPTIONAL_COLUMNS = [
    "phase_start", "phase_end", "is_steady_state",
    "mixer_rpm_setpoint", "mixer_rpm_actual_mean",
    "conveyor_speed_setpoint", "conveyor_speed_actual_mean",
    "air_injection_rate", "air_pressure_bar",
    "laydown_mode", "section_positions_note",
    "sidewall_width_mm", "foam_height_actual_mean_mm", "notes",
]

STREAM_REQUIRED_COLUMNS = ["production_run_id", "phase_name", "stream_name"]
STREAM_OPTIONAL_COLUMNS = [
    "flow_unit", "flow_setpoint", "flow_actual_mean", "flow_actual_min",
    "flow_actual_max", "flow_actual_sd", "pressure_actual_mean_bar",
    "temperature_setpoint_c", "temperature_actual_mean_c", "notes",
]

EVENT_REQUIRED_COLUMNS = ["production_run_id", "event_type", "event_ts"]
EVENT_OPTIONAL_COLUMNS = ["phase_name", "severity", "description", "action_taken"]

LOT_REQUIRED_COLUMNS = ["production_run_id", "component_stream_name", "supplier_lot_no"]
LOT_OPTIONAL_COLUMNS = ["notes"]


page_setup("Production Run / Trial Record")
init_db()
require_login()
logout_button()

st.title("Production Run / Trial Record")
session = get_session()

grades = session.query(FoamGrade).all()
if not grades:
    st.warning("Add a foam grade and recipe version first.")
    st.stop()

with st.expander("Add production run + trial", expanded=False):
    with st.form("add_run"):
        grade = st.selectbox("Foam grade *", grades, format_func=lambda g: g.grade_name)
        versions = (
            session.query(RecipeVersion).filter(RecipeVersion.foam_grade_id == grade.id).all()
            if grade
            else []
        )
        recipe_version = st.selectbox(
            "Recipe version *", versions, format_func=lambda v: v.version_label if v else "—"
        )
        run_date = st.date_input("Run date", value=dt.date.today())
        batch_reference = st.text_input("Batch reference")
        block_reference = st.text_input("Block reference")
        machine_id = st.text_input("Machine ID")
        operator = st.text_input("Operator / team reference")

        st.markdown("**Trial details**")
        objective = st.text_area("Trial or change objective *")
        hypothesis = st.text_area("Hypothesis")
        what_changed = st.text_area("What changed vs. baseline")
        responsible_person = st.text_input("Responsible person")

        submitted = st.form_submit_button("Save production run + trial")
        if submitted:
            if not recipe_version:
                st.error("This foam grade has no recipe version yet — add one first.")
            elif not objective:
                st.error("Trial objective is required.")
            else:
                run = ProductionRun(
                    plant_id=grade.product_family.plant_id,
                    foam_grade_id=grade.id,
                    recipe_version_id=recipe_version.id,
                    run_date=run_date,
                    batch_reference=batch_reference,
                    block_reference=block_reference,
                    machine_id=machine_id,
                    operator_or_team_reference=operator,
                )
                session.add(run)
                session.flush()
                trial = TrialRecord(
                    production_run_id=run.id,
                    trial_or_change_objective=objective,
                    hypothesis=hypothesis,
                    what_changed=what_changed,
                    responsible_person=responsible_person,
                    status="Open",
                )
                session.add(trial)
                session.commit()
                st.success("Production run and trial created.")
                st.rerun()

st.divider()
st.subheader("Trials")

status_filter = st.multiselect("Status filter", ["Open", "Pending Closure", "Closed"], default=["Open", "Pending Closure", "Closed"])
trials = session.query(TrialRecord).order_by(TrialRecord.created_at.desc()).all()
trials = [t for t in trials if t.status in status_filter]

if not trials:
    st.info("No trials match the current filter.")

for t in trials:
    run = t.production_run
    grade = run.foam_grade
    status_icon = {"Open": "🟡", "Pending Closure": "🟠", "Closed": "🟢"}.get(t.status, "⚪")
    with st.container(border=True):
        st.markdown(
            f"{status_icon} **Trial #{t.id}** — {grade.grade_name} · "
            f"recipe {run.recipe_version.version_label} · run {run.run_date} · status `{t.status}`"
        )
        st.write(f"**Objective:** {t.trial_or_change_objective}")
        if t.what_changed:
            st.caption(f"Changed: {t.what_changed}")
        if t.status != "Closed":
            missing = t.missing_closeout_fields()
            if missing:
                st.caption(f"⏳ Missing before closure: {', '.join(missing)}")
        else:
            st.caption(f"Closed {t.date_closed} — reviewed by {t.reviewed_by}, approved by {t.approved_by}")

runs = session.query(ProductionRun).order_by(ProductionRun.created_at.desc()).all()

# ---------------------------------------------------------------------------
# Process phases
# ---------------------------------------------------------------------------
st.divider()
st.subheader("⏱️ Process phases")
st.caption(
    "Every run is a sequence of phases (pre-run, start-up, stabilization, steady-state, "
    "adjustment/grade change, shutdown). Recording phase boundaries lets PI3 separate "
    "steady-state data from transition/scrap material."
)

if runs:
    tab_manual, tab_import = st.tabs(["Manual entry", "CSV / Excel import"])

    with tab_manual:
        run = st.selectbox(
            "Production run *",
            runs,
            format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
            key="phase_run_select",
        )
        with st.form("add_phase"):
            phase_name = st.selectbox("Phase *", PHASE_NAMES)
            is_steady_state = st.checkbox("This is the steady-state phase")
            phase_start = combine_date_time("Phase start", "phase_start")
            phase_end = combine_date_time("Phase end", "phase_end")

            st.markdown("**Machine settings for this phase**")
            c1, c2, c3, c4 = st.columns(4)
            mixer_rpm_setpoint = c1.number_input("Mixer rpm — setpoint", min_value=0.0, step=1.0)
            mixer_rpm_actual_mean = c2.number_input("Mixer rpm — actual mean", min_value=0.0, step=1.0)
            conveyor_speed_setpoint = c3.number_input("Conveyor m/min — setpoint", min_value=0.0, step=0.01)
            conveyor_speed_actual_mean = c4.number_input("Conveyor m/min — actual mean", min_value=0.0, step=0.01)

            c5, c6, c7, c8 = st.columns(4)
            air_injection_rate = c5.number_input("Air injection rate", min_value=0.0, step=0.1)
            air_pressure_bar = c6.number_input("Air pressure (bar)", min_value=0.0, step=0.05)
            sidewall_width_mm = c7.number_input("Sidewall width (mm)", min_value=0.0, step=1.0)
            foam_height_actual_mean_mm = c8.number_input("Foam height — actual mean (mm)", min_value=0.0, step=1.0)

            laydown_mode = st.text_input("Laydown mode (e.g. trough, fall-plate, liquid laydown, traversing)")
            section_positions_note = st.text_area("Section / geometry positions (free text)")
            notes = st.text_area("Phase notes")

            submitted = st.form_submit_button("Save phase")
            if submitted:
                if phase_end < phase_start:
                    st.error("Phase end must not be before phase start.")
                else:
                    session.add(
                        ProductionPhase(
                            production_run_id=run.id,
                            phase_name=phase_name,
                            phase_start=phase_start,
                            phase_end=phase_end,
                            is_steady_state=is_steady_state,
                            mixer_rpm_setpoint=mixer_rpm_setpoint or None,
                            mixer_rpm_actual_mean=mixer_rpm_actual_mean or None,
                            conveyor_speed_setpoint=conveyor_speed_setpoint or None,
                            conveyor_speed_actual_mean=conveyor_speed_actual_mean or None,
                            air_injection_rate=air_injection_rate or None,
                            air_pressure_bar=air_pressure_bar or None,
                            laydown_mode=laydown_mode,
                            section_positions_note=section_positions_note,
                            sidewall_width_mm=sidewall_width_mm or None,
                            foam_height_actual_mean_mm=foam_height_actual_mean_mm or None,
                            notes=notes,
                            source_file_reference="manual entry",
                        )
                    )
                    session.commit()
                    st.success("Phase saved.")
                    st.rerun()

    with tab_import:
        st.caption(
            "Required columns: " + ", ".join(PHASE_REQUIRED_COLUMNS) + ". Optional columns: "
            + ", ".join(PHASE_OPTIONAL_COLUMNS)
        )
        uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="phase_upload")
        if uploaded:
            try:
                df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
            except Exception as exc:
                st.error(f"Could not read file: {exc}")
                df = None

            if df is not None:
                missing_cols = [c for c in PHASE_REQUIRED_COLUMNS if c not in df.columns]
                if missing_cols:
                    st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
                else:
                    valid_run_ids = {r.id for r in runs}
                    good_rows, bad_rows = [], []
                    for _, row in df.iterrows():
                        if row.get("production_run_id") in valid_run_ids and row.get("phase_name") in PHASE_NAMES:
                            good_rows.append(row)
                        else:
                            bad_rows.append(row)

                    st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
                    if bad_rows:
                        st.warning(
                            "Flagged rows reference an unknown production_run_id or a phase_name outside "
                            f"the controlled list ({', '.join(PHASE_NAMES)})."
                        )
                        st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

                    if good_rows and st.button("Confirm import", key="confirm_phase_import"):
                        for row in good_rows:
                            session.add(
                                ProductionPhase(
                                    production_run_id=int(row["production_run_id"]),
                                    phase_name=row["phase_name"],
                                    phase_start=parse_dt(row.get("phase_start")),
                                    phase_end=parse_dt(row.get("phase_end")),
                                    is_steady_state=parse_bool(row.get("is_steady_state", False)),
                                    mixer_rpm_setpoint=row.get("mixer_rpm_setpoint"),
                                    mixer_rpm_actual_mean=row.get("mixer_rpm_actual_mean"),
                                    conveyor_speed_setpoint=row.get("conveyor_speed_setpoint"),
                                    conveyor_speed_actual_mean=row.get("conveyor_speed_actual_mean"),
                                    air_injection_rate=row.get("air_injection_rate"),
                                    air_pressure_bar=row.get("air_pressure_bar"),
                                    laydown_mode=str(row.get("laydown_mode", "") or ""),
                                    section_positions_note=str(row.get("section_positions_note", "") or ""),
                                    sidewall_width_mm=row.get("sidewall_width_mm"),
                                    foam_height_actual_mean_mm=row.get("foam_height_actual_mean_mm"),
                                    notes=str(row.get("notes", "") or ""),
                                    source_file_reference=uploaded.name,
                                )
                            )
                        session.commit()
                        st.success(f"Imported {len(good_rows)} phase(s) from {uploaded.name}.")
                        st.rerun()

    all_phases = session.query(ProductionPhase).order_by(ProductionPhase.phase_start.desc()).limit(30).all()
    if all_phases:
        with st.expander(f"Recent phases ({len(all_phases)} shown, max 30)"):
            st.dataframe(
                [
                    {
                        "Run": p.production_run_id,
                        "Phase": p.phase_name,
                        "Start": p.phase_start,
                        "End": p.phase_end,
                        "Steady-state": p.is_steady_state,
                        "Mixer rpm (actual)": p.mixer_rpm_actual_mean,
                        "Conveyor m/min (actual)": p.conveyor_speed_actual_mean,
                        "Laydown mode": p.laydown_mode,
                    }
                    for p in all_phases
                ],
                hide_index=True,
                use_container_width=True,
            )
else:
    st.info("Create a production run above before adding phases.")

# ---------------------------------------------------------------------------
# Component stream readings
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🧪 Component stream readings")
st.caption(
    "Per raw-material stream (polyol, isocyanate, water/blowing agent, catalyst, etc.), the flow "
    "setpoint vs. actual, pressure, and temperature for a given phase. A phase must exist first."
)

all_phases_for_form = session.query(ProductionPhase).order_by(ProductionPhase.phase_start.desc()).all()

if not runs:
    st.info("Create a production run above before adding stream readings.")
elif not all_phases_for_form:
    st.info("Add a process phase above before adding stream readings.")
else:
    tab_manual, tab_import = st.tabs(["Manual entry", "CSV / Excel import"])

    with tab_manual:
        run = st.selectbox(
            "Production run *",
            runs,
            format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
            key="stream_run_select",
        )
        phases_for_run = (
            session.query(ProductionPhase).filter(ProductionPhase.production_run_id == run.id).all()
            if run
            else []
        )
        if not phases_for_run:
            st.warning("This run has no phases yet — add one above first.")
        else:
            phase = st.selectbox(
                "Phase *",
                phases_for_run,
                format_func=lambda p: f"{p.phase_name} ({p.phase_start})",
                key="stream_phase_select",
            )
            with st.form("add_stream_reading"):
                stream_name = st.text_input("Stream name * (e.g. Polyol A, TDI 80/20, Water blend, Catalyst)")
                flow_unit = st.selectbox("Flow unit", ["kg/min", "L/min"])
                c1, c2, c3, c4 = st.columns(4)
                flow_setpoint = c1.number_input("Flow — setpoint", min_value=0.0, step=0.1)
                flow_actual_mean = c2.number_input("Flow — actual mean", min_value=0.0, step=0.1)
                flow_actual_min = c3.number_input("Flow — actual min", min_value=0.0, step=0.1)
                flow_actual_max = c4.number_input("Flow — actual max", min_value=0.0, step=0.1)
                c5, c6, c7 = st.columns(3)
                flow_actual_sd = c5.number_input("Flow — actual std. dev.", min_value=0.0, step=0.01)
                pressure_actual_mean_bar = c6.number_input("Pressure — actual mean (bar)", min_value=0.0, step=0.1)
                temperature_setpoint_c = c7.number_input("Temperature — setpoint (°C)", step=0.1)
                temperature_actual_mean_c = st.number_input("Temperature — actual mean (°C)", step=0.1)
                notes = st.text_area("Notes")

                submitted = st.form_submit_button("Save stream reading")
                if submitted:
                    if not stream_name:
                        st.error("Stream name is required.")
                    else:
                        session.add(
                            ComponentStreamReading(
                                production_phase_id=phase.id,
                                stream_name=stream_name,
                                flow_unit=flow_unit,
                                flow_setpoint=flow_setpoint or None,
                                flow_actual_mean=flow_actual_mean or None,
                                flow_actual_min=flow_actual_min or None,
                                flow_actual_max=flow_actual_max or None,
                                flow_actual_sd=flow_actual_sd or None,
                                pressure_actual_mean_bar=pressure_actual_mean_bar or None,
                                temperature_setpoint_c=temperature_setpoint_c or None,
                                temperature_actual_mean_c=temperature_actual_mean_c or None,
                                notes=notes,
                                source_file_reference="manual entry",
                            )
                        )
                        session.commit()
                        st.success("Stream reading saved.")
                        st.rerun()

    with tab_import:
        st.caption(
            "Required columns: " + ", ".join(STREAM_REQUIRED_COLUMNS) + " (phase_name must match an "
            "existing phase on that run). Optional columns: " + ", ".join(STREAM_OPTIONAL_COLUMNS)
        )
        uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="stream_upload")
        if uploaded:
            try:
                df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
            except Exception as exc:
                st.error(f"Could not read file: {exc}")
                df = None

            if df is not None:
                missing_cols = [c for c in STREAM_REQUIRED_COLUMNS if c not in df.columns]
                if missing_cols:
                    st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
                else:
                    good_rows, bad_rows, resolved_phase_ids = [], [], []
                    for _, row in df.iterrows():
                        match = next(
                            (
                                p for p in all_phases_for_form
                                if p.production_run_id == row.get("production_run_id")
                                and p.phase_name == row.get("phase_name")
                            ),
                            None,
                        )
                        if match and row.get("stream_name"):
                            good_rows.append(row)
                            resolved_phase_ids.append(match.id)
                        else:
                            bad_rows.append(row)

                    st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
                    if bad_rows:
                        st.warning(
                            "Flagged rows reference a production_run_id/phase_name combination with no "
                            "matching phase, or are missing stream_name."
                        )
                        st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

                    if good_rows and st.button("Confirm import", key="confirm_stream_import"):
                        for row, phase_id in zip(good_rows, resolved_phase_ids):
                            session.add(
                                ComponentStreamReading(
                                    production_phase_id=phase_id,
                                    stream_name=str(row["stream_name"]),
                                    flow_unit=str(row.get("flow_unit", "") or "kg/min"),
                                    flow_setpoint=row.get("flow_setpoint"),
                                    flow_actual_mean=row.get("flow_actual_mean"),
                                    flow_actual_min=row.get("flow_actual_min"),
                                    flow_actual_max=row.get("flow_actual_max"),
                                    flow_actual_sd=row.get("flow_actual_sd"),
                                    pressure_actual_mean_bar=row.get("pressure_actual_mean_bar"),
                                    temperature_setpoint_c=row.get("temperature_setpoint_c"),
                                    temperature_actual_mean_c=row.get("temperature_actual_mean_c"),
                                    notes=str(row.get("notes", "") or ""),
                                    source_file_reference=uploaded.name,
                                )
                            )
                        session.commit()
                        st.success(f"Imported {len(good_rows)} stream reading(s) from {uploaded.name}.")
                        st.rerun()

    recent_streams = (
        session.query(ComponentStreamReading).order_by(ComponentStreamReading.id.desc()).limit(30).all()
    )
    if recent_streams:
        with st.expander(f"Recent stream readings ({len(recent_streams)} shown, max 30)"):
            st.dataframe(
                [
                    {
                        "Phase": r.phase.phase_name if r.phase else "—",
                        "Stream": r.stream_name,
                        "Flow sp": r.flow_setpoint,
                        "Flow actual mean": r.flow_actual_mean,
                        "Unit": r.flow_unit,
                        "Pressure (bar)": r.pressure_actual_mean_bar,
                        "Temp actual (°C)": r.temperature_actual_mean_c,
                    }
                    for r in recent_streams
                ],
                hide_index=True,
                use_container_width=True,
            )

# ---------------------------------------------------------------------------
# Production events (alarms / interventions / grade changes)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🚨 Production events")
st.caption(
    "Alarms, manual interventions, grade changes, and planned/unplanned pauses. This log is what "
    "explains outliers and lets transition material be excluded from steady-state analysis."
)

if runs:
    tab_manual, tab_import = st.tabs(["Manual entry", "CSV / Excel import"])

    with tab_manual:
        run = st.selectbox(
            "Production run *",
            runs,
            format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
            key="event_run_select",
        )
        phases_for_run = (
            session.query(ProductionPhase).filter(ProductionPhase.production_run_id == run.id).all()
            if run
            else []
        )
        with st.form("add_event"):
            event_type = st.selectbox("Event type *", EVENT_TYPES)
            severity = st.selectbox("Severity", [""] + SEVERITIES)
            phase = st.selectbox(
                "Phase (optional)",
                [None] + phases_for_run,
                format_func=lambda p: "—" if p is None else f"{p.phase_name} ({p.phase_start})",
            )
            event_ts = combine_date_time("Event time", "event_ts")
            description = st.text_area("Description")
            action_taken = st.text_area("Action taken")

            submitted = st.form_submit_button("Save event")
            if submitted:
                session.add(
                    ProductionEvent(
                        production_run_id=run.id,
                        production_phase_id=phase.id if phase else None,
                        event_ts=event_ts,
                        event_type=event_type,
                        severity=severity or None,
                        description=description,
                        action_taken=action_taken,
                        source_file_reference="manual entry",
                    )
                )
                session.commit()
                st.success("Event logged.")
                st.rerun()

    with tab_import:
        st.caption(
            "Required columns: " + ", ".join(EVENT_REQUIRED_COLUMNS) + ". Optional columns: "
            + ", ".join(EVENT_OPTIONAL_COLUMNS) + " (phase_name must match an existing phase on that run if given)."
        )
        uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="event_upload")
        if uploaded:
            try:
                df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
            except Exception as exc:
                st.error(f"Could not read file: {exc}")
                df = None

            if df is not None:
                missing_cols = [c for c in EVENT_REQUIRED_COLUMNS if c not in df.columns]
                if missing_cols:
                    st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
                else:
                    valid_run_ids = {r.id for r in runs}
                    all_phases_lookup = session.query(ProductionPhase).all()
                    good_rows, bad_rows, resolved_phase_ids = [], [], []
                    for _, row in df.iterrows():
                        run_ok = row.get("production_run_id") in valid_run_ids
                        ts = parse_dt(row.get("event_ts"))
                        if run_ok and row.get("event_type") in EVENT_TYPES and ts is not None:
                            phase_match = next(
                                (
                                    p for p in all_phases_lookup
                                    if p.production_run_id == row.get("production_run_id")
                                    and p.phase_name == row.get("phase_name")
                                ),
                                None,
                            )
                            good_rows.append(row)
                            resolved_phase_ids.append(phase_match.id if phase_match else None)
                        else:
                            bad_rows.append(row)

                    st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
                    if bad_rows:
                        st.warning(
                            "Flagged rows have an unknown production_run_id, an event_type outside "
                            f"the controlled list ({', '.join(EVENT_TYPES)}), or an unparseable event_ts."
                        )
                        st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

                    if good_rows and st.button("Confirm import", key="confirm_event_import"):
                        for row, phase_id in zip(good_rows, resolved_phase_ids):
                            session.add(
                                ProductionEvent(
                                    production_run_id=int(row["production_run_id"]),
                                    production_phase_id=phase_id,
                                    event_ts=parse_dt(row.get("event_ts")),
                                    event_type=row["event_type"],
                                    severity=str(row.get("severity", "") or "") or None,
                                    description=str(row.get("description", "") or ""),
                                    action_taken=str(row.get("action_taken", "") or ""),
                                    source_file_reference=uploaded.name,
                                )
                            )
                        session.commit()
                        st.success(f"Imported {len(good_rows)} event(s) from {uploaded.name}.")
                        st.rerun()

    recent_events = session.query(ProductionEvent).order_by(ProductionEvent.event_ts.desc()).limit(30).all()
    if recent_events:
        severity_icon = {"Low": "🟡", "Medium": "🟠", "High": "🔴"}
        with st.expander(f"Recent events ({len(recent_events)} shown, max 30)", expanded=False):
            st.dataframe(
                [
                    {
                        "Run": e.production_run_id,
                        "Time": e.event_ts,
                        "Type": e.event_type,
                        "Severity": severity_icon.get(e.severity, "") + " " + (e.severity or "") if e.severity else "",
                        "Description": e.description,
                        "Action taken": e.action_taken,
                    }
                    for e in recent_events
                ],
                hide_index=True,
                use_container_width=True,
            )
else:
    st.info("Create a production run above before logging events.")

# ---------------------------------------------------------------------------
# Raw material lots used
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📦 Raw material lots used")
st.caption(
    "The supplier lot actually consumed per stream during a run. Needed to explain density, cure, "
    "and cell-structure drift traced back to raw-material lot variation."
)

if runs:
    tab_manual, tab_import = st.tabs(["Manual entry", "CSV / Excel import"])

    with tab_manual:
        with st.form("add_lot_use"):
            run = st.selectbox(
                "Production run *",
                runs,
                format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
                key="lot_run_select",
            )
            component_stream_name = st.text_input("Component stream name * (e.g. Polyol A, TDI 80/20)")
            supplier_lot_no = st.text_input("Supplier lot number *")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save lot use")
            if submitted:
                if not component_stream_name or not supplier_lot_no:
                    st.error("Component stream name and supplier lot number are required.")
                else:
                    session.add(
                        RawMaterialLotUse(
                            production_run_id=run.id,
                            component_stream_name=component_stream_name,
                            supplier_lot_no=supplier_lot_no,
                            notes=notes,
                            source_file_reference="manual entry",
                        )
                    )
                    session.commit()
                    st.success("Lot use saved.")
                    st.rerun()

    with tab_import:
        st.caption(
            "Required columns: " + ", ".join(LOT_REQUIRED_COLUMNS) + ". Optional columns: "
            + ", ".join(LOT_OPTIONAL_COLUMNS)
        )
        uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="lot_upload")
        if uploaded:
            try:
                df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
            except Exception as exc:
                st.error(f"Could not read file: {exc}")
                df = None

            if df is not None:
                missing_cols = [c for c in LOT_REQUIRED_COLUMNS if c not in df.columns]
                if missing_cols:
                    st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
                else:
                    valid_run_ids = {r.id for r in runs}
                    good_rows, bad_rows = [], []
                    for _, row in df.iterrows():
                        if (
                            row.get("production_run_id") in valid_run_ids
                            and row.get("component_stream_name")
                            and row.get("supplier_lot_no")
                        ):
                            good_rows.append(row)
                        else:
                            bad_rows.append(row)

                    st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
                    if bad_rows:
                        st.warning("Flagged rows have an unknown production_run_id or missing required fields.")
                        st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

                    if good_rows and st.button("Confirm import", key="confirm_lot_import"):
                        for row in good_rows:
                            session.add(
                                RawMaterialLotUse(
                                    production_run_id=int(row["production_run_id"]),
                                    component_stream_name=str(row["component_stream_name"]),
                                    supplier_lot_no=str(row["supplier_lot_no"]),
                                    notes=str(row.get("notes", "") or ""),
                                    source_file_reference=uploaded.name,
                                )
                            )
                        session.commit()
                        st.success(f"Imported {len(good_rows)} lot use record(s) from {uploaded.name}.")
                        st.rerun()

    recent_lots = session.query(RawMaterialLotUse).order_by(RawMaterialLotUse.id.desc()).limit(30).all()
    if recent_lots:
        with st.expander(f"Recent lot uses ({len(recent_lots)} shown, max 30)"):
            st.dataframe(
                [
                    {
                        "Run": lot.production_run_id,
                        "Stream": lot.component_stream_name,
                        "Supplier lot": lot.supplier_lot_no,
                        "Notes": lot.notes,
                    }
                    for lot in recent_lots
                ],
                hide_index=True,
                use_container_width=True,
            )
else:
    st.info("Create a production run above before recording raw material lots.")

# ---------------------------------------------------------------------------
# Runtime data (ambient conditions, line speed — kept from v0.1)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Runtime data")
st.caption(
    "Ambient and line-speed conditions for a production run. Manual entry, or "
    "structured CSV/Excel import."
)

if runs:
    tab_manual, tab_import = st.tabs(["Manual entry", "CSV / Excel import"])

    with tab_manual:
        with st.form("add_runtime_manual"):
            run = st.selectbox(
                "Production run *",
                runs,
                format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
                key="runtime_run_select",
            )
            c1, c2, c3 = st.columns(3)
            line_speed = c1.number_input("Line speed", min_value=0.0, step=0.1)
            ambient_temp = c2.number_input("Ambient temperature (°C)", step=0.1)
            ambient_humidity = c3.number_input("Ambient humidity (%)", min_value=0.0, max_value=100.0, step=0.5)
            pump_speed = st.text_input("Pump speed / flow data")
            temperature_data = st.text_input("Temperature data")
            pressure_data = st.text_input("Pressure data (where available)")
            rise_time = st.number_input("Rise time (s)", min_value=0.0, step=1.0)
            curing_notes = st.text_area("Curing / cutting timing notes")
            submitted = st.form_submit_button("Save runtime data")
            if submitted:
                session.add(
                    RuntimeDataRecord(
                        production_run_id=run.id,
                        line_speed=line_speed or None,
                        pump_speed_or_flow_data=pump_speed,
                        temperature_data=temperature_data,
                        pressure_data=pressure_data,
                        ambient_temperature=ambient_temp or None,
                        ambient_humidity=ambient_humidity or None,
                        rise_time=rise_time or None,
                        curing_notes=curing_notes,
                        source_file_reference="manual entry",
                    )
                )
                session.commit()
                st.success("Runtime data saved.")
                st.rerun()

    with tab_import:
        st.caption(
            "Required column: `production_run_id`. Optional columns: "
            + ", ".join(RUNTIME_OPTIONAL_COLUMNS)
        )
        uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="runtime_upload")
        if uploaded:
            try:
                if uploaded.name.endswith(".csv"):
                    df = pd.read_csv(uploaded)
                else:
                    df = pd.read_excel(uploaded)
            except Exception as exc:
                st.error(f"Could not read file: {exc}")
                df = None

            if df is not None:
                missing_cols = [c for c in RUNTIME_REQUIRED_COLUMNS if c not in df.columns]
                if missing_cols:
                    st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
                else:
                    valid_run_ids = {r.id for r in runs}
                    good_rows, bad_rows = [], []
                    for _, row in df.iterrows():
                        if row.get("production_run_id") in valid_run_ids:
                            good_rows.append(row)
                        else:
                            bad_rows.append(row)

                    st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
                    if bad_rows:
                        st.warning("Flagged rows reference a production_run_id that does not exist and will be skipped.")
                        st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

                    if good_rows and st.button("Confirm import", key="confirm_runtime_import"):
                        for row in good_rows:
                            session.add(
                                RuntimeDataRecord(
                                    production_run_id=int(row["production_run_id"]),
                                    line_speed=row.get("line_speed"),
                                    pump_speed_or_flow_data=str(row.get("pump_speed_or_flow_data", "") or ""),
                                    temperature_data=str(row.get("temperature_data", "") or ""),
                                    pressure_data=str(row.get("pressure_data", "") or ""),
                                    ambient_temperature=row.get("ambient_temperature"),
                                    ambient_humidity=row.get("ambient_humidity"),
                                    rise_time=row.get("rise_time"),
                                    curing_notes=str(row.get("curing_notes", "") or ""),
                                    source_file_reference=uploaded.name,
                                )
                            )
                        session.commit()
                        st.success(f"Imported {len(good_rows)} runtime data row(s) from {uploaded.name}.")
                        st.rerun()
else:
    st.info("Create a production run above before adding runtime data.")

show_advisory_footer()
