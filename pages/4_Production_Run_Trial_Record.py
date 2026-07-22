"""Screen 5: Production Run

The primary, self-sufficient record of a batch: recipe used, machine
parameters, and (elsewhere) the quality results it produced. This is
routine, everyday data entry - it does NOT require framing a run as an
experiment. If a run is a deliberate trial/change investigation, flag it
as one separately on the Trial / Experiment page; that is optional and
most runs never need it.

Includes the Mandatory-tier process-data capture recommended in "Expanding
PI3 Plant Edition Production-Trial Data Capture for Polyurethane Foaming
Lines", adapted to what's actually capturable without a live PLC/OPC UA/MQTT
link or a machine data export/import: each run gets exactly two phase
snapshots, Setup (planned/configured before the run) and Finalized (what
actually happened, entered at shutdown).

Laid out as one tab per function: Production Runs (overview/edit/delete +
create), Process Phases, Component Stream Readings, Production Events, and
Runtime Data. Every function tab (other than Production Runs) opens with a
production-run selector shared with the other tabs, shows a clickable table
of that run's related records with edit + delete, and keeps CSV/Excel bulk
import as its own sub-tab. Raw material lot tracking has been removed from
this page (not workable — batches get mixed in tanks); the underlying table
is left untouched in the schema.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from auth import logout_button, require_login
from db import (
    EVENT_TYPES,
    PHASE_NAMES,
    SEVERITIES,
    AdjustmentConclusion,
    ApprovalRecord,
    ComponentStreamReading,
    ConditioningSegment,
    FallplateSectionPosition,
    FoamGrade,
    Machine,
    PhysicalPropertyResult,
    ProductionEvent,
    ProductionPhase,
    ProductionRun,
    QualityObservation,
    RawMaterialLotUse,
    RecipeComponent,
    RecipeVersion,
    RuntimeDataRecord,
    Sample,
    get_session,
    init_db,
)
from helpers import combine_date_time, csv_excel_uploader, page_setup, parse_dt

RUN_REQUIRED_COLUMNS = ["foam_grade_id", "recipe_version_id"]
RUN_OPTIONAL_COLUMNS = [
    "machine_id", "run_date", "batch_reference", "block_reference",
    "operator_or_team_reference", "notes",
]

RUNTIME_REQUIRED_COLUMNS = ["production_run_id"]
RUNTIME_OPTIONAL_COLUMNS = [
    "line_speed",
    "temperature_data",
    "pressure_data",
    "ambient_temperature",
    "ambient_humidity",
    "rise_time",
    "curing_notes",
]

PHASE_REQUIRED_COLUMNS = ["production_run_id", "phase_name"]
PHASE_OPTIONAL_COLUMNS = [
    "phase_start", "phase_end",
    "mixer_rpm", "conveyor_speed", "air_injection_rate", "air_pressure_bar",
    "ratio_index", "laydown_mode", "section_positions_note",
    "sidewall_width_mm", "foam_height_mm", "notes",
]

# Component stream readings are actual measurements taken once production is
# running, so they only ever attach to a run's Finalized phase — never to
# Setup (which is the planned/configured snapshot before the run starts).
# phase_name is therefore not part of the import contract; the Finalized
# phase for the run is resolved automatically.
STREAM_REQUIRED_COLUMNS = ["production_run_id", "stream_name"]
STREAM_OPTIONAL_COLUMNS = [
    "flow_unit", "flow", "pump_speed", "flow_total_qty", "pressure_bar", "temperature_c",
    "calibration_status", "calibration_note", "notes",
]

FALLPLATE_REQUIRED_COLUMNS = ["production_run_id", "phase_name", "section_number"]
FALLPLATE_OPTIONAL_COLUMNS = ["position_mm", "angle_deg", "notes"]

EVENT_REQUIRED_COLUMNS = ["production_run_id", "event_type", "event_ts"]
EVENT_OPTIONAL_COLUMNS = ["phase_name", "severity", "description", "action_taken"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run_label(r):
    return f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}"


def _selection_rows(event):
    """Best-effort extraction of selected row indices from a
    st.dataframe(..., on_select="rerun") return value, tolerant of the
    exact attribute/dict shape Streamlit uses."""
    if event is None:
        return []
    sel = getattr(event, "selection", None)
    if sel is None:
        try:
            sel = event["selection"]
        except Exception:
            return []
    rows = getattr(sel, "rows", None)
    if rows is None:
        try:
            rows = sel["rows"]
        except Exception:
            return []
    return list(rows or [])


def _clickable_table(rows, key):
    """Render rows (list of dicts) as a single-row-selectable table. Returns
    the selected row's index, or None if nothing is selected."""
    if not rows:
        return None
    event = st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    sel = _selection_rows(event)
    return sel[0] if sel else None


def _run_selector(runs, key):
    """Selectbox defaulting to the run selected elsewhere on the page
    (st.session_state['pr_selected_run_id']), keeping every tab in sync."""
    default_id = st.session_state.get("pr_selected_run_id")
    default_index = 0
    if default_id is not None:
        ids = [r.id for r in runs]
        if default_id in ids:
            default_index = ids.index(default_id)
    run = st.selectbox(
        "Production run *",
        runs,
        index=default_index,
        format_func=_run_label,
        key=key,
    )
    if run is not None:
        st.session_state["pr_selected_run_id"] = run.id
    return run


def _delete_with_confirm(label, on_confirm, key_prefix, extra_warning=""):
    st.markdown(f"**Delete {label}**")
    if extra_warning:
        st.warning(extra_warning)
    confirm = st.checkbox(f"I understand — permanently delete {label}.", key=f"{key_prefix}_confirm")
    if st.button(f"Delete {label}", key=f"{key_prefix}_btn", type="primary", disabled=not confirm):
        on_confirm()
        st.success(f"{label} deleted.")
        st.rerun()


# --- Production run cascade delete (a run can have a lot hanging off it) ---

def _production_run_dependency_counts(session, run_id):
    phase_ids = [
        p.id for p in session.query(ProductionPhase.id)
        .filter(ProductionPhase.production_run_id == run_id).all()
    ]
    sample_ids = [
        s.id for s in session.query(Sample.id)
        .filter(Sample.production_run_id == run_id).all()
    ]
    return {
        "process phase(s)": len(phase_ids),
        "component stream reading(s)": (
            session.query(ComponentStreamReading)
            .filter(ComponentStreamReading.production_phase_id.in_(phase_ids)).count()
            if phase_ids else 0
        ),
        "fall-plate section position(s)": (
            session.query(FallplateSectionPosition)
            .filter(FallplateSectionPosition.production_phase_id.in_(phase_ids)).count()
            if phase_ids else 0
        ),
        "production event(s)": session.query(ProductionEvent)
        .filter(ProductionEvent.production_run_id == run_id).count(),
        "raw material lot use(s)": session.query(RawMaterialLotUse)
        .filter(RawMaterialLotUse.production_run_id == run_id).count(),
        "runtime data record(s)": session.query(RuntimeDataRecord)
        .filter(RuntimeDataRecord.production_run_id == run_id).count(),
        "quality test result(s)": session.query(PhysicalPropertyResult)
        .filter(PhysicalPropertyResult.production_run_id == run_id).count(),
        "quality issue(s)": session.query(QualityObservation)
        .filter(QualityObservation.production_run_id == run_id).count(),
        "adjustment & conclusion record(s)": session.query(AdjustmentConclusion)
        .filter(AdjustmentConclusion.production_run_id == run_id).count(),
        "approval record(s)": session.query(ApprovalRecord)
        .filter(ApprovalRecord.production_run_id == run_id).count(),
        "sample(s)": len(sample_ids),
        "conditioning segment(s)": (
            session.query(ConditioningSegment)
            .filter(ConditioningSegment.sample_id.in_(sample_ids)).count()
            if sample_ids else 0
        ),
    }


def _delete_production_run_cascade(session, run):
    run_id = run.id
    phase_ids = [
        p.id for p in session.query(ProductionPhase.id)
        .filter(ProductionPhase.production_run_id == run_id).all()
    ]
    sample_ids = [
        s.id for s in session.query(Sample.id)
        .filter(Sample.production_run_id == run_id).all()
    ]

    if phase_ids:
        session.query(ComponentStreamReading).filter(
            ComponentStreamReading.production_phase_id.in_(phase_ids)
        ).delete(synchronize_session=False)
        session.query(FallplateSectionPosition).filter(
            FallplateSectionPosition.production_phase_id.in_(phase_ids)
        ).delete(synchronize_session=False)
    if sample_ids:
        session.query(ConditioningSegment).filter(
            ConditioningSegment.sample_id.in_(sample_ids)
        ).delete(synchronize_session=False)

    session.query(ProductionEvent).filter(
        ProductionEvent.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(ProductionPhase).filter(
        ProductionPhase.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(RawMaterialLotUse).filter(
        RawMaterialLotUse.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(RuntimeDataRecord).filter(
        RuntimeDataRecord.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(PhysicalPropertyResult).filter(
        PhysicalPropertyResult.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(QualityObservation).filter(
        QualityObservation.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(AdjustmentConclusion).filter(
        AdjustmentConclusion.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(ApprovalRecord).filter(
        ApprovalRecord.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(Sample).filter(
        Sample.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(ProductionRun).filter(ProductionRun.id == run_id).delete(synchronize_session=False)
    session.commit()


def _delete_phase_cascade(session, phase):
    phase_id = phase.id
    session.query(ComponentStreamReading).filter(
        ComponentStreamReading.production_phase_id == phase_id
    ).delete(synchronize_session=False)
    session.query(FallplateSectionPosition).filter(
        FallplateSectionPosition.production_phase_id == phase_id
    ).delete(synchronize_session=False)
    session.query(ProductionEvent).filter(
        ProductionEvent.production_phase_id == phase_id
    ).update({"production_phase_id": None}, synchronize_session=False)
    session.query(ProductionPhase).filter(ProductionPhase.id == phase_id).delete(synchronize_session=False)
    session.commit()


page_setup("Production Run")
init_db()
require_login()
logout_button()

st.title("Production Run")
st.caption(
    "Every batch made goes here: recipe used, machine parameters, and quality results. "
    "This is routine data entry, not an experiment log — if this particular run is a "
    "deliberate trial or change investigation, flag it as one on the Trial / Experiment page."
)
session = get_session()

grades = session.query(FoamGrade).all()
if not grades:
    st.warning("Add a foam grade and recipe version first.")
    st.stop()

runs = session.query(ProductionRun).order_by(ProductionRun.created_at.desc()).all()

tab_runs, tab_phases, tab_streams, tab_events, tab_runtime = st.tabs(
    [
        "📋 Production Runs",
        "⏱️ Process Phases",
        "🧪 Component Stream Readings",
        "🚨 Production Events",
        "📊 Runtime Data",
    ]
)

# ---------------------------------------------------------------------------
# Production Runs — overview/edit/delete + create
# ---------------------------------------------------------------------------
with tab_runs:
    sub_overview, sub_create = st.tabs(["Overview", "Create Production Run"])

    with sub_overview:
        if not runs:
            st.info("No production runs yet — use the Create Production Run tab.")
        else:
            run_rows = [
                {
                    "Run": r.id,
                    "Grade": r.foam_grade.grade_name,
                    "Recipe": r.recipe_version.version_label,
                    "Date": r.run_date,
                    "Batch": r.batch_reference,
                    "Block": r.block_reference,
                    "Machine": r.machine.name if r.machine else "—",
                    "Operator": r.operator_or_team_reference,
                }
                for r in runs
            ]
            st.caption(f"{len(runs)} production run(s). Click a row to edit (and optionally delete) that run.")
            idx = _clickable_table(run_rows, key="runs_overview_table")
            if idx is not None:
                st.session_state["pr_selected_run_id"] = runs[idx].id

            selected_run_id = st.session_state.get("pr_selected_run_id")
            selected_run = next((r for r in runs if r.id == selected_run_id), None)

            if selected_run:
                st.divider()
                st.markdown(f"#### Edit Run #{selected_run.id}")
                with st.form(f"edit_run_form_{selected_run.id}"):
                    grade_idx = next((i for i, g in enumerate(grades) if g.id == selected_run.foam_grade_id), 0)
                    grade = st.selectbox(
                        "Foam grade *", grades, index=grade_idx,
                        format_func=lambda g: g.grade_name, key=f"edit_run_grade_{selected_run.id}",
                    )
                    versions = (
                        session.query(RecipeVersion).filter(RecipeVersion.foam_grade_id == grade.id).all()
                        if grade else []
                    )
                    version_idx = 0
                    if versions:
                        version_idx = next(
                            (i for i, v in enumerate(versions) if v.id == selected_run.recipe_version_id), 0
                        )
                    recipe_version = st.selectbox(
                        "Recipe version *", versions, index=version_idx,
                        format_func=lambda v: v.version_label if v else "—",
                        key=f"edit_run_version_{selected_run.id}",
                    )
                    machines_for_plant = (
                        session.query(Machine)
                        .filter(Machine.plant_id == grade.product_family.plant_id, Machine.active.is_(True))
                        .all()
                        if grade else []
                    )
                    machine_options = [None] + machines_for_plant
                    machine_idx = next(
                        (i for i, m in enumerate(machine_options) if m is not None and m.id == selected_run.machine_id),
                        0,
                    )
                    machine = st.selectbox(
                        "Machine / foaming line", machine_options, index=machine_idx,
                        format_func=lambda m: "— not selected —" if m is None else f"{m.name} ({m.oem or 'OEM —'})",
                        key=f"edit_run_machine_{selected_run.id}",
                    )
                    run_date = st.date_input(
                        "Run date", value=selected_run.run_date or dt.date.today(),
                        key=f"edit_run_date_{selected_run.id}",
                    )
                    batch_reference = st.text_input(
                        "Batch reference", value=selected_run.batch_reference or "",
                        key=f"edit_run_batch_{selected_run.id}",
                    )
                    block_reference = st.text_input(
                        "Block reference", value=selected_run.block_reference or "",
                        key=f"edit_run_block_{selected_run.id}",
                    )
                    operator = st.text_input(
                        "Operator / team reference", value=selected_run.operator_or_team_reference or "",
                        key=f"edit_run_operator_{selected_run.id}",
                    )
                    notes = st.text_area(
                        "Notes", value=selected_run.notes or "", key=f"edit_run_notes_{selected_run.id}"
                    )
                    save = st.form_submit_button("Save changes")
                    if save:
                        if not recipe_version:
                            st.error("This foam grade has no recipe version yet — add one first.")
                        else:
                            selected_run.foam_grade_id = grade.id
                            selected_run.plant_id = grade.product_family.plant_id
                            selected_run.recipe_version_id = recipe_version.id
                            selected_run.machine_id = machine.id if machine else None
                            selected_run.run_date = run_date
                            selected_run.batch_reference = batch_reference
                            selected_run.block_reference = block_reference
                            selected_run.operator_or_team_reference = operator
                            selected_run.notes = notes
                            session.commit()
                            st.success("Production run updated.")
                            st.rerun()

                counts = _production_run_dependency_counts(session, selected_run.id)
                total_related = sum(counts.values())
                if total_related:
                    detail = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
                    warning = f"Deleting this run will also permanently delete {total_related} related record(s): {detail}."
                else:
                    warning = "This run has no related records — deleting it is safe."

                def _do_delete_run(_session=session, _run=selected_run):
                    _delete_production_run_cascade(_session, _run)
                    st.session_state.pop("pr_selected_run_id", None)

                _delete_with_confirm(
                    f"Run #{selected_run.id}", _do_delete_run, key_prefix=f"run_{selected_run.id}",
                    extra_warning=warning,
                )

                if st.button("Clear selection", key="clear_run_selection"):
                    st.session_state.pop("pr_selected_run_id", None)
                    st.rerun()

    with sub_create:
        sub_manual, sub_import = st.tabs(["Manual entry", "CSV / Excel import"])

        with sub_manual:
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
                machines_for_plant = (
                    session.query(Machine)
                    .filter(Machine.plant_id == grade.product_family.plant_id, Machine.active.is_(True))
                    .all()
                    if grade
                    else []
                )
                machine = st.selectbox(
                    "Machine / foaming line" + ("" if machines_for_plant else " (none set up for this plant yet)"),
                    [None] + machines_for_plant,
                    format_func=lambda m: "— not selected —" if m is None else f"{m.name} ({m.oem or 'OEM —'})",
                )
                run_date = st.date_input("Run date", value=dt.date.today())
                batch_reference = st.text_input("Batch reference")
                block_reference = st.text_input("Block reference")
                operator = st.text_input("Operator / team reference")
                notes = st.text_area("Notes")

                submitted = st.form_submit_button("Save production run")
                if submitted:
                    if not recipe_version:
                        st.error("This foam grade has no recipe version yet — add one first.")
                    else:
                        run = ProductionRun(
                            plant_id=grade.product_family.plant_id,
                            foam_grade_id=grade.id,
                            recipe_version_id=recipe_version.id,
                            run_date=run_date,
                            batch_reference=batch_reference,
                            block_reference=block_reference,
                            machine_id=machine.id if machine else None,
                            operator_or_team_reference=operator,
                            notes=notes,
                        )
                        session.add(run)
                        session.commit()
                        st.session_state["pr_selected_run_id"] = run.id
                        st.success("Production run created.")
                        st.rerun()

        with sub_import:
            st.caption(
                "recipe_version_id must belong to the foam_grade_id on the same row. plant_id and machine "
                "assignment are derived/validated from the foam grade automatically."
            )
            run_df, run_filename = csv_excel_uploader(RUN_REQUIRED_COLUMNS, RUN_OPTIONAL_COLUMNS, key="run_upload")
            if run_df is not None:
                grades_by_id = {g.id: g for g in grades}
                versions_by_id = {v.id: v for v in session.query(RecipeVersion).all()}
                machines_by_id = {m.id: m for m in session.query(Machine).all()}
                good_rows, bad_rows = [], []
                for _, row in run_df.iterrows():
                    try:
                        grade_row = grades_by_id.get(row.get("foam_grade_id"))
                        version_row = versions_by_id.get(row.get("recipe_version_id"))
                        machine_val = row.get("machine_id")
                        machine_ok = pd.isna(machine_val) or int(machine_val) in machines_by_id
                        ok = bool(grade_row and version_row and version_row.foam_grade_id == grade_row.id and machine_ok)
                    except (TypeError, ValueError):
                        ok = False
                    if ok:
                        good_rows.append(row)
                    else:
                        bad_rows.append(row)

                st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
                if bad_rows:
                    st.warning(
                        "Flagged rows reference an unknown foam_grade_id/recipe_version_id, a recipe version "
                        "that doesn't belong to that foam grade, or an unknown machine_id."
                    )
                    st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

                if good_rows and st.button("Confirm import", key="confirm_run_import"):
                    for row in good_rows:
                        grade_row = grades_by_id[row["foam_grade_id"]]
                        machine_val = row.get("machine_id")
                        run_date_val = pd.to_datetime(row.get("run_date"), errors="coerce")
                        session.add(
                            ProductionRun(
                                plant_id=grade_row.product_family.plant_id,
                                foam_grade_id=grade_row.id,
                                recipe_version_id=int(row["recipe_version_id"]),
                                run_date=run_date_val.date() if not pd.isna(run_date_val) else dt.date.today(),
                                batch_reference=str(row.get("batch_reference", "") or ""),
                                block_reference=str(row.get("block_reference", "") or ""),
                                machine_id=int(machine_val) if not pd.isna(machine_val) else None,
                                operator_or_team_reference=str(row.get("operator_or_team_reference", "") or ""),
                                notes=str(row.get("notes", "") or ""),
                            )
                        )
                    session.commit()
                    st.success(f"Imported {len(good_rows)} production run(s) from {run_filename}.")
                    st.rerun()

# ---------------------------------------------------------------------------
# Process phases
# ---------------------------------------------------------------------------
with tab_phases:
    st.caption(
        "Two snapshots per run: **Setup** (planned/configured before the run) and **Finalized** "
        "(what actually happened, entered at shutdown). Enter the same fields at both points — "
        "comparing Setup to Finalized for a run is the plan-vs-actual read, no separate setpoint "
        "column needed."
    )

    if not runs:
        st.info("Create a production run first (Production Runs tab).")
    else:
        run = _run_selector(runs, key="phase_tab_run_select")
        st.caption(f"Showing phases for **{_run_label(run)}**")

        sub_overview, sub_create, sub_import, sub_fallplate = st.tabs(
            ["Overview & Edit", "Create", "CSV / Excel import", "Fall-plate positions"]
        )

        phases_for_run = (
            session.query(ProductionPhase)
            .filter(ProductionPhase.production_run_id == run.id)
            .order_by(ProductionPhase.phase_start)
            .all()
        )

        with sub_overview:
            if not phases_for_run:
                st.info("No phases recorded yet for this run — use the Create tab.")
            else:
                phase_rows = [
                    {
                        "Phase": p.phase_name,
                        "Start": p.phase_start,
                        "End": p.phase_end,
                        "Mixer rpm": p.mixer_rpm,
                        "Conveyor m/min": p.conveyor_speed,
                        "Ratio/index": p.ratio_index,
                        "Laydown mode": p.laydown_mode,
                    }
                    for p in phases_for_run
                ]
                idx = _clickable_table(phase_rows, key=f"phases_table_{run.id}")
                if idx is not None:
                    st.session_state["pr_selected_phase_id"] = phases_for_run[idx].id

                sel_phase = next(
                    (p for p in phases_for_run if p.id == st.session_state.get("pr_selected_phase_id")), None
                )
                if sel_phase:
                    st.markdown(f"##### Edit {sel_phase.phase_name} phase (Run #{run.id})")
                    with st.form(f"edit_phase_form_{sel_phase.id}"):
                        phase_name = st.selectbox(
                            "Phase *", PHASE_NAMES, index=PHASE_NAMES.index(sel_phase.phase_name),
                            key=f"edit_phase_name_{sel_phase.id}",
                        )
                        phase_start = combine_date_time(
                            "Phase start", f"edit_phase_start_{sel_phase.id}",
                            default_date=sel_phase.phase_start.date() if sel_phase.phase_start else None,
                            default_time=sel_phase.phase_start.time() if sel_phase.phase_start else None,
                        )
                        phase_end = combine_date_time(
                            "Phase end", f"edit_phase_end_{sel_phase.id}",
                            default_date=sel_phase.phase_end.date() if sel_phase.phase_end else None,
                            default_time=sel_phase.phase_end.time() if sel_phase.phase_end else None,
                        )

                        st.markdown("**Machine settings for this phase**")
                        c1, c2, c3, c4 = st.columns(4)
                        mixer_rpm = c1.number_input(
                            "Mixer rpm", min_value=0.0, step=1.0, value=float(sel_phase.mixer_rpm or 0.0),
                            key=f"edit_phase_mixer_{sel_phase.id}",
                        )
                        conveyor_speed = c2.number_input(
                            "Conveyor speed (m/min)", min_value=0.0, step=0.01,
                            value=float(sel_phase.conveyor_speed or 0.0), key=f"edit_phase_conveyor_{sel_phase.id}",
                        )
                        air_injection_rate = c3.number_input(
                            "Air injection rate", min_value=0.0, step=0.1,
                            value=float(sel_phase.air_injection_rate or 0.0), key=f"edit_phase_air_inj_{sel_phase.id}",
                        )
                        air_pressure_bar = c4.number_input(
                            "Air pressure (bar)", min_value=0.0, step=0.05,
                            value=float(sel_phase.air_pressure_bar or 0.0), key=f"edit_phase_air_pres_{sel_phase.id}",
                        )

                        c5, c6, c7 = st.columns(3)
                        sidewall_width_mm = c5.number_input(
                            "Sidewall width (mm)", min_value=0.0, step=1.0,
                            value=float(sel_phase.sidewall_width_mm or 0.0), key=f"edit_phase_sidewall_{sel_phase.id}",
                        )
                        foam_height_mm = c6.number_input(
                            "Foam height (mm)", min_value=0.0, step=1.0,
                            value=float(sel_phase.foam_height_mm or 0.0), key=f"edit_phase_height_{sel_phase.id}",
                        )
                        ratio_index = c7.number_input(
                            "Ratio / index", min_value=0.0, step=0.1,
                            value=float(sel_phase.ratio_index or 0.0), key=f"edit_phase_ratio_{sel_phase.id}",
                            help="Stoichiometric ratio/index for this phase.",
                        )

                        laydown_mode = st.text_input(
                            "Laydown mode (e.g. trough, fall-plate, liquid laydown, traversing)",
                            value=sel_phase.laydown_mode or "", key=f"edit_phase_laydown_{sel_phase.id}",
                        )
                        section_positions_note = st.text_area(
                            "Other geometry notes (structured fall-plate section positions are entered in the "
                            "Fall-plate positions sub-tab)",
                            value=sel_phase.section_positions_note or "", key=f"edit_phase_geom_note_{sel_phase.id}",
                        )
                        notes = st.text_area(
                            "Phase notes", value=sel_phase.notes or "", key=f"edit_phase_notes_{sel_phase.id}"
                        )

                        save = st.form_submit_button("Save changes")
                        if save:
                            if phase_end < phase_start:
                                st.error("Phase end must not be before phase start.")
                            else:
                                sel_phase.phase_name = phase_name
                                sel_phase.phase_start = phase_start
                                sel_phase.phase_end = phase_end
                                sel_phase.mixer_rpm = mixer_rpm or None
                                sel_phase.conveyor_speed = conveyor_speed or None
                                sel_phase.air_injection_rate = air_injection_rate or None
                                sel_phase.air_pressure_bar = air_pressure_bar or None
                                sel_phase.ratio_index = ratio_index or None
                                sel_phase.laydown_mode = laydown_mode
                                sel_phase.section_positions_note = section_positions_note
                                sel_phase.sidewall_width_mm = sidewall_width_mm or None
                                sel_phase.foam_height_mm = foam_height_mm or None
                                sel_phase.notes = notes
                                session.commit()
                                st.success("Phase updated.")
                                st.rerun()

                    def _do_delete_phase(_session=session, _phase=sel_phase):
                        _delete_phase_cascade(_session, _phase)
                        st.session_state.pop("pr_selected_phase_id", None)

                    _delete_with_confirm(
                        f"{sel_phase.phase_name} phase (Run #{run.id})", _do_delete_phase,
                        key_prefix=f"phase_{sel_phase.id}",
                        extra_warning=(
                            "Deleting this phase also deletes its component stream readings and fall-plate "
                            "section positions, and unlinks (does not delete) any production events that "
                            "referenced it."
                        ),
                    )
                else:
                    st.caption("Click a row above to edit (and optionally delete) that phase.")

        with sub_create:
            with st.form(f"add_phase_{run.id}"):
                phase_name = st.selectbox("Phase *", PHASE_NAMES, key=f"new_phase_name_{run.id}")
                phase_start = combine_date_time("Phase start", f"new_phase_start_{run.id}")
                phase_end = combine_date_time("Phase end", f"new_phase_end_{run.id}")

                st.markdown("**Machine settings for this phase**")
                c1, c2, c3, c4 = st.columns(4)
                mixer_rpm = c1.number_input("Mixer rpm", min_value=0.0, step=1.0, key=f"new_phase_mixer_{run.id}")
                conveyor_speed = c2.number_input(
                    "Conveyor speed (m/min)", min_value=0.0, step=0.01, key=f"new_phase_conveyor_{run.id}"
                )
                air_injection_rate = c3.number_input(
                    "Air injection rate", min_value=0.0, step=0.1, key=f"new_phase_air_inj_{run.id}"
                )
                air_pressure_bar = c4.number_input(
                    "Air pressure (bar)", min_value=0.0, step=0.05, key=f"new_phase_air_pres_{run.id}"
                )

                c5, c6, c7 = st.columns(3)
                sidewall_width_mm = c5.number_input(
                    "Sidewall width (mm)", min_value=0.0, step=1.0, key=f"new_phase_sidewall_{run.id}"
                )
                foam_height_mm = c6.number_input(
                    "Foam height (mm)", min_value=0.0, step=1.0, key=f"new_phase_height_{run.id}"
                )
                ratio_index = c7.number_input(
                    "Ratio / index", min_value=0.0, step=0.1, key=f"new_phase_ratio_{run.id}",
                    help="Stoichiometric ratio/index for this phase. Enter the intended value on the Setup "
                    "row and the reconstructed actual value on the Finalized row — comparing the two is the "
                    "single strongest diagnostic for explaining density/compression/cure drift.",
                )

                laydown_mode = st.text_input(
                    "Laydown mode (e.g. trough, fall-plate, liquid laydown, traversing)",
                    key=f"new_phase_laydown_{run.id}",
                )
                section_positions_note = st.text_area(
                    "Other geometry notes (structured fall-plate section positions are entered in the "
                    "Fall-plate positions sub-tab)",
                    key=f"new_phase_geom_note_{run.id}",
                )
                notes = st.text_area("Phase notes", key=f"new_phase_notes_{run.id}")

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
                                mixer_rpm=mixer_rpm or None,
                                conveyor_speed=conveyor_speed or None,
                                air_injection_rate=air_injection_rate or None,
                                air_pressure_bar=air_pressure_bar or None,
                                ratio_index=ratio_index or None,
                                laydown_mode=laydown_mode,
                                section_positions_note=section_positions_note,
                                sidewall_width_mm=sidewall_width_mm or None,
                                foam_height_mm=foam_height_mm or None,
                                notes=notes,
                                source_file_reference="manual entry",
                            )
                        )
                        session.commit()
                        st.success("Phase saved.")
                        st.rerun()

        with sub_import:
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
                                        mixer_rpm=row.get("mixer_rpm"),
                                        conveyor_speed=row.get("conveyor_speed"),
                                        air_injection_rate=row.get("air_injection_rate"),
                                        air_pressure_bar=row.get("air_pressure_bar"),
                                        ratio_index=row.get("ratio_index"),
                                        laydown_mode=str(row.get("laydown_mode", "") or ""),
                                        section_positions_note=str(row.get("section_positions_note", "") or ""),
                                        sidewall_width_mm=row.get("sidewall_width_mm"),
                                        foam_height_mm=row.get("foam_height_mm"),
                                        notes=str(row.get("notes", "") or ""),
                                        source_file_reference=uploaded.name,
                                    )
                                )
                            session.commit()
                            st.success(f"Imported {len(good_rows)} phase(s) from {uploaded.name}.")
                            st.rerun()

        with sub_fallplate:
            st.caption(
                "Structured height/angle per section for fall-plate or pour-plate lines (typically 4-6 "
                "sections). Requires a phase to exist first."
            )
            if not phases_for_run:
                st.info("Add a phase for this run first (Create tab) before recording section positions.")
            else:
                sub_fp_manual, sub_fp_import = st.tabs(["Manual entry", "CSV / Excel import"])

                with sub_fp_manual:
                    phase_for_fp = st.selectbox(
                        "Phase *", phases_for_run,
                        format_func=lambda p: f"{p.phase_name} ({p.phase_start})",
                        key=f"fallplate_phase_select_{run.id}",
                    )
                    with st.form(f"add_fallplate_section_{run.id}"):
                        c1, c2 = st.columns(2)
                        section_number = c1.number_input("Section number *", min_value=1, step=1, value=1)
                        position_mm = c2.number_input("Position (mm above conveyor datum)", step=1.0)
                        angle_deg = st.number_input("Angle (degrees, optional)", step=0.5)
                        fp_notes = st.text_area("Notes", key=f"fp_notes_{run.id}")
                        submitted = st.form_submit_button("Save section position")
                        if submitted:
                            session.add(
                                FallplateSectionPosition(
                                    production_phase_id=phase_for_fp.id,
                                    section_number=int(section_number),
                                    position_mm=position_mm or None,
                                    angle_deg=angle_deg or None,
                                    notes=fp_notes,
                                )
                            )
                            session.commit()
                            st.success("Section position saved.")
                            st.rerun()

                with sub_fp_import:
                    st.caption(
                        "Required columns: " + ", ".join(FALLPLATE_REQUIRED_COLUMNS) + " (phase_name must match "
                        "an existing phase on that run). Optional columns: " + ", ".join(FALLPLATE_OPTIONAL_COLUMNS)
                    )
                    uploaded_fp = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="fallplate_upload")
                    if uploaded_fp:
                        try:
                            df_fp = (
                                pd.read_csv(uploaded_fp) if uploaded_fp.name.endswith(".csv")
                                else pd.read_excel(uploaded_fp)
                            )
                        except Exception as exc:
                            st.error(f"Could not read file: {exc}")
                            df_fp = None

                        if df_fp is not None:
                            missing_cols = [c for c in FALLPLATE_REQUIRED_COLUMNS if c not in df_fp.columns]
                            if missing_cols:
                                st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
                            else:
                                all_phases_lookup = session.query(ProductionPhase).all()
                                good_rows, bad_rows, resolved_phase_ids = [], [], []
                                for _, row in df_fp.iterrows():
                                    match = next(
                                        (
                                            p for p in all_phases_lookup
                                            if p.production_run_id == row.get("production_run_id")
                                            and p.phase_name == row.get("phase_name")
                                        ),
                                        None,
                                    )
                                    if match and row.get("section_number") is not None:
                                        good_rows.append(row)
                                        resolved_phase_ids.append(match.id)
                                    else:
                                        bad_rows.append(row)

                                st.write(
                                    f"Rows ready to import: **{len(good_rows)}** | "
                                    f"Rows flagged/rejected: **{len(bad_rows)}**"
                                )
                                if bad_rows:
                                    st.warning(
                                        "Flagged rows reference a production_run_id/phase_name combination with "
                                        "no matching phase, or are missing section_number."
                                    )
                                    st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

                                if good_rows and st.button("Confirm import", key="confirm_fallplate_import"):
                                    for row, phase_id in zip(good_rows, resolved_phase_ids):
                                        session.add(
                                            FallplateSectionPosition(
                                                production_phase_id=phase_id,
                                                section_number=int(row["section_number"]),
                                                position_mm=row.get("position_mm"),
                                                angle_deg=row.get("angle_deg"),
                                                notes=str(row.get("notes", "") or ""),
                                            )
                                        )
                                    session.commit()
                                    st.success(f"Imported {len(good_rows)} section position(s) from {uploaded_fp.name}.")
                                    st.rerun()

                recent_fp = (
                    session.query(FallplateSectionPosition)
                    .join(ProductionPhase)
                    .filter(ProductionPhase.production_run_id == run.id)
                    .order_by(FallplateSectionPosition.id.desc())
                    .all()
                )
                if recent_fp:
                    st.dataframe(
                        [
                            {
                                "Phase": fp.phase.phase_name if fp.phase else "—",
                                "Section": fp.section_number,
                                "Position (mm)": fp.position_mm,
                                "Angle (deg)": fp.angle_deg,
                                "Notes": fp.notes,
                            }
                            for fp in recent_fp
                        ],
                        hide_index=True,
                        use_container_width=True,
                    )

# ---------------------------------------------------------------------------
# Component stream readings
# ---------------------------------------------------------------------------
with tab_streams:
    st.caption(
        "Per raw-material stream (polyol, isocyanate, water/blowing agent, catalyst, etc.), the flow, "
        "pressure, and temperature for a given phase (Setup or Finalized). A phase must exist first."
    )

    if not runs:
        st.info("Create a production run first (Production Runs tab).")
    else:
        run = _run_selector(runs, key="stream_tab_run_select")
        phases_for_run = (
            session.query(ProductionPhase).filter(ProductionPhase.production_run_id == run.id).all()
        )
        finalized_phase = next((p for p in phases_for_run if p.phase_name == "Finalized"), None)
        if not finalized_phase:
            st.info(
                f"Add the Finalized phase for {_run_label(run)} first (Process Phases tab). Component "
                "stream readings are actual measurements, so they only ever attach to the Finalized "
                "phase, never to Setup."
            )
        else:
            st.caption(
                f"Showing stream readings for **{_run_label(run)}** — Finalized phase "
                f"({finalized_phase.phase_start})"
            )
            sub_overview, sub_create, sub_import = st.tabs(["Overview & Edit", "Create", "CSV / Excel import"])

            streams_for_run = (
                session.query(ComponentStreamReading)
                .join(ProductionPhase)
                .filter(ProductionPhase.production_run_id == run.id)
                .order_by(ComponentStreamReading.id.desc())
                .all()
            )
            recipe_components = (
                session.query(RecipeComponent)
                .filter(RecipeComponent.recipe_version_id == run.recipe_version_id)
                .all()
            )

            with sub_overview:
                if not streams_for_run:
                    st.info("No stream readings recorded yet for this run — use the Create tab.")
                else:
                    stream_rows = [
                        {
                            "Phase": r.phase.phase_name if r.phase else "—",
                            "Stream": r.stream_name,
                            "Pump speed": r.pump_speed,
                            "Flow": r.flow,
                            "Unit": r.flow_unit,
                            "Total delivered": r.flow_total_qty,
                            "Pressure (bar)": r.pressure_bar,
                            "Temp (°C)": r.temperature_c,
                            "Calibration": r.calibration_status or "—",
                        }
                        for r in streams_for_run
                    ]
                    idx = _clickable_table(stream_rows, key=f"streams_table_{run.id}")
                    if idx is not None:
                        st.session_state["pr_selected_stream_id"] = streams_for_run[idx].id

                    sel_stream = next(
                        (r for r in streams_for_run if r.id == st.session_state.get("pr_selected_stream_id")), None
                    )
                    if sel_stream:
                        st.markdown(f"##### Edit stream reading — {sel_stream.stream_name}")
                        st.caption("Phase: Finalized (component stream readings always attach here).")
                        with st.form(f"edit_stream_form_{sel_stream.id}"):
                            stream_name = st.text_input(
                                "Stream / raw material name *", value=sel_stream.stream_name,
                                key=f"edit_stream_name_{sel_stream.id}",
                            )
                            flow_unit_options = ["kg/min", "L/min"]
                            flow_unit_idx = (
                                flow_unit_options.index(sel_stream.flow_unit)
                                if sel_stream.flow_unit in flow_unit_options else 0
                            )
                            flow_unit = st.selectbox(
                                "Flow unit", flow_unit_options, index=flow_unit_idx,
                                key=f"edit_stream_flow_unit_{sel_stream.id}",
                            )
                            c1, c2, c3, c4 = st.columns(4)
                            flow = c1.number_input(
                                "Flow", min_value=0.0, step=0.1, value=float(sel_stream.flow or 0.0),
                                key=f"edit_stream_flow_{sel_stream.id}",
                            )
                            pump_speed = c2.number_input(
                                "Pump speed", min_value=0.0, step=0.1, value=float(sel_stream.pump_speed or 0.0),
                                key=f"edit_stream_pump_{sel_stream.id}",
                                help="Metering pump setting for this stream (RPM/Hz/% depending on OEM) — the "
                                "control input, distinct from the measured Flow.",
                            )
                            pressure_bar = c3.number_input(
                                "Pressure (bar)", min_value=0.0, step=0.1, value=float(sel_stream.pressure_bar or 0.0),
                                key=f"edit_stream_pressure_{sel_stream.id}",
                            )
                            temperature_c = c4.number_input(
                                "Temperature (°C)", step=0.1, value=float(sel_stream.temperature_c or 0.0),
                                key=f"edit_stream_temp_{sel_stream.id}",
                            )
                            flow_total_qty = st.number_input(
                                "Total delivered this phase (same base unit as flow unit, kg or L)",
                                min_value=0.0, step=0.1, value=float(sel_stream.flow_total_qty or 0.0),
                                key=f"edit_stream_total_{sel_stream.id}",
                            )
                            c5, c6 = st.columns(2)
                            calibration_options = ["", "Valid", "Expired", "Failed", "Not Verified"]
                            calibration_idx = (
                                calibration_options.index(sel_stream.calibration_status)
                                if sel_stream.calibration_status in calibration_options else 0
                            )
                            calibration_status = c5.selectbox(
                                "Instrument calibration status", calibration_options, index=calibration_idx,
                                key=f"edit_stream_calib_status_{sel_stream.id}",
                            )
                            calibration_note = c6.text_input(
                                "Calibration note (e.g. cal. due date, certificate ref.)",
                                value=sel_stream.calibration_note or "", key=f"edit_stream_calib_note_{sel_stream.id}",
                            )
                            notes = st.text_area(
                                "Notes", value=sel_stream.notes or "", key=f"edit_stream_notes_{sel_stream.id}"
                            )

                            save = st.form_submit_button("Save changes")
                            if save:
                                if not stream_name.strip():
                                    st.error("Stream / raw material name is required.")
                                else:
                                    sel_stream.production_phase_id = finalized_phase.id
                                    sel_stream.stream_name = stream_name.strip()
                                    sel_stream.flow_unit = flow_unit
                                    sel_stream.flow = flow or None
                                    sel_stream.pump_speed = pump_speed or None
                                    sel_stream.flow_total_qty = flow_total_qty or None
                                    sel_stream.pressure_bar = pressure_bar or None
                                    sel_stream.temperature_c = temperature_c or None
                                    sel_stream.calibration_status = calibration_status or None
                                    sel_stream.calibration_note = calibration_note
                                    sel_stream.notes = notes
                                    session.commit()
                                    st.success("Stream reading updated.")
                                    st.rerun()

                        def _do_delete_stream(_session=session, _stream=sel_stream):
                            _session.delete(_stream)
                            _session.commit()
                            st.session_state.pop("pr_selected_stream_id", None)

                        _delete_with_confirm(
                            f"stream reading — {sel_stream.stream_name}", _do_delete_stream,
                            key_prefix=f"stream_{sel_stream.id}",
                        )
                    else:
                        st.caption("Click a row above to edit (and optionally delete) that stream reading.")

            with sub_create:
                st.caption("Phase: Finalized (component stream readings always attach here).")
                phase = finalized_phase
                if not recipe_components:
                    st.warning(
                        "This run's recipe version has no components listed yet — add them on the Recipe "
                        "Version Record page. Falling back to free text for now."
                    )
                stream_choice = st.selectbox(
                    "Stream / raw material *",
                    recipe_components,
                    format_func=lambda c: f"{c.raw_material_name}" + (f" ({c.role_in_formulation})" if c.role_in_formulation else ""),
                    key=f"stream_choice_select_{run.id}",
                ) if recipe_components else None
                with st.form(f"add_stream_reading_{run.id}"):
                    stream_other = st.text_input(
                        "Or type a stream not in the recipe (e.g. blended stream, process air, water addition)"
                    )
                    flow_unit = st.selectbox("Flow unit", ["kg/min", "L/min"])
                    c1, c2, c3, c4 = st.columns(4)
                    flow = c1.number_input("Flow", min_value=0.0, step=0.1)
                    pump_speed = c2.number_input(
                        "Pump speed", min_value=0.0, step=0.1,
                        help="Metering pump setting for this stream (RPM/Hz/% depending on OEM) — the "
                        "control input, distinct from the measured Flow.",
                    )
                    pressure_bar = c3.number_input("Pressure (bar)", min_value=0.0, step=0.1)
                    temperature_c = c4.number_input("Temperature (°C)", step=0.1)
                    flow_total_qty = st.number_input(
                        "Total delivered this phase (same base unit as flow unit, kg or L)", min_value=0.0, step=0.1
                    )
                    c5, c6 = st.columns(2)
                    calibration_status = c5.selectbox(
                        "Instrument calibration status", ["", "Valid", "Expired", "Failed", "Not Verified"]
                    )
                    calibration_note = c6.text_input("Calibration note (e.g. cal. due date, certificate ref.)")
                    notes = st.text_area("Notes")

                    submitted = st.form_submit_button("Save stream reading")
                    if submitted:
                        final_stream_name = stream_other.strip() or (
                            stream_choice.raw_material_name if stream_choice else ""
                        )
                        if not final_stream_name:
                            st.error("Pick a stream from the recipe, or type one that isn't in it.")
                        else:
                            session.add(
                                ComponentStreamReading(
                                    production_phase_id=phase.id,
                                    stream_name=final_stream_name,
                                    flow_unit=flow_unit,
                                    flow=flow or None,
                                    pump_speed=pump_speed or None,
                                    flow_total_qty=flow_total_qty or None,
                                    pressure_bar=pressure_bar or None,
                                    temperature_c=temperature_c or None,
                                    calibration_status=calibration_status or None,
                                    calibration_note=calibration_note,
                                    notes=notes,
                                    source_file_reference="manual entry",
                                )
                            )
                            session.commit()
                            st.success("Stream reading saved.")
                            st.rerun()

            with sub_import:
                st.caption(
                    "Required columns: " + ", ".join(STREAM_REQUIRED_COLUMNS) + ". Optional columns: "
                    + ", ".join(STREAM_OPTIONAL_COLUMNS) + ". Each row's production_run_id must already have "
                    "a Finalized phase — readings always attach there, never to Setup."
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
                            finalized_by_run = {
                                p.production_run_id: p
                                for p in session.query(ProductionPhase)
                                .filter(ProductionPhase.phase_name == "Finalized").all()
                            }
                            good_rows, bad_rows, resolved_phase_ids = [], [], []
                            for _, row in df.iterrows():
                                match = finalized_by_run.get(row.get("production_run_id"))
                                if match and row.get("stream_name"):
                                    good_rows.append(row)
                                    resolved_phase_ids.append(match.id)
                                else:
                                    bad_rows.append(row)

                            st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
                            if bad_rows:
                                st.warning(
                                    "Flagged rows reference a production_run_id with no Finalized phase yet, "
                                    "or are missing stream_name."
                                )
                                st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

                            if good_rows and st.button("Confirm import", key="confirm_stream_import"):
                                for row, phase_id in zip(good_rows, resolved_phase_ids):
                                    session.add(
                                        ComponentStreamReading(
                                            production_phase_id=phase_id,
                                            stream_name=str(row["stream_name"]),
                                            flow_unit=str(row.get("flow_unit", "") or "kg/min"),
                                            flow=row.get("flow"),
                                            pump_speed=row.get("pump_speed"),
                                            flow_total_qty=row.get("flow_total_qty"),
                                            pressure_bar=row.get("pressure_bar"),
                                            temperature_c=row.get("temperature_c"),
                                            calibration_status=str(row.get("calibration_status", "") or "") or None,
                                            calibration_note=str(row.get("calibration_note", "") or ""),
                                            notes=str(row.get("notes", "") or ""),
                                            source_file_reference=uploaded.name,
                                        )
                                    )
                                session.commit()
                                st.success(f"Imported {len(good_rows)} stream reading(s) from {uploaded.name}.")
                                st.rerun()

# ---------------------------------------------------------------------------
# Production events (alarms / interventions / grade changes)
# ---------------------------------------------------------------------------
with tab_events:
    st.caption(
        "Alarms, manual interventions, grade changes, and planned/unplanned pauses. This log is what "
        "explains outliers and lets transition material be excluded from steady-state analysis."
    )

    if not runs:
        st.info("Create a production run first (Production Runs tab).")
    else:
        run = _run_selector(runs, key="event_tab_run_select")
        st.caption(f"Showing events for **{_run_label(run)}**")
        phases_for_run = (
            session.query(ProductionPhase).filter(ProductionPhase.production_run_id == run.id).all()
        )

        sub_overview, sub_create, sub_import = st.tabs(["Overview & Edit", "Create", "CSV / Excel import"])

        events_for_run = (
            session.query(ProductionEvent)
            .filter(ProductionEvent.production_run_id == run.id)
            .order_by(ProductionEvent.event_ts.desc())
            .all()
        )

        with sub_overview:
            if not events_for_run:
                st.info("No events logged yet for this run — use the Create tab.")
            else:
                severity_icon = {"Low": "🟡", "Medium": "🟠", "High": "🔴"}
                event_rows = [
                    {
                        "Time": e.event_ts,
                        "Type": e.event_type,
                        "Severity": severity_icon.get(e.severity, "") + " " + (e.severity or "") if e.severity else "",
                        "Phase": e.phase.phase_name if e.phase else "—",
                        "Description": e.description,
                        "Action taken": e.action_taken,
                    }
                    for e in events_for_run
                ]
                idx = _clickable_table(event_rows, key=f"events_table_{run.id}")
                if idx is not None:
                    st.session_state["pr_selected_event_id"] = events_for_run[idx].id

                sel_event = next(
                    (e for e in events_for_run if e.id == st.session_state.get("pr_selected_event_id")), None
                )
                if sel_event:
                    st.markdown(f"##### Edit event — {sel_event.event_type}")
                    with st.form(f"edit_event_form_{sel_event.id}"):
                        event_type = st.selectbox(
                            "Event type *", EVENT_TYPES, index=EVENT_TYPES.index(sel_event.event_type),
                            key=f"edit_event_type_{sel_event.id}",
                        )
                        severity_options = [""] + SEVERITIES
                        severity_idx = severity_options.index(sel_event.severity) if sel_event.severity in severity_options else 0
                        severity = st.selectbox(
                            "Severity", severity_options, index=severity_idx, key=f"edit_event_severity_{sel_event.id}"
                        )
                        phase_options = [None] + phases_for_run
                        phase_idx = next(
                            (i for i, p in enumerate(phase_options) if p is not None and p.id == sel_event.production_phase_id),
                            0,
                        )
                        phase = st.selectbox(
                            "Phase (optional)", phase_options, index=phase_idx,
                            format_func=lambda p: "—" if p is None else f"{p.phase_name} ({p.phase_start})",
                            key=f"edit_event_phase_{sel_event.id}",
                        )
                        event_ts = combine_date_time(
                            "Event time", f"edit_event_ts_{sel_event.id}",
                            default_date=sel_event.event_ts.date() if sel_event.event_ts else None,
                            default_time=sel_event.event_ts.time() if sel_event.event_ts else None,
                        )
                        description = st.text_area(
                            "Description", value=sel_event.description or "", key=f"edit_event_desc_{sel_event.id}"
                        )
                        action_taken = st.text_area(
                            "Action taken", value=sel_event.action_taken or "", key=f"edit_event_action_{sel_event.id}"
                        )

                        save = st.form_submit_button("Save changes")
                        if save:
                            sel_event.event_type = event_type
                            sel_event.severity = severity or None
                            sel_event.production_phase_id = phase.id if phase else None
                            sel_event.event_ts = event_ts
                            sel_event.description = description
                            sel_event.action_taken = action_taken
                            session.commit()
                            st.success("Event updated.")
                            st.rerun()

                    def _do_delete_event(_session=session, _event=sel_event):
                        _session.delete(_event)
                        _session.commit()
                        st.session_state.pop("pr_selected_event_id", None)

                    _delete_with_confirm(
                        f"event — {sel_event.event_type}", _do_delete_event, key_prefix=f"event_{sel_event.id}"
                    )
                else:
                    st.caption("Click a row above to edit (and optionally delete) that event.")

        with sub_create:
            with st.form(f"add_event_{run.id}"):
                event_type = st.selectbox("Event type *", EVENT_TYPES)
                severity = st.selectbox("Severity", [""] + SEVERITIES)
                phase = st.selectbox(
                    "Phase (optional)",
                    [None] + phases_for_run,
                    format_func=lambda p: "—" if p is None else f"{p.phase_name} ({p.phase_start})",
                )
                event_ts = combine_date_time("Event time", f"new_event_ts_{run.id}")
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

        with sub_import:
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

# ---------------------------------------------------------------------------
# Runtime data (ambient conditions, line speed)
# ---------------------------------------------------------------------------
with tab_runtime:
    st.caption(
        "Ambient and line-speed conditions for a production run. Manual entry, or structured CSV/Excel import."
    )

    if not runs:
        st.info("Create a production run first (Production Runs tab).")
    else:
        run = _run_selector(runs, key="runtime_tab_run_select")
        st.caption(f"Showing runtime data for **{_run_label(run)}**")

        sub_overview, sub_create, sub_import = st.tabs(["Overview & Edit", "Create", "CSV / Excel import"])

        runtime_for_run = (
            session.query(RuntimeDataRecord)
            .filter(RuntimeDataRecord.production_run_id == run.id)
            .order_by(RuntimeDataRecord.id.desc())
            .all()
        )

        with sub_overview:
            if not runtime_for_run:
                st.info("No runtime data recorded yet for this run — use the Create tab.")
            else:
                runtime_rows = [
                    {
                        "Line speed": rt.line_speed,
                        "Temperature data": rt.temperature_data,
                        "Pressure data": rt.pressure_data,
                        "Ambient temp (°C)": rt.ambient_temperature,
                        "Ambient humidity (%)": rt.ambient_humidity,
                        "Rise time (s)": rt.rise_time,
                    }
                    for rt in runtime_for_run
                ]
                idx = _clickable_table(runtime_rows, key=f"runtime_table_{run.id}")
                if idx is not None:
                    st.session_state["pr_selected_runtime_id"] = runtime_for_run[idx].id

                sel_runtime = next(
                    (rt for rt in runtime_for_run if rt.id == st.session_state.get("pr_selected_runtime_id")), None
                )
                if sel_runtime:
                    st.markdown("##### Edit runtime data")
                    with st.form(f"edit_runtime_form_{sel_runtime.id}"):
                        c1, c2, c3 = st.columns(3)
                        line_speed = c1.number_input(
                            "Line speed", min_value=0.0, step=0.1, value=float(sel_runtime.line_speed or 0.0),
                            key=f"edit_runtime_speed_{sel_runtime.id}",
                        )
                        ambient_temp = c2.number_input(
                            "Ambient temperature (°C)", step=0.1, value=float(sel_runtime.ambient_temperature or 0.0),
                            key=f"edit_runtime_ambient_temp_{sel_runtime.id}",
                        )
                        ambient_humidity = c3.number_input(
                            "Ambient humidity (%)", min_value=0.0, max_value=100.0, step=0.5,
                            value=float(sel_runtime.ambient_humidity or 0.0),
                            key=f"edit_runtime_ambient_hum_{sel_runtime.id}",
                        )
                        temperature_data = st.text_input(
                            "Temperature data", value=sel_runtime.temperature_data or "",
                            key=f"edit_runtime_tempdata_{sel_runtime.id}",
                        )
                        pressure_data = st.text_input(
                            "Pressure data (where available)", value=sel_runtime.pressure_data or "",
                            key=f"edit_runtime_pressdata_{sel_runtime.id}",
                        )
                        rise_time = st.number_input(
                            "Rise time (s)", min_value=0.0, step=1.0, value=float(sel_runtime.rise_time or 0.0),
                            key=f"edit_runtime_rise_{sel_runtime.id}",
                        )
                        curing_notes = st.text_area(
                            "Curing / cutting timing notes", value=sel_runtime.curing_notes or "",
                            key=f"edit_runtime_curing_{sel_runtime.id}",
                        )
                        save = st.form_submit_button("Save changes")
                        if save:
                            sel_runtime.line_speed = line_speed or None
                            sel_runtime.temperature_data = temperature_data
                            sel_runtime.pressure_data = pressure_data
                            sel_runtime.ambient_temperature = ambient_temp or None
                            sel_runtime.ambient_humidity = ambient_humidity or None
                            sel_runtime.rise_time = rise_time or None
                            sel_runtime.curing_notes = curing_notes
                            session.commit()
                            st.success("Runtime data updated.")
                            st.rerun()

                    def _do_delete_runtime(_session=session, _rt=sel_runtime):
                        _session.delete(_rt)
                        _session.commit()
                        st.session_state.pop("pr_selected_runtime_id", None)

                    _delete_with_confirm(
                        "this runtime data record", _do_delete_runtime, key_prefix=f"runtime_{sel_runtime.id}"
                    )
                else:
                    st.caption("Click a row above to edit (and optionally delete) that runtime data record.")

        with sub_create:
            with st.form(f"add_runtime_manual_{run.id}"):
                c1, c2, c3 = st.columns(3)
                line_speed = c1.number_input("Line speed", min_value=0.0, step=0.1)
                ambient_temp = c2.number_input("Ambient temperature (°C)", step=0.1)
                ambient_humidity = c3.number_input("Ambient humidity (%)", min_value=0.0, max_value=100.0, step=0.5)
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

        with sub_import:
            st.caption(
                "Required column: `production_run_id`. Optional columns: " + ", ".join(RUNTIME_OPTIONAL_COLUMNS)
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
