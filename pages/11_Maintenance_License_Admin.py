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
from helpers import clickable_table, delete_with_confirm, page_setup

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
    record_rows = [
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
    ]
    st.caption("Click a row to edit (and optionally delete) that commercial record.")
    idx = clickable_table(record_rows, key="maintenance_table")
    if idx is not None:
        st.session_state["maintenance_selected_id"] = records[idx].id

    selected_record_id = st.session_state.get("maintenance_selected_id")
    selected_record = next((r for r in records if r.id == selected_record_id), None)

    if selected_record:
        st.markdown(f"**Edit commercial record: {selected_record.plant.name}**")
        with st.form(f"edit_maintenance_{selected_record.id}"):
            e_plant = st.selectbox(
                "Plant *", plants,
                index=next((i for i, p in enumerate(plants) if p.id == selected_record.plant_id), 0),
                format_func=lambda p: p.name, key=f"edit_maint_plant_{selected_record.id}",
            )
            e_plant_count = st.number_input(
                "Plant count", min_value=1, step=1, value=selected_record.plant_count or 1,
                key=f"edit_maint_count_{selected_record.id}",
            )
            e_installation_type = st.selectbox(
                "Installation / deployment tier", INSTALLATION_TYPES,
                index=INSTALLATION_TYPES.index(selected_record.installation_type) if selected_record.installation_type in INSTALLATION_TYPES else 0,
                key=f"edit_maint_tier_{selected_record.id}",
            )
            e_deployment_type = st.text_input(
                "Deployment type", value=selected_record.deployment_type or "", key=f"edit_maint_deploy_{selected_record.id}"
            )
            e_license_value = st.number_input(
                "License / setup value (EUR)", min_value=0.0, step=500.0,
                value=float(selected_record.license_value or 0.0), key=f"edit_maint_license_{selected_record.id}",
            )
            e_maintenance_pct = st.number_input(
                "Annual maintenance percentage", min_value=0.0, step=0.5,
                value=float(selected_record.annual_maintenance_percentage or 18.0), key=f"edit_maint_pct_{selected_record.id}",
            )
            e_start_date = st.date_input(
                "Maintenance start date", value=selected_record.maintenance_start_date or dt.date.today(),
                key=f"edit_maint_start_{selected_record.id}",
            )
            e_renewal_date = st.date_input(
                "Renewal date", value=selected_record.renewal_date or dt.date.today(),
                key=f"edit_maint_renewal_{selected_record.id}",
            )
            if st.form_submit_button("Save changes"):
                selected_record.plant_id = e_plant.id
                selected_record.plant_count = e_plant_count
                selected_record.installation_type = e_installation_type
                selected_record.deployment_type = e_deployment_type
                selected_record.license_value = e_license_value or None
                selected_record.annual_maintenance_percentage = e_maintenance_pct
                selected_record.annual_maintenance_value = e_license_value * (e_maintenance_pct / 100.0) or None
                selected_record.maintenance_start_date = e_start_date
                selected_record.renewal_date = e_renewal_date
                session.commit()
                st.success("Commercial record updated.")
                st.rerun()

        def _do_delete_maintenance(_session=session, _id=selected_record.id):
            _session.query(MaintenanceLicenseRecord).filter(MaintenanceLicenseRecord.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("maintenance_selected_id", None)

        delete_with_confirm(
            "this commercial record", _do_delete_maintenance, key_prefix=f"maintenance_{selected_record.id}",
            extra_warning="This is a leaf record — deleting it has no other effects.",
        )

        if st.button("Clear selection", key="clear_maintenance_selection"):
            st.session_state.pop("maintenance_selected_id", None)
            st.rerun()

st.caption(
    "Tiering is by plant/installation count and deployment scope only — never by "
    "reduced functionality. PI3/AI connectivity is a separate, optional line item "
    "configured on the PI3/AI Connectivity screen."
)

