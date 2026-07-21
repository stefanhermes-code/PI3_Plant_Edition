"""Screen: Raw Materials (master data)

A master list of raw materials so recipes can be built from a dropdown
instead of retyping the same material name (and its supplier) into every
recipe component. Supports manual entry and bulk CSV/Excel import, since
material lists commonly already exist as an ERP/supplier export.
"""

import pandas as pd
import streamlit as st

from auth import logout_button, require_login
from db import RAW_MATERIAL_CATEGORIES, RawMaterial, get_session, init_db
from helpers import csv_excel_uploader, page_setup, parse_bool, show_advisory_footer

RAW_MATERIAL_REQUIRED_COLUMNS = ["name"]
RAW_MATERIAL_OPTIONAL_COLUMNS = ["category", "default_supplier", "notes", "active"]

page_setup("Raw Materials")
init_db()
require_login()
logout_button()

st.title("Raw Materials")
st.caption(
    "Master list of raw materials used across recipes (polyols, isocyanates, catalysts, "
    "surfactants, additives, ...). Recipe components pick from this list, or add a new "
    "material inline if it isn't here yet."
)
session = get_session()

tab_manual, tab_import = st.tabs(["Manual entry", "CSV / Excel import"])

with tab_manual:
    with st.form("add_raw_material"):
        name = st.text_input("Raw material name *")
        c1, c2 = st.columns(2)
        category = c1.selectbox("Category", RAW_MATERIAL_CATEGORIES)
        default_supplier = c2.text_input("Default supplier")
        notes = st.text_area("Notes")
        active = st.checkbox("Active", value=True)
        submitted = st.form_submit_button("Save raw material")
        if submitted:
            if not name.strip():
                st.error("Raw material name is required.")
            else:
                session.add(
                    RawMaterial(
                        name=name.strip(),
                        category=category,
                        default_supplier=default_supplier,
                        notes=notes,
                        active=active,
                    )
                )
                session.commit()
                st.success(f"Raw material '{name}' added.")
                st.rerun()

with tab_import:
    df, filename = csv_excel_uploader(RAW_MATERIAL_REQUIRED_COLUMNS, RAW_MATERIAL_OPTIONAL_COLUMNS, key="rawmat_upload")
    if df is not None:
        existing_names = {m.name.strip().lower() for m in session.query(RawMaterial).all()}
        good_rows, dup_rows = [], []
        for _, row in df.iterrows():
            name_val = str(row.get("name", "") or "").strip()
            if not name_val:
                continue
            if name_val.lower() in existing_names:
                dup_rows.append(row)
            else:
                good_rows.append(row)
                existing_names.add(name_val.lower())

        st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged as duplicates: **{len(dup_rows)}**")
        if dup_rows:
            st.warning("These rows match a raw material name already in the list and were skipped.")
            st.dataframe(pd.DataFrame(dup_rows), use_container_width=True)

        if good_rows and st.button("Confirm import", key="confirm_rawmat_import"):
            for row in good_rows:
                cat = str(row.get("category", "") or "").strip()
                session.add(
                    RawMaterial(
                        name=str(row["name"]).strip(),
                        category=cat if cat in RAW_MATERIAL_CATEGORIES else (cat or "Other"),
                        default_supplier=str(row.get("default_supplier", "") or ""),
                        notes=str(row.get("notes", "") or ""),
                        active=True if pd.isna(row.get("active")) else parse_bool(row.get("active")),
                    )
                )
            session.commit()
            st.success(f"Imported {len(good_rows)} raw material(s) from {filename}.")
            st.rerun()

st.divider()
st.subheader("Raw materials")

materials = session.query(RawMaterial).order_by(RawMaterial.name).all()
if not materials:
    st.info("No raw materials recorded yet.")
else:
    st.dataframe(
        [
            {
                "Name": m.name,
                "Category": m.category,
                "Default supplier": m.default_supplier,
                "Active": m.active,
                "Notes": m.notes,
            }
            for m in materials
        ],
        hide_index=True,
        use_container_width=True,
    )

show_advisory_footer()
