"""Screen 5: Production Run / Trial Record"""

import datetime as dt

import pandas as pd
import streamlit as st

from auth import logout_button, require_login
from db import FoamGrade, ProductionRun, RecipeVersion, RuntimeDataRecord, TrialRecord, get_session, init_db
from helpers import page_setup, show_advisory_footer

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
        versions = [v for v in grade.recipe_versions] if grade else []
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

st.divider()
st.subheader("Runtime data")
st.caption(
    "Runtime data captures machine/process conditions for a production run. Manual entry, or "
    "structured CSV/Excel import. Live machine connection is future integration — not part of v0.1."
)

runs = session.query(ProductionRun).order_by(ProductionRun.created_at.desc()).all()

if runs:
    tab_manual, tab_import = st.tabs(["Manual entry", "CSV / Excel import"])

    with tab_manual:
        with st.form("add_runtime_manual"):
            run = st.selectbox(
                "Production run *",
                runs,
                format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
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
        uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"])
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

                    if good_rows and st.button("Confirm import"):
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
