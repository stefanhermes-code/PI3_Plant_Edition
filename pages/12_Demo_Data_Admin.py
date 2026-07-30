"""Admin utility (not one of the 12 core screens): load the internal demo
data set on a fresh database, without needing shell/CLI access. Useful the
first time the app is pointed at a brand-new Supabase database.
"""

import streamlit as st

from db import get_session, init_db
from demo_data import already_seeded, seed_demo_data
from auth import logout_button, require_login, require_role
from helpers import page_setup, render_function_action_intro

page_setup("Demo Data Admin")
init_db()
require_login()
logout_button()
require_role("admin")

st.title("Demo Data Admin")
render_function_action_intro(
    function_text=(
        "Loads a self-contained internal demonstration case - a hardness-drift/shrinkage "
        "investigation on a 28 kg/m3 mattress comfort grade, with a full plant, recipe, "
        "production runs, trials, and closed-out conclusions - into a brand-new database, so a "
        "fresh deployment has something realistic to click through without needing shell or CLI "
        "access. No real client data is used."
    ),
    action_text=(
        "Click 'Load demo data' on a fresh deployment. The button disappears once the 'Demo Foam "
        "Works' plant already exists, so there's no risk of loading it twice."
    ),
)

session = get_session()

if already_seeded(session):
    st.success("Demo data is already loaded ('Demo Foam Works' plant exists).")
else:
    if st.button("Load demo data", type="primary"):
        message = seed_demo_data(session)
        st.success(message)
        st.rerun()

