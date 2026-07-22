"""Screen 11: PI3/AI Connectivity (placeholder)

Standard version (always included): Search, Compare, Retrieve, Structure,
Report, Review and Approval.

Optional PI3/AI connectivity (this screen): Assisted interpretation,
question answering, advisory comparison, company-specific knowledge
interface. Separate annual fee. Disabled unless explicitly enabled in
admin settings. Even when enabled, final decisions require human review
and approval — no autonomous formulation commands, ever.

This is a placeholder in v0.1: the toggle and commercial fields exist, but
no PI3/AI reasoning layer is implemented yet.
"""

import datetime as dt

import streamlit as st

from db import Plant, PI3AIConnectionSetting, get_session, init_db
from auth import current_user, logout_button, require_login, require_role
from helpers import page_setup

page_setup("PI3/AI Connectivity")
init_db()
require_login()
logout_button()

st.title("PI3/AI Connectivity")
st.warning(
    "Placeholder screen. Standard PI3 Plant Edition (search, compare, retrieve, "
    "structure, report, review and approval) is fully available without this add-on. "
    "PI3/AI connectivity is optional, separately billed, and disabled by default."
)

session = get_session()
plants = session.query(Plant).all()
if not plants:
    st.info("Add a plant first.")
    st.stop()

plant = st.selectbox("Plant", plants, format_func=lambda p: p.name)
setting = (
    session.query(PI3AIConnectionSetting).filter(PI3AIConnectionSetting.plant_id == plant.id).first()
)

if setting:
    st.metric("Status", setting.pi3_ai_status)
    st.write(f"Annual fee: {'EUR ' + str(setting.pi3_ai_annual_fee) if setting.pi3_ai_annual_fee else '—'}")
    if setting.pi3_ai_connectivity_enabled:
        st.success(f"Enabled by {setting.enabled_by} on {setting.enabled_at}")
    else:
        st.info("Currently disabled for this plant.")
else:
    st.info("Not yet configured for this plant. Default status: Disabled.")

st.divider()
require_role("admin")
st.subheader("Admin: configure PI3/AI connectivity")

with st.form("pi3_ai_settings"):
    enabled = st.toggle("Enable PI3/AI connectivity for this plant", value=setting.pi3_ai_connectivity_enabled if setting else False)
    annual_fee = st.number_input(
        "PI3/AI annual fee (EUR)", min_value=0.0, step=500.0,
        value=float(setting.pi3_ai_annual_fee) if setting and setting.pi3_ai_annual_fee else 0.0,
    )
    submitted = st.form_submit_button("Save")
    if submitted:
        user = current_user()
        if setting is None:
            setting = PI3AIConnectionSetting(plant_id=plant.id)
            session.add(setting)
        setting.pi3_ai_connectivity_enabled = enabled
        setting.pi3_ai_status = "Enabled" if enabled else "Disabled"
        setting.pi3_ai_annual_fee = annual_fee or None
        if enabled:
            setting.enabled_by = user["display_name"]
            setting.enabled_at = dt.datetime.utcnow()
        session.commit()
        st.success("PI3/AI connectivity settings saved.")
        st.rerun()

st.caption(
    "Even with PI3/AI connectivity enabled, all final decisions require human review "
    "and approval on the Approval & Review screen. No autonomous formulation commands."
)

