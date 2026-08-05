"""Screen 5: Production Run

The primary, self-sufficient record of a batch: recipe used, machine
parameters, and (elsewhere) the quality results it produced. This is
routine, everyday data entry - it does NOT require framing a run as an
experiment. A deliberate trial/change investigation is captured as its own
independent Customer Trial or Optimization Trial record instead (see
pages/11_Customer_Trials.py / pages/12_Optimization_Trials.py) - the old
"Trial / Experiment" flag on a production run itself, and the standalone
Trial / Experiment page, were removed along with TrialRecord/
AdjustmentConclusion/ApprovalRecord (see cascades.py/db.py history).

Includes the Mandatory-tier process-data capture recommended in "Expanding
PI3 Plant Edition Production-Trial Data Capture for Polyurethane Foaming
Lines", adapted to what's actually capturable without a live PLC/OPC UA/MQTT
link or a machine data export/import: each run gets exactly two phase
snapshots, Setup (planned/configured before the run) and Finalized (what
actually happened, entered at shutdown).

Laid out as one tab per function: Production Runs (overview/edit/delete +
create), Setup, Runtime Data, Component Stream Readings, and Production
Events. Every function tab (other than Production Runs) opens with a
production-run selector shared with the other tabs, shows that run's related
record(s) with edit + delete, and keeps CSV/Excel bulk import as its own
sub-tab. Raw material lot tracking has been removed from this page (not
workable — batches get mixed in tanks); the underlying table is left
untouched in the schema.

Setup and Runtime Data used to be a single generic "Process Phases" tab (a
phase_name dropdown picking which of the two ProductionPhase snapshots you
were editing) plus a separate standalone "Runtime Data" tab backed by its
own RuntimeDataRecord table — the two were doing overlapping work (e.g.
RuntimeDataRecord.line_speed duplicating ProductionPhase.conveyor_speed).
Restructured 2026-08-02 into two dedicated tabs instead: Setup (hardcoded to
the "Setup" ProductionPhase row) and Runtime Data (hardcoded to the
"Finalized" ProductionPhase row, now also carrying rise_time — one of the
two fields RuntimeDataRecord had that nothing else captured (the other,
curing_notes, was removed from the app entirely on 2026-08-03 - not a real,
reliably-captured field in practice). See RuntimeDataRecord in db.py for
why that table is still there but retired.

Setup's field set was narrowed again on 2026-08-03, after feedback that
several fields shown there weren't actually Setup-appropriate: foam_height_mm
(a measured outcome of the foaming process, not something planned) and the
ambient temperature/humidity readings (observed conditions, not configured)
moved to Runtime Data only; ratio_index moved off ProductionPhase entirely
onto RecipeVersion (it's a formulation constant, not a per-run setting - see
db.py); the free-text "laydown_mode" field was renamed "Foaming mode" and
converted to a controlled dropdown (FOAMING_MODES: LLD/Trough/Traverse); the
free-text "Other geometry notes" field was dropped now that fall-plate
section positions are captured in structured form below it; and a new
top_flat_system_used yes/no field was added. Both Setup and Runtime Data
still carry the shared machine settings (mixer/conveyor/air/sidewall/foaming
mode/top-flat), so the plan-vs-actual comparison still works for those.
"""

import datetime as dt

import pandas as pd
import streamlit as st

import analytics
from access_control import can_use_page
from auth import current_user, logout_button, require_login
from cascades import delete_production_run_cascade, production_run_dependency_counts
from db import (
    EVENT_TYPES,
    FOAMING_MODES,
    SEVERITIES,
    ComponentStreamReading,
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
    Sample,
    expected_fallplate_section_count,
    get_session,
    init_db,
)
from helpers import (
    clickable_table,
    combine_date_time,
    csv_excel_uploader,
    dedupe_import_rows,
    delete_with_confirm,
    import_within_row_limit,
    page_setup,
    parse_dt,
    render_data_table,
    render_function_action_intro,
    set_pending_banner,
    show_pending_banner,
    upload_within_size_limit,
    view_only_notice,
)
from tenant_scope import apply_scope, clear_scope_cache, company_picker, grade_ids_for_company, plant_ids_for_company


@st.cache_data(ttl=30)
def _cached_versions_for_grade(_session, grade_id):
    """RecipeVersion rows for one foam grade. This page's Edit-run form and
    Add-run form each independently re-ran this exact query on every
    rerun (a Streamlit rerun fires on every widget interaction anywhere on
    the page, including switching tabs) - one of the contributors to this
    page's ~31-query-per-click count found in the 2026-08-05 performance
    audit. clear_scope_cache() (called after every write on this page)
    invalidates this too."""
    if not grade_id:
        return []
    return _session.query(RecipeVersion).filter(RecipeVersion.foam_grade_id == grade_id).all()


@st.cache_data(ttl=30)
def _cached_active_machines_for_plant(_session, plant_id):
    """Active Machine rows for one plant - same repeated-query pattern as
    _cached_versions_for_grade above, same fix."""
    if not plant_id:
        return []
    return (
        _session.query(Machine)
        .filter(Machine.plant_id == plant_id, Machine.active.is_(True))
        .all()
    )


RUN_REQUIRED_COLUMNS = ["foam_grade_id", "recipe_version_id"]
RUN_OPTIONAL_COLUMNS = [
    "machine_id", "run_date", "batch_reference", "block_reference",
    "operator_or_team_reference", "notes",
]

# Setup and Runtime Data are both ProductionPhase rows (phase_name "Setup"
# and "Finalized" respectively) - each tab is hardcoded to its own phase_name,
# so imports need production_run_id only; phase_name is implied by which tab
# you're importing into, not a file column.
#
# Setup's column set is deliberately narrower than Runtime Data's as of
# 2026-08-03: foam_height_mm (a measured outcome, not something planned) and
# ambient_temperature_c/ambient_humidity_pct (observed environmental
# conditions, not configured) were removed from Setup - both still exist on
# Runtime Data, the actual/observed snapshot. ratio_index was removed from
# both (it is now a recipe-level field - see RecipeVersion.ratio_index).
# laydown_mode was renamed foaming_mode and section_positions_note was
# dropped (superseded by the structured Fall-plate positions sub-tab) - see
# db.py's ProductionPhase for the full rationale on each.
SETUP_REQUIRED_COLUMNS = ["production_run_id"]
SETUP_OPTIONAL_COLUMNS = [
    "phase_start", "phase_end",
    "mixer_rpm", "conveyor_speed", "air_injection_rate", "air_pressure_bar",
    "foaming_mode", "top_flat_system_used",
    "sidewall_width_mm", "notes",
]

RUNTIME_REQUIRED_COLUMNS = ["production_run_id"]
RUNTIME_OPTIONAL_COLUMNS = SETUP_OPTIONAL_COLUMNS + [
    "foam_height_mm", "ambient_temperature_c", "ambient_humidity_pct",
    "rise_time", "meters_produced",
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
FALLPLATE_OPTIONAL_COLUMNS = ["distance_from_trough_mm", "position_mm", "actuator_position", "angle_deg", "notes"]

EVENT_REQUIRED_COLUMNS = ["production_run_id", "event_type", "event_ts"]
EVENT_OPTIONAL_COLUMNS = ["phase_name", "severity", "description", "action_taken"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run_label(r):
    return f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}"


def _max_batch_seq_for_prefix(session, prefix, plant_ids):
    """Highest existing sequence number already used under a given
    'B-DDMMYY' prefix, scoped to this company's plants (plant_ids=None means
    unfiltered - the platform owner viewing "All companies"). Uses the max,
    not a count, so a deleted or out-of-order batch reference never causes a
    duplicate number to be reissued. Scoping matters here too: without it,
    one company's daily batch count would jump around based on a different
    company's unrelated activity on the same calendar day."""
    query = session.query(ProductionRun.batch_reference).filter(ProductionRun.batch_reference.like(f"{prefix}-%"))
    existing = apply_scope(query, ProductionRun.plant_id, plant_ids).all()
    max_seq = 0
    for (br,) in existing:
        if not br:
            continue
        tail = br.rsplit("-", 1)[-1]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return max_seq


def _generate_batch_reference(session, run_date, plant_ids):
    """Auto-generate a batch reference as B-DDMMYY-NN (e.g. B-240726-01),
    NN scoped per calendar day (and per company) so it resets daily and
    stays short. Manual batch numbers are error-prone (typos, accidental
    duplicates) so this is computed rather than typed."""
    prefix = f"B-{run_date:%d%m%y}"
    return f"{prefix}-{_max_batch_seq_for_prefix(session, prefix, plant_ids) + 1:02d}"


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


# --- Production run cascade delete (a run can have a lot hanging off it) ---
# Shared with pages 1/2/3, which have to delete every run under a plant/
# product family/foam grade/recipe version being deleted - see cascades.py.

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
render_function_action_intro(
    function_text=(
        "This is the master record for a production batch. It stores the recipe and machine the "
        "batch ran on, its Setup and Finalized process settings, the metered material flows, any "
        "production events, and the runtime data. Quality test results and issues are captured on "
        "separate pages but always link back to the run created here. A deliberate trial/change "
        "investigation is its own independent record on the Customer Trials & Samples or "
        "Optimization Trials & Samples page instead - most runs never need that."
    ),
    action_steps=[
        "Open the **Production Runs** tab and create the batch record: pick the foam grade, "
        "recipe version, machine and run date. The batch reference is generated automatically.",
        "Open the **Setup** tab and log the planned/configured settings for this run, including "
        "ambient temperature and humidity.",
        "Open the **Runtime Data** tab once the run is finished and log what actually happened - "
        "the same settings as Setup, plus line-speed and rise time. Comparing Setup to Runtime "
        "Data is the plan-vs-actual read.",
        "Open the **Component Stream Readings** tab and log the metered material flows. Readings "
        "always attach to the Runtime Data (Finalized) snapshot.",
        "Open the **Production Events** tab and log any alarms, interventions or grade changes.",
    ],
    action_note=(
        "Every tab except Production Runs opens with the same run selector - pick the run once, "
        "then step through its tabs in order. Manual entry and CSV/Excel import are both "
        "available throughout."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("production_run", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="prod_run_company_filter"
)
active_company_id = company.id if company else None
plant_ids = plant_ids_for_company(session, active_company_id)
grade_ids = grade_ids_for_company(session, active_company_id)

grades = apply_scope(session.query(FoamGrade), FoamGrade.id, grade_ids).all()
if not grades:
    st.warning("Add a foam grade and recipe version first.")
    st.stop()

runs = (
    apply_scope(session.query(ProductionRun), ProductionRun.plant_id, plant_ids)
    .order_by(ProductionRun.created_at.desc())
    .all()
)

tab_runs, tab_setup, tab_runtime, tab_streams, tab_events = st.tabs(
    [
        "📋 Production Runs",
        "🛠️ Setup",
        "📊 Runtime Data",
        "🧪 Component Stream Readings",
        "🚨 Production Events",
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
                    "Date": r.run_date,
                    "Batch": r.batch_reference,
                    "Block": r.block_reference,
                    "Machine": r.machine.name if r.machine else "—",
                    "Operator": r.operator_or_team_reference,
                }
                for r in runs
            ]
            st.caption(f"{len(runs)} production run(s). Click a row to edit (and optionally delete) that run.")
            idx = clickable_table(run_rows, key="runs_overview_table")
            if idx is not None:
                st.session_state["pr_selected_run_id"] = runs[idx].id
            else:
                st.session_state.pop("pr_selected_run_id", None)

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
                    # No version picker here - a production run always uses whichever
                    # recipe version is currently active for the foam grade (see
                    # RecipeVersion.is_active in db.py; only one can be active per
                    # grade at a time). Offering a dropdown of every version implied
                    # a choice that doesn't actually exist - the version to use isn't
                    # a decision made on this page, it's whatever Recipes has marked
                    # current. Same fallback as Recipe Optimization's current_version
                    # for legacy data recorded before is_active existed.
                    versions_for_grade = _cached_versions_for_grade(session, grade.id if grade else None)
                    current_version = next(
                        (v for v in versions_for_grade if v.is_active),
                        versions_for_grade[-1] if versions_for_grade else None,
                    )
                    if current_version:
                        st.caption(f"Recipe version in use: **{current_version.version_label}** (current)")
                    else:
                        st.caption("⚠️ This foam grade has no recipe version yet - add one on the Recipes page first.")
                    machines_for_plant = _cached_active_machines_for_plant(
                        session, grade.product_family.plant_id if grade else None
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
                        help="Auto-generated when the run was created (B-DDMMYY-NN). Only change this "
                        "to correct a genuine mistake.",
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
                    save = st.form_submit_button("Save changes", disabled=not page_usable)
                    if save and page_usable:
                        if not current_version:
                            st.error("This foam grade has no recipe version yet — add one on the Recipes page first.")
                        else:
                            selected_run.foam_grade_id = grade.id
                            selected_run.plant_id = grade.product_family.plant_id
                            selected_run.recipe_version_id = current_version.id
                            selected_run.machine_id = machine.id if machine else None
                            selected_run.run_date = run_date
                            selected_run.batch_reference = batch_reference
                            selected_run.block_reference = block_reference
                            selected_run.operator_or_team_reference = operator
                            selected_run.notes = notes
                            session.commit()
                            st.success("Production run updated.")
                            st.rerun()

                counts = production_run_dependency_counts(session, selected_run.id)
                total_related = sum(counts.values())
                if total_related:
                    detail = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
                    warning = f"Deleting this run will also permanently delete {total_related} related record(s): {detail}."
                else:
                    warning = "This run has no related records — deleting it is safe."

                def _do_delete_run(_session=session, _run_id=selected_run.id):
                    delete_production_run_cascade(_session, _run_id)
                    _session.commit()
                    clear_scope_cache()
                    st.session_state.pop("pr_selected_run_id", None)

                if page_usable:
                    delete_with_confirm(
                        f"Run #{selected_run.id}", _do_delete_run, key_prefix=f"run_{selected_run.id}",
                        extra_warning=warning,
                    )
                else:
                    st.caption("View-only access - deleting is restricted for your role.")

                if st.button("Clear selection", key="clear_run_selection"):
                    st.session_state.pop("pr_selected_run_id", None)
                    st.rerun()

    with sub_create:
        sub_manual, sub_import = st.tabs(["Manual entry", "CSV / Excel import"])

        with sub_manual:
            # run_date lives outside the form so the batch-reference preview
            # below updates live as it's changed, before the operator commits
            # to saving - forms otherwise only release widget values on submit.
            run_date = st.date_input("Run date", value=dt.date.today(), key="create_run_date")
            batch_reference = _generate_batch_reference(session, run_date, plant_ids)
            st.caption(
                f"Batch reference (auto-generated, prevents typos/duplicates): **{batch_reference}**"
            )

            with st.form("add_run"):
                grade = st.selectbox("Foam grade *", grades, format_func=lambda g: g.grade_name)
                # No version picker - see the same note in the Edit Run form above.
                # A new run always uses whichever recipe version is currently active
                # for the chosen foam grade.
                versions_for_grade = _cached_versions_for_grade(session, grade.id if grade else None)
                current_version = next(
                    (v for v in versions_for_grade if v.is_active),
                    versions_for_grade[-1] if versions_for_grade else None,
                )
                if current_version:
                    st.caption(f"Recipe version in use: **{current_version.version_label}** (current)")
                else:
                    st.caption("⚠️ This foam grade has no recipe version yet - add one on the Recipes page first.")
                machines_for_plant = _cached_active_machines_for_plant(
                    session, grade.product_family.plant_id if grade else None
                )
                machine = st.selectbox(
                    "Machine / foaming line" + ("" if machines_for_plant else " (none set up for this plant yet)"),
                    [None] + machines_for_plant,
                    format_func=lambda m: "— not selected —" if m is None else f"{m.name} ({m.oem or 'OEM —'})",
                )
                block_reference = st.text_input("Block reference")
                operator = st.text_input("Operator / team reference")
                notes = st.text_area("Notes")

                submitted = st.form_submit_button("Save production run", disabled=not page_usable)
                if submitted and page_usable:
                    if not current_version:
                        st.error("This foam grade has no recipe version yet — add one on the Recipes page first.")
                    else:
                        run = ProductionRun(
                            plant_id=grade.product_family.plant_id,
                            foam_grade_id=grade.id,
                            recipe_version_id=current_version.id,
                            run_date=run_date,
                            batch_reference=_generate_batch_reference(session, run_date, plant_ids),
                            block_reference=block_reference,
                            machine_id=machine.id if machine else None,
                            operator_or_team_reference=operator,
                            notes=notes,
                        )
                        session.add(run)
                        session.commit()
                        clear_scope_cache()
                        st.session_state["pr_selected_run_id"] = run.id
                        st.success(f"Production run created. Batch reference: {run.batch_reference}.")
                        st.rerun()

        with sub_import:
            show_pending_banner("run_import_msg")
            st.caption(
                "recipe_version_id must belong to the foam_grade_id on the same row. plant_id and machine "
                "assignment are derived/validated from the foam grade automatically."
            )
            run_df, run_filename = csv_excel_uploader(RUN_REQUIRED_COLUMNS, RUN_OPTIONAL_COLUMNS, key="run_upload")
            if run_df is not None:
                grades_by_id = {g.id: g for g in grades}
                # Scoped to this company's grades (not an unfiltered, every-
                # company RecipeVersion.all() as before the 2026-08-05
                # performance audit) - cheaper, and no reason to pull other
                # companies' recipe versions into a lookup dict just to
                # validate this CSV upload.
                versions_by_id = {
                    v.id: v for v in apply_scope(session.query(RecipeVersion), RecipeVersion.foam_grade_id, grade_ids).all()
                }
                # Scoped to this company's plants too - otherwise a CSV row could
                # reference a machine_id belonging to a different company (the
                # foam_grade/recipe_version cross-check above doesn't catch this,
                # since machine_id isn't derived from either of those).
                machines_by_id = {
                    m.id: m for m in apply_scope(session.query(Machine), Machine.plant_id, plant_ids).all()
                }
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
                    render_data_table(pd.DataFrame(bad_rows), max_height="300px")

                if good_rows and st.button("Confirm import", key="confirm_run_import", disabled=not page_usable):
                    # Rows with an explicit batch_reference are deduped against
                    # what's already in the database, so re-clicking Confirm
                    # import (e.g. because the previous success message wasn't
                    # visibly persistent) can't silently insert the same batch
                    # twice. Rows with a blank batch_reference always get a
                    # fresh auto-generated one, so they need no such check.
                    existing_batch_refs = {
                        r.batch_reference
                        for r in apply_scope(session.query(ProductionRun), ProductionRun.plant_id, plant_ids).all()
                        if r.batch_reference
                    }
                    import_rows, dup_rows = [], []
                    for row in good_rows:
                        br = str(row.get("batch_reference", "") or "").strip()
                        if br and br in existing_batch_refs:
                            dup_rows.append(row)
                        else:
                            import_rows.append(row)
                            if br:
                                existing_batch_refs.add(br)

                    # Rows that already carry a batch_reference (e.g. migrating a
                    # historical log) keep it as-is; blank ones get auto-generated,
                    # tracking the running sequence per day in-memory so multiple
                    # blank rows for the same date in one file don't collide.
                    seq_by_prefix = {}
                    for row in import_rows:
                        grade_row = grades_by_id[row["foam_grade_id"]]
                        machine_val = row.get("machine_id")
                        run_date_val = pd.to_datetime(row.get("run_date"), errors="coerce")
                        final_run_date = run_date_val.date() if not pd.isna(run_date_val) else dt.date.today()
                        batch_val = str(row.get("batch_reference", "") or "").strip()
                        if not batch_val:
                            prefix = f"B-{final_run_date:%d%m%y}"
                            if prefix not in seq_by_prefix:
                                seq_by_prefix[prefix] = _max_batch_seq_for_prefix(session, prefix, plant_ids)
                            seq_by_prefix[prefix] += 1
                            batch_val = f"{prefix}-{seq_by_prefix[prefix]:02d}"
                        session.add(
                            ProductionRun(
                                plant_id=grade_row.product_family.plant_id,
                                foam_grade_id=grade_row.id,
                                recipe_version_id=int(row["recipe_version_id"]),
                                run_date=final_run_date,
                                batch_reference=batch_val,
                                block_reference=str(row.get("block_reference", "") or ""),
                                machine_id=int(machine_val) if not pd.isna(machine_val) else None,
                                operator_or_team_reference=str(row.get("operator_or_team_reference", "") or ""),
                                notes=str(row.get("notes", "") or ""),
                            )
                        )
                    session.commit()
                    clear_scope_cache()
                    msg = f"Imported {len(import_rows)} production run(s) from {run_filename}."
                    if dup_rows:
                        msg += f" Skipped {len(dup_rows)} row(s) whose batch_reference already exists (likely a repeat click)."
                    set_pending_banner("run_import_msg", msg)
                    st.rerun()

# ---------------------------------------------------------------------------
# Setup (planned/configured settings, entered before the run starts)
# ---------------------------------------------------------------------------
with tab_setup:
    st.caption(
        "The planned/configured settings for this run, entered before it starts. Compare this to "
        "the Runtime Data tab (the Finalized/actual snapshot) for the plan-vs-actual read — no "
        "separate setpoint column needed."
    )

    if not runs:
        st.info("Create a production run first (Production Runs tab).")
    else:
        run = _run_selector(runs, key="setup_tab_run_select")
        st.caption(f"Showing Setup data for **{_run_label(run)}**")

        sub_overview, sub_create, sub_import, sub_fallplate = st.tabs(
            ["Overview & Edit", "Create", "CSV / Excel import", "Fall-plate positions"]
        )

        setup_phase = (
            session.query(ProductionPhase)
            .filter(ProductionPhase.production_run_id == run.id, ProductionPhase.phase_name == "Setup")
            .first()
        )

        with sub_overview:
            if not setup_phase:
                st.info("No Setup data recorded yet for this run — use the Create tab.")
            else:
                st.markdown("##### Edit Setup data")
                with st.form(f"edit_setup_form_{setup_phase.id}"):
                    phase_start = combine_date_time(
                        "Setup start", f"edit_setup_start_{setup_phase.id}",
                        default_date=setup_phase.phase_start.date() if setup_phase.phase_start else None,
                        default_time=setup_phase.phase_start.time() if setup_phase.phase_start else None,
                    )
                    phase_end = combine_date_time(
                        "Setup end", f"edit_setup_end_{setup_phase.id}",
                        default_date=setup_phase.phase_end.date() if setup_phase.phase_end else None,
                        default_time=setup_phase.phase_end.time() if setup_phase.phase_end else None,
                    )

                    st.markdown("**Machine settings**")
                    c1, c2, c3, c4 = st.columns(4)
                    mixer_rpm = c1.number_input(
                        "Mixer rpm", min_value=0.0, step=1.0, value=float(setup_phase.mixer_rpm or 0.0),
                        key=f"edit_setup_mixer_{setup_phase.id}",
                    )
                    conveyor_speed = c2.number_input(
                        "Conveyor speed (m/min)", min_value=0.0, step=0.01,
                        value=float(setup_phase.conveyor_speed or 0.0), key=f"edit_setup_conveyor_{setup_phase.id}",
                    )
                    air_injection_rate = c3.number_input(
                        "Air injection rate", min_value=0.0, step=0.1,
                        value=float(setup_phase.air_injection_rate or 0.0), key=f"edit_setup_air_inj_{setup_phase.id}",
                    )
                    air_pressure_bar = c4.number_input(
                        "Air pressure (bar)", min_value=0.0, step=0.05,
                        value=float(setup_phase.air_pressure_bar or 0.0), key=f"edit_setup_air_pres_{setup_phase.id}",
                    )

                    c5, c6, c7 = st.columns(3)
                    sidewall_width_mm = c5.number_input(
                        "Sidewall width (mm)", min_value=0.0, step=1.0,
                        value=float(setup_phase.sidewall_width_mm or 0.0), key=f"edit_setup_sidewall_{setup_phase.id}",
                    )
                    foaming_mode_options = [None] + FOAMING_MODES
                    foaming_mode = c6.selectbox(
                        "Foaming mode", foaming_mode_options,
                        index=(
                            foaming_mode_options.index(setup_phase.foaming_mode)
                            if setup_phase.foaming_mode in FOAMING_MODES else 0
                        ),
                        format_func=lambda m: "— not set —" if m is None else m,
                        key=f"edit_setup_foaming_mode_{setup_phase.id}",
                    )
                    top_flat_system_used = c7.selectbox(
                        "Top-flat system in use?", [None, True, False],
                        index=(
                            0 if setup_phase.top_flat_system_used is None
                            else (1 if setup_phase.top_flat_system_used else 2)
                        ),
                        format_func=lambda v: "— not set —" if v is None else ("Yes" if v else "No"),
                        key=f"edit_setup_topflat_{setup_phase.id}",
                    )

                    notes = st.text_area(
                        "Notes", value=setup_phase.notes or "", key=f"edit_setup_notes_{setup_phase.id}"
                    )

                    save = st.form_submit_button("Save changes", disabled=not page_usable)
                    if save and page_usable:
                        if phase_end < phase_start:
                            st.error("Setup end must not be before Setup start.")
                        else:
                            setup_phase.phase_start = phase_start
                            setup_phase.phase_end = phase_end
                            setup_phase.mixer_rpm = mixer_rpm or None
                            setup_phase.conveyor_speed = conveyor_speed or None
                            setup_phase.air_injection_rate = air_injection_rate or None
                            setup_phase.air_pressure_bar = air_pressure_bar or None
                            setup_phase.foaming_mode = foaming_mode
                            setup_phase.top_flat_system_used = top_flat_system_used
                            setup_phase.sidewall_width_mm = sidewall_width_mm or None
                            setup_phase.notes = notes
                            session.commit()
                            st.success("Setup data updated.")
                            st.rerun()

                def _do_delete_setup(_session=session, _phase=setup_phase):
                    _delete_phase_cascade(_session, _phase)

                if page_usable:
                    delete_with_confirm(
                        f"Setup data (Run #{run.id})", _do_delete_setup, key_prefix=f"setup_{setup_phase.id}",
                        extra_warning=(
                            "Deleting this also deletes its fall-plate section positions, and unlinks (does "
                            "not delete) any production events that referenced it."
                        ),
                    )
                else:
                    st.caption("View-only access - deleting is restricted for your role.")

        with sub_create:
            if setup_phase:
                st.caption("Setup data already recorded for this run — edit it in the Overview & Edit tab.")
            else:
                with st.form(f"add_setup_{run.id}"):
                    # Default the date to the run's own run_date rather than
                    # today - setup happens on the day the batch is scheduled,
                    # and setup start/end are normally the same day (hours
                    # apart, not days), so both fields default to that one
                    # date. Still fully editable, including across a
                    # midnight-spanning setup if that genuinely happens.
                    phase_start = combine_date_time(
                        "Setup start", f"new_setup_start_{run.id}", default_date=run.run_date
                    )
                    phase_end = combine_date_time(
                        "Setup end", f"new_setup_end_{run.id}", default_date=run.run_date
                    )

                    st.markdown("**Machine settings**")
                    c1, c2, c3, c4 = st.columns(4)
                    mixer_rpm = c1.number_input("Mixer rpm", min_value=0.0, step=1.0, key=f"new_setup_mixer_{run.id}")
                    conveyor_speed = c2.number_input(
                        "Conveyor speed (m/min)", min_value=0.0, step=0.01, key=f"new_setup_conveyor_{run.id}"
                    )
                    air_injection_rate = c3.number_input(
                        "Air injection rate", min_value=0.0, step=0.1, key=f"new_setup_air_inj_{run.id}"
                    )
                    air_pressure_bar = c4.number_input(
                        "Air pressure (bar)", min_value=0.0, step=0.05, key=f"new_setup_air_pres_{run.id}"
                    )

                    c5, c6, c7 = st.columns(3)
                    sidewall_width_mm = c5.number_input(
                        "Sidewall width (mm)", min_value=0.0, step=1.0, key=f"new_setup_sidewall_{run.id}"
                    )
                    foaming_mode = c6.selectbox(
                        "Foaming mode", [None] + FOAMING_MODES,
                        format_func=lambda m: "— not set —" if m is None else m,
                        key=f"new_setup_foaming_mode_{run.id}",
                    )
                    top_flat_system_used = c7.selectbox(
                        "Top-flat system in use?", [None, True, False],
                        format_func=lambda v: "— not set —" if v is None else ("Yes" if v else "No"),
                        key=f"new_setup_topflat_{run.id}",
                    )

                    notes = st.text_area("Notes", key=f"new_setup_notes_{run.id}")

                    submitted = st.form_submit_button("Save Setup data", disabled=not page_usable)
                    if submitted and page_usable:
                        if phase_end < phase_start:
                            st.error("Setup end must not be before Setup start.")
                        else:
                            session.add(
                                ProductionPhase(
                                    production_run_id=run.id,
                                    phase_name="Setup",
                                    phase_start=phase_start,
                                    phase_end=phase_end,
                                    mixer_rpm=mixer_rpm or None,
                                    conveyor_speed=conveyor_speed or None,
                                    air_injection_rate=air_injection_rate or None,
                                    air_pressure_bar=air_pressure_bar or None,
                                    foaming_mode=foaming_mode,
                                    top_flat_system_used=top_flat_system_used,
                                    sidewall_width_mm=sidewall_width_mm or None,
                                    notes=notes,
                                    source_file_reference="manual entry",
                                )
                            )
                            session.commit()
                            st.success("Setup data saved.")
                            st.rerun()

        with sub_import:
            show_pending_banner("setup_import_msg")
            st.caption(
                "Required column: `production_run_id`. Optional columns: " + ", ".join(SETUP_OPTIONAL_COLUMNS)
                + ". One Setup row per run - a run that already has one is skipped as a duplicate."
            )
            uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="setup_upload")
            if uploaded and upload_within_size_limit(uploaded):
                try:
                    df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
                except Exception as exc:
                    st.error(f"Could not read file: {exc}")
                    df = None

                if df is not None and import_within_row_limit(df):
                    missing_cols = [c for c in SETUP_REQUIRED_COLUMNS if c not in df.columns]
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
                            render_data_table(pd.DataFrame(bad_rows), max_height="300px")

                        if good_rows and st.button("Confirm import", key="confirm_setup_import", disabled=not page_usable):
                            # Dedupe on production_run_id: only one Setup row per run.
                            existing_keys = {
                                p.production_run_id
                                for p in session.query(ProductionPhase).filter(ProductionPhase.phase_name == "Setup").all()
                            }
                            accept, dup = dedupe_import_rows(
                                good_rows, existing_keys,
                                key_func=lambda row: int(row["production_run_id"]),
                            )
                            for row in accept:
                                session.add(
                                    ProductionPhase(
                                        production_run_id=int(row["production_run_id"]),
                                        phase_name="Setup",
                                        phase_start=parse_dt(row.get("phase_start")),
                                        phase_end=parse_dt(row.get("phase_end")),
                                        mixer_rpm=row.get("mixer_rpm"),
                                        conveyor_speed=row.get("conveyor_speed"),
                                        air_injection_rate=row.get("air_injection_rate"),
                                        air_pressure_bar=row.get("air_pressure_bar"),
                                        foaming_mode=(
                                            str(row.get("foaming_mode")).strip()
                                            if str(row.get("foaming_mode", "") or "").strip() in FOAMING_MODES
                                            else None
                                        ),
                                        top_flat_system_used=(
                                            bool(row.get("top_flat_system_used"))
                                            if pd.notna(row.get("top_flat_system_used"))
                                            else None
                                        ),
                                        sidewall_width_mm=row.get("sidewall_width_mm"),
                                        notes=str(row.get("notes", "") or ""),
                                        source_file_reference=uploaded.name,
                                    )
                                )
                            session.commit()
                            msg = f"Imported {len(accept)} Setup row(s) from {uploaded.name}."
                            if dup:
                                msg += f" Skipped {len(dup)} row(s) whose run already has Setup data (likely a repeat click)."
                            set_pending_banner("setup_import_msg", msg)
                            st.rerun()

        with sub_fallplate:
            st.caption(
                "A fall-plate profile is a series of vertical positions at the plate joints/support "
                "points between the trough outlet and the horizontal conveyor (point 1 nearest the "
                "trough), not one overall plate angle - height above the conveyor datum is the primary "
                "setting; angle is calculated from adjacent points (or recorded directly on older "
                "machines). Requires Setup data to exist first for this run."
            )
            _expected_sections = expected_fallplate_section_count(run.machine)
            if _expected_sections:
                st.caption(
                    f"This run's machine ({run.machine.model}) typically has **{_expected_sections}** "
                    "adjustable fall-plate sections."
                )
            if not setup_phase:
                st.info("Add Setup data for this run first (Create tab) before recording section positions.")
            else:
                sub_fp_manual, sub_fp_import = st.tabs(["Manual entry", "CSV / Excel import"])

                with sub_fp_manual:
                    with st.form(f"add_fallplate_section_setup_{run.id}"):
                        c1, c2 = st.columns(2)
                        section_number = c1.number_input(
                            "Fall-plate point ID *", min_value=1, step=1, value=1,
                            help="1 = the point nearest the trough outlet, counting toward the conveyor.",
                        )
                        distance_from_trough_mm = c2.number_input(
                            "Distance from trough outlet (mm, optional)", step=1.0
                        )
                        c3, c4 = st.columns(2)
                        position_mm = c3.number_input("Height above conveyor datum (mm)", step=1.0)
                        actuator_position = c4.number_input(
                            "Raw actuator/encoder position (optional)", step=1.0,
                            help="Only if this machine's HMI shows a raw screw/motor/encoder position "
                            "rather than a calculated height.",
                        )
                        angle_deg = st.number_input(
                            "Plate angle (degrees, calculated or recorded, optional)", step=0.5
                        )
                        fp_notes = st.text_area("Notes", key=f"fp_notes_setup_{run.id}")
                        submitted = st.form_submit_button("Save section position", disabled=not page_usable)
                        if submitted and page_usable:
                            session.add(
                                FallplateSectionPosition(
                                    production_phase_id=setup_phase.id,
                                    section_number=int(section_number),
                                    distance_from_trough_mm=distance_from_trough_mm or None,
                                    position_mm=position_mm or None,
                                    actuator_position=actuator_position or None,
                                    angle_deg=angle_deg or None,
                                    notes=fp_notes,
                                )
                            )
                            session.commit()
                            st.success("Section position saved.")
                            st.rerun()

                with sub_fp_import:
                    st.caption(
                        "Required columns: " + ", ".join(FALLPLATE_REQUIRED_COLUMNS) + " (phase_name must be "
                        "'Setup'). Optional columns: " + ", ".join(FALLPLATE_OPTIONAL_COLUMNS)
                    )
                    uploaded_fp = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="fallplate_upload_setup")
                    if uploaded_fp and upload_within_size_limit(uploaded_fp):
                        try:
                            df_fp = (
                                pd.read_csv(uploaded_fp) if uploaded_fp.name.endswith(".csv")
                                else pd.read_excel(uploaded_fp)
                            )
                        except Exception as exc:
                            st.error(f"Could not read file: {exc}")
                            df_fp = None

                        if df_fp is not None and import_within_row_limit(df_fp):
                            missing_cols = [c for c in FALLPLATE_REQUIRED_COLUMNS if c not in df_fp.columns]
                            if missing_cols:
                                st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
                            else:
                                good_rows, bad_rows = [], []
                                for _, row in df_fp.iterrows():
                                    match = (
                                        row.get("production_run_id") == run.id
                                        and row.get("phase_name") == "Setup"
                                        and row.get("section_number") is not None
                                    )
                                    if match:
                                        good_rows.append(row)
                                    else:
                                        bad_rows.append(row)

                                st.write(
                                    f"Rows ready to import: **{len(good_rows)}** | "
                                    f"Rows flagged/rejected: **{len(bad_rows)}**"
                                )
                                if bad_rows:
                                    st.warning(
                                        f"Flagged rows don't match Run #{run.id}'s Setup phase, or are missing "
                                        "section_number."
                                    )
                                    render_data_table(pd.DataFrame(bad_rows), max_height="300px")

                                if good_rows and st.button("Confirm import", key="confirm_fallplate_import_setup", disabled=not page_usable):
                                    existing_keys = {
                                        (s.production_phase_id, s.section_number)
                                        for s in session.query(FallplateSectionPosition).all()
                                    }
                                    accept, dup = [], []
                                    for row in good_rows:
                                        key = (setup_phase.id, int(row["section_number"]))
                                        if key in existing_keys:
                                            dup.append(row)
                                        else:
                                            existing_keys.add(key)
                                            accept.append(row)

                                    for row in accept:
                                        session.add(
                                            FallplateSectionPosition(
                                                production_phase_id=setup_phase.id,
                                                section_number=int(row["section_number"]),
                                                distance_from_trough_mm=row.get("distance_from_trough_mm"),
                                                position_mm=row.get("position_mm"),
                                                actuator_position=row.get("actuator_position"),
                                                angle_deg=row.get("angle_deg"),
                                                notes=str(row.get("notes", "") or ""),
                                            )
                                        )
                                    session.commit()
                                    msg = f"Imported {len(accept)} section position(s) from {uploaded_fp.name}."
                                    if dup:
                                        msg += f" Skipped {len(dup)} row(s) already recorded for that section (likely a repeat click)."
                                    st.success(msg)
                                    st.rerun()

                recent_fp = (
                    session.query(FallplateSectionPosition)
                    .filter(FallplateSectionPosition.production_phase_id == setup_phase.id)
                    .order_by(FallplateSectionPosition.id.desc())
                    .all()
                )
                if recent_fp:
                    render_data_table(
                        pd.DataFrame(
                            [
                                {
                                    "Point ID": fp.section_number,
                                    "Distance from trough (mm)": fp.distance_from_trough_mm,
                                    "Height above datum (mm)": fp.position_mm,
                                    "Actuator position": fp.actuator_position,
                                    "Angle (deg)": fp.angle_deg,
                                    "Notes": fp.notes,
                                }
                                for fp in recent_fp
                            ]
                        ),
                        max_height="300px",
                    )

# ---------------------------------------------------------------------------
# Component stream readings
# ---------------------------------------------------------------------------
with tab_streams:
    st.caption(
        "Per raw-material stream (polyol, isocyanate, water/blowing agent, catalyst, etc.), the flow, "
        "pressure, and temperature — always against the Runtime Data (Finalized) snapshot."
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
                f"Add Runtime Data for {_run_label(run)} first (Runtime Data tab). Component "
                "stream readings are actual measurements, so they only ever attach to the Runtime "
                "Data (Finalized) snapshot, never to Setup."
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
                    idx = clickable_table(stream_rows, key=f"streams_table_{run.id}")
                    if idx is not None:
                        st.session_state["pr_selected_stream_id"] = streams_for_run[idx].id
                    else:
                        st.session_state.pop("pr_selected_stream_id", None)

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

                            save = st.form_submit_button("Save changes", disabled=not page_usable)
                            if save and page_usable:
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

                        if page_usable:
                            delete_with_confirm(
                                f"stream reading — {sel_stream.stream_name}", _do_delete_stream,
                                key_prefix=f"stream_{sel_stream.id}",
                            )
                        else:
                            st.caption("View-only access - deleting is restricted for your role.")
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

                    submitted = st.form_submit_button("Save stream reading", disabled=not page_usable)
                    if submitted and page_usable:
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
                show_pending_banner("stream_import_msg")
                st.caption(
                    "Required columns: " + ", ".join(STREAM_REQUIRED_COLUMNS) + ". Optional columns: "
                    + ", ".join(STREAM_OPTIONAL_COLUMNS) + ". Each row's production_run_id must already have "
                    "a Finalized phase — readings always attach there, never to Setup."
                )
                uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="stream_upload")
                if uploaded and upload_within_size_limit(uploaded):
                    try:
                        df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
                    except Exception as exc:
                        st.error(f"Could not read file: {exc}")
                        df = None

                    if df is not None and import_within_row_limit(df):
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
                                render_data_table(pd.DataFrame(bad_rows), max_height="300px")

                            if good_rows and st.button("Confirm import", key="confirm_stream_import", disabled=not page_usable):
                                # Dedupe on (production_run_id, stream_name): a repeat click of
                                # this button (e.g. because the previous success message wasn't
                                # visibly persistent) must not double-insert the same material's
                                # reading for the same run.
                                existing_keys = {
                                    (r.phase.production_run_id, r.stream_name.strip().lower())
                                    for r in session.query(ComponentStreamReading).all()
                                    if r.phase
                                }
                                paired = list(zip(good_rows, resolved_phase_ids))
                                accept, dup = [], []
                                for row, phase_id in paired:
                                    key = (row["production_run_id"], str(row["stream_name"]).strip().lower())
                                    if key in existing_keys:
                                        dup.append(row)
                                    else:
                                        existing_keys.add(key)
                                        accept.append((row, phase_id))

                                for row, phase_id in accept:
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
                                msg = f"Imported {len(accept)} stream reading(s) from {uploaded.name}."
                                if dup:
                                    msg += (
                                        f" Skipped {len(dup)} row(s) already recorded for that run/material "
                                        "(likely a repeat click)."
                                    )
                                set_pending_banner("stream_import_msg", msg)
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
                idx = clickable_table(event_rows, key=f"events_table_{run.id}")
                if idx is not None:
                    st.session_state["pr_selected_event_id"] = events_for_run[idx].id
                else:
                    st.session_state.pop("pr_selected_event_id", None)

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

                        save = st.form_submit_button("Save changes", disabled=not page_usable)
                        if save and page_usable:
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

                    if page_usable:
                        delete_with_confirm(
                            f"event — {sel_event.event_type}", _do_delete_event, key_prefix=f"event_{sel_event.id}"
                        )
                    else:
                        st.caption("View-only access - deleting is restricted for your role.")
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

                submitted = st.form_submit_button("Save event", disabled=not page_usable)
                if submitted and page_usable:
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
            if uploaded and upload_within_size_limit(uploaded):
                try:
                    df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
                except Exception as exc:
                    st.error(f"Could not read file: {exc}")
                    df = None

                if df is not None and import_within_row_limit(df):
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
                            render_data_table(pd.DataFrame(bad_rows), max_height="300px")

                        if good_rows and st.button("Confirm import", key="confirm_event_import", disabled=not page_usable):
                            # Dedupe on (production_run_id, event_ts, event_type): a repeat
                            # click of this button must not double-insert the same logged
                            # event.
                            existing_keys = {
                                (e.production_run_id, e.event_ts, e.event_type)
                                for e in session.query(ProductionEvent).all()
                            }
                            paired = list(zip(good_rows, resolved_phase_ids))
                            accept, dup = [], []
                            for row, phase_id in paired:
                                key = (int(row["production_run_id"]), parse_dt(row.get("event_ts")), row["event_type"])
                                if key in existing_keys:
                                    dup.append(row)
                                else:
                                    existing_keys.add(key)
                                    accept.append((row, phase_id))

                            for row, phase_id in accept:
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
                            msg = f"Imported {len(accept)} event(s) from {uploaded.name}."
                            if dup:
                                msg += f" Skipped {len(dup)} row(s) already recorded for that run/time/type (likely a repeat click)."
                            st.success(msg)
                            st.rerun()

# ---------------------------------------------------------------------------
# Runtime Data (the Finalized snapshot: what actually happened, entered at
# shutdown - the same settings as Setup, plus line speed/rise time/curing
# outcomes that only exist once the run has actually happened).
# ---------------------------------------------------------------------------
with tab_runtime:
    st.caption(
        "What actually happened, entered at shutdown - the same settings as Setup, plus rise time and "
        "curing notes. Compare this to the Setup tab for the plan-vs-actual read. Component stream "
        "readings and production events both attach to this snapshot, never to Setup."
    )

    if not runs:
        st.info("Create a production run first (Production Runs tab).")
    else:
        run = _run_selector(runs, key="runtime_tab_run_select")
        st.caption(f"Showing Runtime Data for **{_run_label(run)}**")

        sub_overview, sub_create, sub_import, sub_fallplate = st.tabs(
            ["Overview & Edit", "Create", "CSV / Excel import", "Fall-plate positions"]
        )

        finalized_phase = (
            session.query(ProductionPhase)
            .filter(ProductionPhase.production_run_id == run.id, ProductionPhase.phase_name == "Finalized")
            .first()
        )

        with sub_overview:
            if not finalized_phase:
                st.info("No Runtime Data recorded yet for this run — use the Create tab.")
            else:
                st.markdown("##### Edit Runtime Data")
                with st.form(f"edit_runtime_form_{finalized_phase.id}"):
                    phase_start = combine_date_time(
                        "Run start", f"edit_runtime_start_{finalized_phase.id}",
                        default_date=finalized_phase.phase_start.date() if finalized_phase.phase_start else None,
                        default_time=finalized_phase.phase_start.time() if finalized_phase.phase_start else None,
                    )
                    phase_end = combine_date_time(
                        "Run end", f"edit_runtime_end_{finalized_phase.id}",
                        default_date=finalized_phase.phase_end.date() if finalized_phase.phase_end else None,
                        default_time=finalized_phase.phase_end.time() if finalized_phase.phase_end else None,
                    )

                    st.markdown("**Machine settings**")
                    c1, c2, c3, c4 = st.columns(4)
                    mixer_rpm = c1.number_input(
                        "Mixer rpm", min_value=0.0, step=1.0, value=float(finalized_phase.mixer_rpm or 0.0),
                        key=f"edit_runtime_mixer_{finalized_phase.id}",
                    )
                    conveyor_speed = c2.number_input(
                        "Conveyor speed (m/min)", min_value=0.0, step=0.01,
                        value=float(finalized_phase.conveyor_speed or 0.0), key=f"edit_runtime_conveyor_{finalized_phase.id}",
                        help="Also serves as the line speed - no separate field, since the two are the same reading.",
                    )
                    air_injection_rate = c3.number_input(
                        "Air injection rate", min_value=0.0, step=0.1,
                        value=float(finalized_phase.air_injection_rate or 0.0), key=f"edit_runtime_air_inj_{finalized_phase.id}",
                    )
                    air_pressure_bar = c4.number_input(
                        "Air pressure (bar)", min_value=0.0, step=0.05,
                        value=float(finalized_phase.air_pressure_bar or 0.0), key=f"edit_runtime_air_pres_{finalized_phase.id}",
                    )

                    c5, c6, c7 = st.columns(3)
                    sidewall_width_mm = c5.number_input(
                        "Sidewall width (mm)", min_value=0.0, step=1.0,
                        value=float(finalized_phase.sidewall_width_mm or 0.0), key=f"edit_runtime_sidewall_{finalized_phase.id}",
                    )
                    foaming_mode_options = [None] + FOAMING_MODES
                    foaming_mode = c6.selectbox(
                        "Foaming mode", foaming_mode_options,
                        index=(
                            foaming_mode_options.index(finalized_phase.foaming_mode)
                            if finalized_phase.foaming_mode in FOAMING_MODES else 0
                        ),
                        format_func=lambda m: "— not set —" if m is None else m,
                        key=f"edit_runtime_foaming_mode_{finalized_phase.id}",
                    )
                    top_flat_system_used = c7.selectbox(
                        "Top-flat system in use?", [None, True, False],
                        index=(
                            0 if finalized_phase.top_flat_system_used is None
                            else (1 if finalized_phase.top_flat_system_used else 2)
                        ),
                        format_func=lambda v: "— not set —" if v is None else ("Yes" if v else "No"),
                        key=f"edit_runtime_topflat_{finalized_phase.id}",
                    )

                    st.markdown("**Ambient conditions**")
                    c8, c9 = st.columns(2)
                    ambient_temperature_c = c8.number_input(
                        "Ambient temperature (°C)", step=0.1,
                        value=float(finalized_phase.ambient_temperature_c or 0.0),
                        key=f"edit_runtime_ambient_temp_{finalized_phase.id}",
                    )
                    ambient_humidity_pct = c9.number_input(
                        "Ambient humidity (%)", min_value=0.0, max_value=100.0, step=0.5,
                        value=float(finalized_phase.ambient_humidity_pct or 0.0),
                        key=f"edit_runtime_ambient_hum_{finalized_phase.id}",
                    )

                    st.markdown("**Runtime outcomes**")
                    foam_height_mm = st.number_input(
                        "Foam height (mm)", min_value=0.0, step=1.0,
                        value=float(finalized_phase.foam_height_mm or 0.0), key=f"edit_runtime_height_{finalized_phase.id}",
                        help="A result of the foaming process, measured here — not a Setup-tab setting.",
                    )
                    rise_time = st.number_input(
                        "Rise time (s)", min_value=0.0, step=1.0, value=float(finalized_phase.rise_time or 0.0),
                        key=f"edit_runtime_rise_{finalized_phase.id}",
                    )
                    meters_produced = st.number_input(
                        "Meters produced (m)", min_value=0.0, step=1.0,
                        value=float(finalized_phase.meters_produced or 0.0), key=f"edit_runtime_meters_{finalized_phase.id}",
                        help=(
                            "Leave at 0 to have it calculated instead from conveyor speed x run start/end time. "
                            "Combined with sidewall width and foam height, this drives the calculated volume/weight "
                            "shown below once saved."
                        ),
                    )

                    notes = st.text_area(
                        "Notes", value=finalized_phase.notes or "", key=f"edit_runtime_notes_{finalized_phase.id}"
                    )

                    save = st.form_submit_button("Save changes", disabled=not page_usable)
                    if save and page_usable:
                        if phase_end < phase_start:
                            st.error("Run end must not be before Run start.")
                        else:
                            finalized_phase.phase_start = phase_start
                            finalized_phase.phase_end = phase_end
                            finalized_phase.mixer_rpm = mixer_rpm or None
                            finalized_phase.conveyor_speed = conveyor_speed or None
                            finalized_phase.air_injection_rate = air_injection_rate or None
                            finalized_phase.air_pressure_bar = air_pressure_bar or None
                            finalized_phase.ambient_temperature_c = ambient_temperature_c or None
                            finalized_phase.ambient_humidity_pct = ambient_humidity_pct or None
                            finalized_phase.rise_time = rise_time or None
                            finalized_phase.foaming_mode = foaming_mode
                            finalized_phase.top_flat_system_used = top_flat_system_used
                            finalized_phase.sidewall_width_mm = sidewall_width_mm or None
                            finalized_phase.foam_height_mm = foam_height_mm or None
                            finalized_phase.meters_produced = meters_produced or None
                            finalized_phase.notes = notes
                            session.commit()
                            st.success("Runtime Data updated.")
                            st.rerun()

                st.markdown("**Calculated output**")
                _output = analytics.compute_runtime_output(finalized_phase, run.foam_grade)
                if _output["length_m"] is None and _output["volume_m3"] is None:
                    st.caption(
                        "Not enough data yet to calculate output - needs conveyor speed plus either meters "
                        "produced or a run start/end time, and sidewall width + foam height for volume/weight."
                    )
                else:
                    oc1, oc2, oc3 = st.columns(3)
                    length_label = "Meters produced"
                    if _output["length_source"] == "calculated":
                        length_label += " (calculated)"
                    oc1.metric(length_label, f"{_output['length_m']:.1f} m" if _output["length_m"] is not None else "—")
                    oc2.metric(
                        "Calculated volume",
                        f"{_output['volume_m3']:.2f} m³" if _output["volume_m3"] is not None else "—",
                    )
                    oc3.metric(
                        "Calculated weight",
                        f"{_output['weight_kg']:.0f} kg" if _output["weight_kg"] is not None else "—",
                    )
                    if _output["length_source"] == "entered" and _output["implied_duration_min"] is not None:
                        note = (
                            f"At this conveyor speed, {_output['length_m']:.1f} m implies a runtime of "
                            f"{_output['implied_duration_min']:.1f} min"
                        )
                        if _output["actual_duration_min"] is not None:
                            note += f" (recorded run start/end gives {_output['actual_duration_min']:.1f} min)."
                        else:
                            note += "."
                        st.caption(note)
                    elif _output["length_source"] == "calculated" and _output["actual_duration_min"] is not None:
                        st.caption(
                            "Meters produced wasn't entered - calculated from conveyor speed x the recorded run "
                            f"duration ({_output['actual_duration_min']:.1f} min)."
                        )
                    if _output["volume_m3"] is None and _output["length_m"] is not None:
                        st.caption("Sidewall width and/or foam height not recorded - can't calculate volume/weight yet.")
                    elif _output["weight_kg"] is None and _output["volume_m3"] is not None:
                        st.caption("This foam grade has no target density set - can't calculate weight yet.")

                def _do_delete_runtime(_session=session, _phase=finalized_phase):
                    _delete_phase_cascade(_session, _phase)

                if page_usable:
                    delete_with_confirm(
                        f"Runtime Data (Run #{run.id})", _do_delete_runtime, key_prefix=f"runtime_{finalized_phase.id}",
                        extra_warning=(
                            "Deleting this also deletes its component stream readings and fall-plate section "
                            "positions, and unlinks (does not delete) any production events that referenced it."
                        ),
                    )
                else:
                    st.caption("View-only access - deleting is restricted for your role.")

        with sub_create:
            if finalized_phase:
                st.caption("Runtime Data already recorded for this run — edit it in the Overview & Edit tab.")
            else:
                with st.form(f"add_runtime_{run.id}"):
                    # Same reasoning as the Setup Create form above: default
                    # to the run's own run_date, not today, since Runtime
                    # Data is normally logged the same day the batch ran
                    # (hours apart, not days) - still fully editable.
                    phase_start = combine_date_time(
                        "Run start", f"new_runtime_start_{run.id}", default_date=run.run_date
                    )
                    phase_end = combine_date_time(
                        "Run end", f"new_runtime_end_{run.id}", default_date=run.run_date
                    )

                    st.markdown("**Machine settings**")
                    c1, c2, c3, c4 = st.columns(4)
                    mixer_rpm = c1.number_input("Mixer rpm", min_value=0.0, step=1.0, key=f"new_runtime_mixer_{run.id}")
                    conveyor_speed = c2.number_input(
                        "Conveyor speed (m/min)", min_value=0.0, step=0.01, key=f"new_runtime_conveyor_{run.id}",
                        help="Also serves as the line speed - no separate field, since the two are the same reading.",
                    )
                    air_injection_rate = c3.number_input(
                        "Air injection rate", min_value=0.0, step=0.1, key=f"new_runtime_air_inj_{run.id}"
                    )
                    air_pressure_bar = c4.number_input(
                        "Air pressure (bar)", min_value=0.0, step=0.05, key=f"new_runtime_air_pres_{run.id}"
                    )

                    c5, c6, c7 = st.columns(3)
                    sidewall_width_mm = c5.number_input(
                        "Sidewall width (mm)", min_value=0.0, step=1.0, key=f"new_runtime_sidewall_{run.id}"
                    )
                    foaming_mode = c6.selectbox(
                        "Foaming mode", [None] + FOAMING_MODES,
                        format_func=lambda m: "— not set —" if m is None else m,
                        key=f"new_runtime_foaming_mode_{run.id}",
                    )
                    top_flat_system_used = c7.selectbox(
                        "Top-flat system in use?", [None, True, False],
                        format_func=lambda v: "— not set —" if v is None else ("Yes" if v else "No"),
                        key=f"new_runtime_topflat_{run.id}",
                    )

                    st.markdown("**Ambient conditions**")
                    c8, c9 = st.columns(2)
                    ambient_temperature_c = c8.number_input(
                        "Ambient temperature (°C)", step=0.1, key=f"new_runtime_ambient_temp_{run.id}",
                    )
                    ambient_humidity_pct = c9.number_input(
                        "Ambient humidity (%)", min_value=0.0, max_value=100.0, step=0.5,
                        key=f"new_runtime_ambient_hum_{run.id}",
                    )

                    st.markdown("**Runtime outcomes**")
                    foam_height_mm = st.number_input(
                        "Foam height (mm)", min_value=0.0, step=1.0, key=f"new_runtime_height_{run.id}",
                        help="A result of the foaming process, measured here — not a Setup-tab setting.",
                    )
                    rise_time = st.number_input("Rise time (s)", min_value=0.0, step=1.0, key=f"new_runtime_rise_{run.id}")
                    meters_produced = st.number_input(
                        "Meters produced (m)", min_value=0.0, step=1.0, key=f"new_runtime_meters_{run.id}",
                        help=(
                            "Leave at 0 to have it calculated instead from conveyor speed x run start/end time. "
                            "Combined with sidewall width and foam height, this drives the calculated volume/weight "
                            "shown after saving."
                        ),
                    )

                    notes = st.text_area("Notes", key=f"new_runtime_notes_{run.id}")

                    submitted = st.form_submit_button("Save Runtime Data", disabled=not page_usable)
                    if submitted and page_usable:
                        if phase_end < phase_start:
                            st.error("Run end must not be before Run start.")
                        else:
                            session.add(
                                ProductionPhase(
                                    production_run_id=run.id,
                                    phase_name="Finalized",
                                    phase_start=phase_start,
                                    phase_end=phase_end,
                                    mixer_rpm=mixer_rpm or None,
                                    conveyor_speed=conveyor_speed or None,
                                    air_injection_rate=air_injection_rate or None,
                                    air_pressure_bar=air_pressure_bar or None,
                                    ambient_temperature_c=ambient_temperature_c or None,
                                    ambient_humidity_pct=ambient_humidity_pct or None,
                                    rise_time=rise_time or None,
                                    foaming_mode=foaming_mode,
                                    top_flat_system_used=top_flat_system_used,
                                    sidewall_width_mm=sidewall_width_mm or None,
                                    foam_height_mm=foam_height_mm or None,
                                    meters_produced=meters_produced or None,
                                    notes=notes,
                                    source_file_reference="manual entry",
                                )
                            )
                            session.commit()
                            st.success("Runtime Data saved.")
                            st.rerun()

        with sub_import:
            show_pending_banner("runtime_import_msg")
            st.caption(
                "Required column: `production_run_id`. Optional columns: " + ", ".join(RUNTIME_OPTIONAL_COLUMNS)
                + ". One Runtime Data row per run - a run that already has one is skipped as a duplicate."
            )
            uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="runtime_upload")
            if uploaded and upload_within_size_limit(uploaded):
                try:
                    if uploaded.name.endswith(".csv"):
                        df = pd.read_csv(uploaded)
                    else:
                        df = pd.read_excel(uploaded)
                except Exception as exc:
                    st.error(f"Could not read file: {exc}")
                    df = None

                if df is not None and import_within_row_limit(df):
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
                            render_data_table(pd.DataFrame(bad_rows), max_height="300px")

                        if good_rows and st.button("Confirm import", key="confirm_runtime_import", disabled=not page_usable):
                            # Dedupe on production_run_id: only one Finalized row per run.
                            existing_keys = {
                                p.production_run_id
                                for p in session.query(ProductionPhase).filter(ProductionPhase.phase_name == "Finalized").all()
                            }
                            accept, dup = dedupe_import_rows(
                                good_rows, existing_keys,
                                key_func=lambda row: int(row["production_run_id"]),
                            )
                            for row in accept:
                                session.add(
                                    ProductionPhase(
                                        production_run_id=int(row["production_run_id"]),
                                        phase_name="Finalized",
                                        phase_start=parse_dt(row.get("phase_start")),
                                        phase_end=parse_dt(row.get("phase_end")),
                                        mixer_rpm=row.get("mixer_rpm"),
                                        conveyor_speed=row.get("conveyor_speed"),
                                        air_injection_rate=row.get("air_injection_rate"),
                                        air_pressure_bar=row.get("air_pressure_bar"),
                                        ambient_temperature_c=row.get("ambient_temperature_c"),
                                        ambient_humidity_pct=row.get("ambient_humidity_pct"),
                                        rise_time=row.get("rise_time"),
                                        foaming_mode=(
                                            str(row.get("foaming_mode")).strip()
                                            if str(row.get("foaming_mode", "") or "").strip() in FOAMING_MODES
                                            else None
                                        ),
                                        top_flat_system_used=(
                                            bool(row.get("top_flat_system_used"))
                                            if pd.notna(row.get("top_flat_system_used"))
                                            else None
                                        ),
                                        sidewall_width_mm=row.get("sidewall_width_mm"),
                                        foam_height_mm=row.get("foam_height_mm"),
                                        meters_produced=row.get("meters_produced"),
                                        notes=str(row.get("notes", "") or ""),
                                        source_file_reference=uploaded.name,
                                    )
                                )
                            session.commit()
                            msg = f"Imported {len(accept)} Runtime Data row(s) from {uploaded.name}."
                            if dup:
                                msg += f" Skipped {len(dup)} row(s) whose run already has Runtime Data (likely a repeat click)."
                            set_pending_banner("runtime_import_msg", msg)
                            st.rerun()

        with sub_fallplate:
            st.caption(
                "A fall-plate profile is a series of vertical positions at the plate joints/support "
                "points between the trough outlet and the horizontal conveyor (point 1 nearest the "
                "trough), not one overall plate angle - height above the conveyor datum is the primary "
                "setting; angle is calculated from adjacent points (or recorded directly on older "
                "machines). Requires Runtime Data to exist first for this run."
            )
            _expected_sections_rt = expected_fallplate_section_count(run.machine)
            if _expected_sections_rt:
                st.caption(
                    f"This run's machine ({run.machine.model}) typically has **{_expected_sections_rt}** "
                    "adjustable fall-plate sections."
                )
            if not finalized_phase:
                st.info("Add Runtime Data for this run first (Create tab) before recording section positions.")
            else:
                sub_fp_manual, sub_fp_import = st.tabs(["Manual entry", "CSV / Excel import"])

                with sub_fp_manual:
                    with st.form(f"add_fallplate_section_runtime_{run.id}"):
                        c1, c2 = st.columns(2)
                        section_number = c1.number_input(
                            "Fall-plate point ID *", min_value=1, step=1, value=1,
                            help="1 = the point nearest the trough outlet, counting toward the conveyor.",
                        )
                        distance_from_trough_mm = c2.number_input(
                            "Distance from trough outlet (mm, optional)", step=1.0
                        )
                        c3, c4 = st.columns(2)
                        position_mm = c3.number_input("Height above conveyor datum (mm)", step=1.0)
                        actuator_position = c4.number_input(
                            "Raw actuator/encoder position (optional)", step=1.0,
                            help="Only if this machine's HMI shows a raw screw/motor/encoder position "
                            "rather than a calculated height.",
                        )
                        angle_deg = st.number_input(
                            "Plate angle (degrees, calculated or recorded, optional)", step=0.5
                        )
                        fp_notes = st.text_area("Notes", key=f"fp_notes_runtime_{run.id}")
                        submitted = st.form_submit_button("Save section position", disabled=not page_usable)
                        if submitted and page_usable:
                            session.add(
                                FallplateSectionPosition(
                                    production_phase_id=finalized_phase.id,
                                    section_number=int(section_number),
                                    distance_from_trough_mm=distance_from_trough_mm or None,
                                    position_mm=position_mm or None,
                                    actuator_position=actuator_position or None,
                                    angle_deg=angle_deg or None,
                                    notes=fp_notes,
                                )
                            )
                            session.commit()
                            st.success("Section position saved.")
                            st.rerun()

                with sub_fp_import:
                    st.caption(
                        "Required columns: " + ", ".join(FALLPLATE_REQUIRED_COLUMNS) + " (phase_name must be "
                        "'Finalized'). Optional columns: " + ", ".join(FALLPLATE_OPTIONAL_COLUMNS)
                    )
                    uploaded_fp = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key="fallplate_upload_runtime")
                    if uploaded_fp and upload_within_size_limit(uploaded_fp):
                        try:
                            df_fp = (
                                pd.read_csv(uploaded_fp) if uploaded_fp.name.endswith(".csv")
                                else pd.read_excel(uploaded_fp)
                            )
                        except Exception as exc:
                            st.error(f"Could not read file: {exc}")
                            df_fp = None

                        if df_fp is not None and import_within_row_limit(df_fp):
                            missing_cols = [c for c in FALLPLATE_REQUIRED_COLUMNS if c not in df_fp.columns]
                            if missing_cols:
                                st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
                            else:
                                good_rows, bad_rows = [], []
                                for _, row in df_fp.iterrows():
                                    match = (
                                        row.get("production_run_id") == run.id
                                        and row.get("phase_name") == "Finalized"
                                        and row.get("section_number") is not None
                                    )
                                    if match:
                                        good_rows.append(row)
                                    else:
                                        bad_rows.append(row)

                                st.write(
                                    f"Rows ready to import: **{len(good_rows)}** | "
                                    f"Rows flagged/rejected: **{len(bad_rows)}**"
                                )
                                if bad_rows:
                                    st.warning(
                                        f"Flagged rows don't match Run #{run.id}'s Finalized phase, or are "
                                        "missing section_number."
                                    )
                                    render_data_table(pd.DataFrame(bad_rows), max_height="300px")

                                if good_rows and st.button("Confirm import", key="confirm_fallplate_import_runtime", disabled=not page_usable):
                                    existing_keys = {
                                        (s.production_phase_id, s.section_number)
                                        for s in session.query(FallplateSectionPosition).all()
                                    }
                                    accept, dup = [], []
                                    for row in good_rows:
                                        key = (finalized_phase.id, int(row["section_number"]))
                                        if key in existing_keys:
                                            dup.append(row)
                                        else:
                                            existing_keys.add(key)
                                            accept.append(row)

                                    for row in accept:
                                        session.add(
                                            FallplateSectionPosition(
                                                production_phase_id=finalized_phase.id,
                                                section_number=int(row["section_number"]),
                                                distance_from_trough_mm=row.get("distance_from_trough_mm"),
                                                position_mm=row.get("position_mm"),
                                                actuator_position=row.get("actuator_position"),
                                                angle_deg=row.get("angle_deg"),
                                                notes=str(row.get("notes", "") or ""),
                                            )
                                        )
                                    session.commit()
                                    msg = f"Imported {len(accept)} section position(s) from {uploaded_fp.name}."
                                    if dup:
                                        msg += f" Skipped {len(dup)} row(s) already recorded for that section (likely a repeat click)."
                                    st.success(msg)
                                    st.rerun()

                recent_fp = (
                    session.query(FallplateSectionPosition)
                    .filter(FallplateSectionPosition.production_phase_id == finalized_phase.id)
                    .order_by(FallplateSectionPosition.id.desc())
                    .all()
                )
                if recent_fp:
                    render_data_table(
                        pd.DataFrame(
                            [
                                {
                                    "Point ID": fp.section_number,
                                    "Distance from trough (mm)": fp.distance_from_trough_mm,
                                    "Height above datum (mm)": fp.position_mm,
                                    "Actuator position": fp.actuator_position,
                                    "Angle (deg)": fp.angle_deg,
                                    "Notes": fp.notes,
                                }
                                for fp in recent_fp
                            ]
                        ),
                        max_height="300px",
                    )
