"""Admin utility (not one of the 12 core screens): load the internal demo
data set on a fresh database, without needing shell/CLI access. Useful the
first time the app is pointed at a brand-new Supabase database.
"""

import streamlit as st

from db import get_session, init_db
from demo_data import already_seeded, seed_demo_data
from auth import logout_button, require_login, require_role
from helpers import page_setup

page_setup("Demo Data Admin")
init_db()
require_login()
logout_button()
require_role("admin")

st.title("Demo Data Admin")
st.caption(
    "Loads the internal demonstration case (hardness drift / shrinkage in a "
    "28 kg/m3 mattress comfort grade) from 04_PI3_Plant_Edition_Demonstration_Case. "
    "No real client data is used."
)

session = get_session()

if already_seeded(session):
    st.success("Demo data is already loaded ('Demo Foam Works' plant exists).")
else:
    if st.button("Load demo data", type="primary"):
        message = seed_demo_data(session)
        st.success(message)
        st.rerun()

