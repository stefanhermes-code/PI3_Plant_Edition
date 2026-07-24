"""Screen 2: Plant & Foam Equipment Overview"""

import streamlit as st

from auth import current_user, logout_button, require_login
from cascades import delete_plant_cascade, plant_dependency_counts
from db import MACHINE_OEMS, Machine, Plant, ProductionRun, get_session, init_db
from helpers import clickable_table, delete_with_confirm, page_setup

page_setup("Plant & Foam Equipment Overview")
init_db()
require_login()
logout_button()

st.title("Plant & Foam Equipment Overview")
session = get_session()
user = current_user()

with st.expander("Add plant", expanded=False):
    with st.form("add_plant"):
        name = st.text_input("Plant name *")
        plant_code = st.text_input("Plant code")
        location = st.text_input("Location")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save plant")
        if submitted:
            if not name:
                st.error("Plant name is required.")
            else:
                session.add(Plant(name=name, plant_code=plant_code, location=location, notes=notes))
                session.commit()
                st.success(f"Plant '{name}' added.")
                st.rerun()

st.divider()
st.subheader("Plants")

plants = session.query(Plant).all()
if not plants:
    st.info("No plants recorded yet.")
else:
    plant_rows = [
        {
            "Name": plant.name,
            "Code": plant.plant_code or "—",
            "Location": plant.location or "—",
            "Product families": len(plant.product_families),
            "Machines": session.query(Machine).filter(Machine.plant_id == plant.id).count(),
            "Notes": plant.notes or "",
        }
        for plant in plants
    ]
    st.caption("Click a row to edit (and optionally delete) that plant.")
    idx = clickable_table(plant_rows, key="plants_table")
    if idx is not None:
        st.session_state["plant_selected_id"] = plants[idx].id

    selected_plant_id = st.session_state.get("plant_selected_id")
    selected_plant = next((p for p in plants if p.id == selected_plant_id), None)

    if selected_plant:
        st.markdown(f"**Edit plant: {selected_plant.name}**")
        with st.form(f"edit_plant_{selected_plant.id}"):
            e_name = st.text_input("Plant name *", value=selected_plant.name, key=f"edit_plant_name_{selected_plant.id}")
            e_code = st.text_input("Plant code", value=selected_plant.plant_code or "", key=f"edit_plant_code_{selected_plant.id}")
            e_location = st.text_input("Location", value=selected_plant.location or "", key=f"edit_plant_loc_{selected_plant.id}")
            e_notes = st.text_area("Notes", value=selected_plant.notes or "", key=f"edit_plant_notes_{selected_plant.id}")
            if st.form_submit_button("Save changes"):
                if not e_name.strip():
                    st.error("Plant name is required.")
                else:
                    selected_plant.name = e_name.strip()
                    selected_plant.plant_code = e_code
                    selected_plant.location = e_location
                    selected_plant.notes = e_notes
                    session.commit()
                    st.success("Plant updated.")
                    st.rerun()

        counts = plant_dependency_counts(session, selected_plant.id)
        total_related = sum(counts.values())
        if total_related:
            detail = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
            warning = f"Deleting this plant will also permanently delete {total_related} related record(s): {detail}."
        else:
            warning = "This plant has no related records — deleting it is safe."

        def _do_delete_plant(_session=session, _id=selected_plant.id):
            delete_plant_cascade(_session, _id)
            _session.commit()
            st.session_state.pop("plant_selected_id", None)

        delete_with_confirm(
            f"'{selected_plant.name}'", _do_delete_plant, key_prefix=f"plant_{selected_plant.id}",
            extra_warning=warning,
        )

        if st.button("Clear selection", key="clear_plant_selection"):
            st.session_state.pop("plant_selected_id", None)
            st.rerun()

st.divider()
st.subheader("Machines / foaming lines")
st.caption(
    "So process parameters (conveyor speed, sidewall width, laydown mode, etc.) connect to the actual "
    "equipment that produced them, not just a plant. A production run picks one of these."
)

if not plants:
    st.info("Add a plant first before adding machines.")
else:
    with st.expander("Add machine / foaming line", expanded=False):
        with st.form("add_machine"):
            plant_for_machine = st.selectbox("Plant *", plants, format_func=lambda p: p.name)
            name = st.text_input("Machine / line name * (e.g. Line 1, Maxfoam A)")
            machine_code = st.text_input("Machine code")
            oem = st.selectbox("OEM / manufacturer", MACHINE_OEMS)
            model = st.text_input("Model")
            active = st.checkbox("Active", value=True)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save machine")
            if submitted:
                if not name:
                    st.error("Machine / line name is required.")
                else:
                    session.add(
                        Machine(
                            plant_id=plant_for_machine.id,
                            name=name,
                            machine_code=machine_code,
                            oem=oem,
                            model=model,
                            active=active,
                            notes=notes,
                        )
                    )
                    session.commit()
                    st.success(f"Machine '{name}' added.")
                    st.rerun()

    machines = session.query(Machine).order_by(Machine.plant_id, Machine.name).all()
    if not machines:
        st.info("No machines recorded yet.")
    else:
        machine_rows = [
            {
                "Plant": m.plant.name,
                "Machine": m.name,
                "Code": m.machine_code or "—",
                "OEM": m.oem or "—",
                "Model": m.model or "—",
                "Active": m.active,
                "Notes": m.notes or "",
            }
            for m in machines
        ]
        st.caption("Click a row to edit (and optionally delete) that machine.")
        idx = clickable_table(machine_rows, key="machines_table")
        if idx is not None:
            st.session_state["machine_selected_id"] = machines[idx].id

        selected_machine_id = st.session_state.get("machine_selected_id")
        selected_machine = next((m for m in machines if m.id == selected_machine_id), None)

        if selected_machine:
            st.markdown(f"**Edit machine: {selected_machine.name}**")
            with st.form(f"edit_machine_{selected_machine.id}"):
                e_plant = st.selectbox(
                    "Plant *", plants,
                    index=next((i for i, p in enumerate(plants) if p.id == selected_machine.plant_id), 0),
                    format_func=lambda p: p.name, key=f"edit_machine_plant_{selected_machine.id}",
                )
                e_name = st.text_input("Machine / line name *", value=selected_machine.name, key=f"edit_machine_name_{selected_machine.id}")
                e_code = st.text_input(
                    "Machine code", value=selected_machine.machine_code or "", key=f"edit_machine_code_{selected_machine.id}"
                )
                e_oem = st.selectbox(
                    "OEM / manufacturer", MACHINE_OEMS,
                    index=MACHINE_OEMS.index(selected_machine.oem) if selected_machine.oem in MACHINE_OEMS else 0,
                    key=f"edit_machine_oem_{selected_machine.id}",
                )
                e_model = st.text_input("Model", value=selected_machine.model or "", key=f"edit_machine_model_{selected_machine.id}")
                e_active = st.checkbox("Active", value=selected_machine.active, key=f"edit_machine_active_{selected_machine.id}")
                e_notes = st.text_area("Notes", value=selected_machine.notes or "", key=f"edit_machine_notes_{selected_machine.id}")
                if st.form_submit_button("Save changes"):
                    if not e_name.strip():
                        st.error("Machine / line name is required.")
                    else:
                        selected_machine.plant_id = e_plant.id
                        selected_machine.name = e_name.strip()
                        selected_machine.machine_code = e_code
                        selected_machine.oem = e_oem
                        selected_machine.model = e_model
                        selected_machine.active = e_active
                        selected_machine.notes = e_notes
                        session.commit()
                        st.success("Machine updated.")
                        st.rerun()

            linked_runs = session.query(ProductionRun).filter(ProductionRun.machine_id == selected_machine.id).count()
            if linked_runs:
                warning = (
                    f"{linked_runs} production run(s) reference this machine. Deleting it will unlink them "
                    "(the runs stay, the machine reference is cleared), not delete those runs."
                )
            else:
                warning = "No production runs reference this machine — deleting it is safe."

            def _do_delete_machine(_session=session, _id=selected_machine.id):
                _session.query(ProductionRun).filter(ProductionRun.machine_id == _id).update(
                    {"machine_id": None}, synchronize_session="fetch"
                )
                _session.query(Machine).filter(Machine.id == _id).delete(synchronize_session=False)
                _session.commit()
                st.session_state.pop("machine_selected_id", None)

            delete_with_confirm(
                f"'{selected_machine.name}'", _do_delete_machine, key_prefix=f"machine_{selected_machine.id}",
                extra_warning=warning,
            )

            if st.button("Clear selection", key="clear_machine_selection"):
                st.session_state.pop("machine_selected_id", None)
                st.rerun()

