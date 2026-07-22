"""Screen 12: Maintenance / License Admin

Commercial/admin model. Tiering principle: tier by plant/installation count
and deployment scope, NOT by reduced functionality. Every standard
deployment carries an 18% annual maintenance default (editable per record,
but defaults to 18%).
"""

import datetime as dt

import streamlit as st

from db import INSTALLATION_TYPES, MaintenanceLicenseRecord, Plant, get_session, init_db
from auth import logout_button, require_login, require_role
from helpers import page_setup

page_setup("Maintenance & License Admin")
init_db()
require_login()
logout_button()
require_role("admin")

st.title("Maintenance & License Admin")
session = get_session()

plants = session.query(Plant).all()
if not plants:
    st.info("Add a plant first.")
    st.stop()

with st.expander("Add / update commercial record", expanded=False):
    with st.form("maintenance_form"):
        plant = st.selectbox("Plant *", plants, format_func=lambda p: p.name)
        plant_count = st.number_input("Plant count", min_value=1, step=1, value=1)
        installation_type = st.selectbox("Installation / deployment tier", INSTALLATION_TYPES)
        deployment_type = st.text_input("Deployment type (e.g. private / restricted company environment)")
        license_value = st.number_input("License / setup value (EUR)", min_value=0.0, step=500.0)
        annual_maintenance_percentage = st.number_input(
            "Annual maintenance percentage", min_value=0.0, step=0.5, value=18.0
        )
        maintenance_start_date = st.date_input("Maintenance start date", value=dt.date.today())
        renewal_date = st.date_input(
            "Renewal date", value=dt.date.today().replace(year=dt.date.today().year + 1)
        )
        submitted = st.form_submit_button("Save")
        if submitted:
            annual_maintenance_value = license_value * (annual_maintenance_percentage / 100.0)
            record = MaintenanceLicenseRecord(
                plant_id=plant.id,
                plant_count=plant_count,
                installation_type=installation_type,
                deployment_type=deployment_type,
                license_value=license_value or None,
                annual_maintenance_percentage=annual_maintenance_percentage,
                annual_maintenance_value=annual_maintenance_value or None,
                maintenance_start_date=maintenance_start_date,
                renewal_date=renewal_date,
            )
            session.add(record)
            session.commit()
            st.success("Commercial record saved.")
            st.rerun()

st.divider()
st.subheader("Commercial records")

records = session.query(MaintenanceLicenseRecord).all()
if not records:
    st.info("No commercial records yet.")
else:
    st.dataframe(
        [
            {
                "Plant": r.plant.name,
                "Plant count": r.plant_count,
                "Tier": r.installation_type,
                "Deployment": r.deployment_type,
                "License value (EUR)": r.license_value,
                "Maintenance %": r.annual_maintenance_percentage,
                "Maintenance value (EUR)": r.annual_maintenance_value,
                "Start": r.maintenance_start_date,
                "Renewal": r.renewal_date,
            }
            for r in records
        ],
        hide_index=True,
        use_container_width=True,
    )

st.caption(
    "Tiering is by plant/installation count and deployment scope only — never by "
    "reduced functionality. PI3/AI connectivity is a separate, optional line item "
    "configured on the PI3/AI Connectivity screen."
)

