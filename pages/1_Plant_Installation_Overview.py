"""Screen 2: Plant / Installation Overview"""

import streamlit as st

from auth import current_user, logout_button, require_login
from db import Plant, get_session, init_db
from helpers import page_setup, show_advisory_footer

page_setup("Plant / Installation Overview")
init_db()
require_login()
logout_button()

st.title("Plant / Installation Overview")
session = get_session()
user = current_user()

with st.expander("Add plant / installation", expanded=False):
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
st.subheader("Plants / installations")

plants = session.query(Plant).all()
if not plants:
    st.info("No plants recorded yet.")
else:
    for plant in plants:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{plant.name}**  \nCode: {plant.plant_code or '—'} | Location: {plant.location or '—'}")
            c1.caption(plant.notes or "")
            c2.metric("Product families", len(plant.product_families))

show_advisory_footer()
