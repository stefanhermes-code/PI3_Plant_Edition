"""Screen 2: Plant & Foam Equipment Overview"""

import streamlit as st

from auth import current_user, logout_button, require_login
from db import MACHINE_OEMS, Machine, Plant, get_session, init_db
from helpers import page_setup

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
    for plant in plants:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(f"**{plant.name}**  \nCode: {plant.plant_code or '—'} | Location: {plant.location or '—'}")
            c1.caption(plant.notes or "")
            c2.metric("Product families", len(plant.product_families))
            machine_count = session.query(Machine).filter(Machine.plant_id == plant.id).count()
            c3.metric("Machines", machine_count)

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
        st.dataframe(
            [
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
            ],
            hide_index=True,
            use_container_width=True,
        )

