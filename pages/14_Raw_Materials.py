"""Screen: Raw Materials (master data)

A master list of raw materials so recipes can be built from a dropdown
instead of retyping the same material name (and its supplier) into every
recipe component. Supports manual entry and bulk CSV/Excel import, since
material lists commonly already exist as an ERP/supplier export.
"""

import pandas as pd
import streamlit as st

import ai_assistant
from auth import logout_button, require_login
from db import RAW_MATERIAL_CATEGORIES, RawMaterial, RecipeComponent, get_session, init_db
from helpers import (
    clickable_table,
    csv_excel_uploader,
    delete_with_confirm,
    page_setup,
    parse_bool,
    set_pending_banner,
    show_pending_banner,
)


def _extract_pdf_text(uploaded_file):
    """Best-effort text extraction from an uploaded PDF (TDS or SDS).
    Returns "" on any failure rather than raising, since a badly-scanned
    or image-only PDF shouldn't crash the page - the user still has the
    manual entry tab as a fallback."""
    try:
        import pdfplumber

        with pdfplumber.open(uploaded_file) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return ""

RAW_MATERIAL_REQUIRED_COLUMNS = ["name"]
RAW_MATERIAL_OPTIONAL_COLUMNS = ["category", "default_supplier", "cost_per_kg", "notes", "active"]

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

tab_manual, tab_tds, tab_import = st.tabs(["Manual entry", "Add from TDS", "CSV / Excel import"])

with tab_manual:
    with st.form("add_raw_material"):
        name = st.text_input("Raw material name *")
        c1, c2, c3 = st.columns(3)
        category = c1.selectbox("Category", RAW_MATERIAL_CATEGORIES)
        default_supplier = c2.text_input("Default supplier")
        cost_per_kg = c3.number_input(
            "Cost per kg",
            min_value=0.0,
            step=0.01,
            value=0.0,
            help="Leave at 0 if not known yet - recipe cost calculations skip materials with no cost recorded "
            "rather than treating them as free.",
        )
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
                        cost_per_kg=cost_per_kg or None,
                        notes=notes,
                        active=active,
                    )
                )
                session.commit()
                st.success(f"Raw material '{name}' added.")
                st.rerun()

with tab_tds:
    st.caption(
        "Upload a supplier technical data sheet (TDS) to prefill the fields below instead of "
        "retyping them. An SDS is optional and only adds handling/hazard notes."
    )
    tds_file = st.file_uploader("Technical data sheet (PDF) *", type=["pdf"], key="tds_upload")
    sds_file = st.file_uploader("Safety data sheet (PDF, optional)", type=["pdf"], key="sds_upload")

    if tds_file is not None:
        if st.button("Extract from document(s)", key="extract_tds_btn"):
            tds_text = _extract_pdf_text(tds_file)
            sds_text = _extract_pdf_text(sds_file) if sds_file is not None else None
            if not tds_text.strip():
                st.warning(
                    "Could not read any text from this PDF (it may be a scanned image). "
                    "Use Manual entry instead."
                )
            elif ai_assistant.openai_key_configured():
                with st.spinner("Using PI3 to read the document..."):
                    extracted = ai_assistant.extract_raw_material_from_tds(tds_text, sds_text)
                if extracted:
                    st.session_state["tds_extracted"] = extracted
                    st.success("Extracted - review and adjust the fields below, then save.")
            else:
                # No OpenAI key configured: hand the operator the raw text to
                # copy from by hand rather than offering a feature that can't run.
                st.session_state["tds_extracted"] = {
                    "name": "",
                    "category": "",
                    "default_supplier": "",
                    "notes": tds_text[:2000],
                }
                st.info(
                    "PI3 isn't configured on this deployment, so fields can't be auto-filled. "
                    "The extracted document text is in Notes below for you to copy from."
                )

    tds_extracted = st.session_state.get("tds_extracted", {})
    with st.form("add_raw_material_from_tds"):
        t_name = st.text_input("Raw material name *", value=tds_extracted.get("name", ""))
        tc1, tc2 = st.columns(2)
        tds_category = tds_extracted.get("category", "")
        t_category = tc1.selectbox(
            "Category",
            RAW_MATERIAL_CATEGORIES,
            index=RAW_MATERIAL_CATEGORIES.index(tds_category) if tds_category in RAW_MATERIAL_CATEGORIES else 0,
        )
        t_supplier = tc2.text_input("Default supplier", value=tds_extracted.get("default_supplier", ""))
        t_cost = st.number_input(
            "Cost per kg",
            min_value=0.0,
            step=0.01,
            value=0.0,
            help="A TDS doesn't carry pricing - enter it here if you know it, or leave at 0 and add it later.",
        )
        t_notes = st.text_area("Notes", value=tds_extracted.get("notes", ""), height=150)
        t_active = st.checkbox("Active", value=True, key="tds_active")
        if st.form_submit_button("Save raw material (from TDS)"):
            if not t_name.strip():
                st.error("Raw material name is required.")
            else:
                session.add(
                    RawMaterial(
                        name=t_name.strip(),
                        category=t_category,
                        default_supplier=t_supplier,
                        cost_per_kg=t_cost or None,
                        notes=t_notes,
                        active=t_active,
                    )
                )
                session.commit()
                st.session_state.pop("tds_extracted", None)
                st.success(f"Raw material '{t_name}' added.")
                st.rerun()

with tab_import:
    show_pending_banner("rawmat_import_msg")
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
                cost_val = row.get("cost_per_kg")
                session.add(
                    RawMaterial(
                        name=str(row["name"]).strip(),
                        category=cat if cat in RAW_MATERIAL_CATEGORIES else (cat or "Other"),
                        default_supplier=str(row.get("default_supplier", "") or ""),
                        cost_per_kg=float(cost_val) if not pd.isna(cost_val) else None,
                        notes=str(row.get("notes", "") or ""),
                        active=True if pd.isna(row.get("active")) else parse_bool(row.get("active")),
                    )
                )
            session.commit()
            set_pending_banner("rawmat_import_msg", f"Imported {len(good_rows)} raw material(s) from {filename}.")
            st.rerun()

st.divider()
st.subheader("Raw materials")

materials = session.query(RawMaterial).order_by(RawMaterial.name).all()
if not materials:
    st.info("No raw materials recorded yet.")
else:
    df = pd.DataFrame(
        [
            {
                "Name": m.name,
                "Category": m.category or "—",
                "Default supplier": m.default_supplier or "",
                "Cost/kg": m.cost_per_kg,
                "Active": m.active,
                "Notes": m.notes or "",
            }
            for m in materials
        ]
    )

    st.caption("Filter by column:")
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    name_filter = c1.text_input("Name contains", key="rawmat_filter_name")
    category_filter = c2.multiselect(
        "Category", sorted(df["Category"].unique()), key="rawmat_filter_category"
    )
    supplier_filter = c3.text_input("Supplier contains", key="rawmat_filter_supplier")
    active_filter = c4.selectbox("Active", ["All", "Yes", "No"], key="rawmat_filter_active")
    notes_filter = st.text_input("Notes contains", key="rawmat_filter_notes")

    mask = pd.Series(True, index=df.index)
    if name_filter:
        mask &= df["Name"].str.contains(name_filter, case=False, na=False)
    if category_filter:
        mask &= df["Category"].isin(category_filter)
    if supplier_filter:
        mask &= df["Default supplier"].str.contains(supplier_filter, case=False, na=False)
    if active_filter == "Yes":
        mask &= df["Active"]
    elif active_filter == "No":
        mask &= ~df["Active"]
    if notes_filter:
        mask &= df["Notes"].str.contains(notes_filter, case=False, na=False)

    filtered_materials = [m for m, keep in zip(materials, mask) if keep]
    filtered_df = df[mask]

    st.caption(
        f"Showing {len(filtered_df)} of {len(df)} raw material(s). "
        "Click a row to edit (and optionally delete) that material."
    )
    idx = clickable_table(filtered_df.to_dict("records"), key="rawmat_table")
    if idx is not None:
        st.session_state["rawmat_selected_id"] = filtered_materials[idx].id
    else:
        st.session_state.pop("rawmat_selected_id", None)

    selected_id = st.session_state.get("rawmat_selected_id")
    selected = next((m for m in materials if m.id == selected_id), None)

    if selected:
        st.divider()
        st.subheader(f"Edit: {selected.name}")
        with st.form(f"edit_rawmat_{selected.id}"):
            e_name = st.text_input("Raw material name *", value=selected.name, key=f"edit_rawmat_name_{selected.id}")
            ec1, ec2, ec3 = st.columns(3)
            e_category = ec1.selectbox(
                "Category",
                RAW_MATERIAL_CATEGORIES,
                index=RAW_MATERIAL_CATEGORIES.index(selected.category) if selected.category in RAW_MATERIAL_CATEGORIES else 0,
                key=f"edit_rawmat_category_{selected.id}",
            )
            e_supplier = ec2.text_input(
                "Default supplier", value=selected.default_supplier or "", key=f"edit_rawmat_supplier_{selected.id}"
            )
            e_cost = ec3.number_input(
                "Cost per kg", min_value=0.0, step=0.01, value=float(selected.cost_per_kg or 0.0),
                key=f"edit_rawmat_cost_{selected.id}",
            )
            e_notes = st.text_area("Notes", value=selected.notes or "", key=f"edit_rawmat_notes_{selected.id}")
            e_active = st.checkbox("Active", value=selected.active, key=f"edit_rawmat_active_{selected.id}")
            if st.form_submit_button("Save changes"):
                if not e_name.strip():
                    st.error("Raw material name is required.")
                else:
                    selected.name = e_name.strip()
                    selected.category = e_category
                    selected.default_supplier = e_supplier
                    selected.cost_per_kg = e_cost or None
                    selected.notes = e_notes
                    selected.active = e_active
                    session.commit()
                    st.success("Raw material updated.")
                    st.rerun()

        linked_components = (
            session.query(RecipeComponent).filter(RecipeComponent.raw_material_id == selected.id).count()
        )
        if linked_components:
            warning = (
                f"{linked_components} recipe component(s) reference this raw material. Deleting it will unlink "
                "them (their component name/role stays, but the raw-material link is cleared) rather than "
                "deleting those recipe components."
            )
        else:
            warning = "No recipe components reference this raw material — deleting it is safe."

        def _do_delete_rawmat(_session=session, _id=selected.id):
            _session.query(RecipeComponent).filter(RecipeComponent.raw_material_id == _id).update(
                {"raw_material_id": None}, synchronize_session="fetch"
            )
            _session.query(RawMaterial).filter(RawMaterial.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("rawmat_selected_id", None)

        delete_with_confirm(
            selected.name, _do_delete_rawmat, key_prefix=f"rawmat_{selected.id}", extra_warning=warning
        )

        if st.button("Clear selection", key="clear_rawmat_selection"):
            st.session_state.pop("rawmat_selected_id", None)
            st.rerun()

