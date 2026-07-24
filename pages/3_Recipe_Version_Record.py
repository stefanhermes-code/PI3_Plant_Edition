"""Screen 4: Recipe Version Record (formulation memory)"""

import datetime as dt

import pandas as pd
import streamlit as st

from auth import logout_button, require_login
from cascades import delete_recipe_version_cascade, recipe_version_dependency_counts
from db import (
    APPROVAL_STATUSES,
    FoamGrade,
    RawMaterial,
    RecipeComponent,
    RecipeVersion,
    get_session,
    init_db,
)
from helpers import clickable_table, csv_excel_uploader, delete_with_confirm, page_setup

RECIPE_VERSION_REQUIRED_COLUMNS = ["foam_grade_id", "version_label"]
RECIPE_VERSION_OPTIONAL_COLUMNS = ["effective_date", "change_note", "approval_status", "created_by"]

COMPONENT_REQUIRED_COLUMNS = ["recipe_version_id", "raw_material_name"]
COMPONENT_OPTIONAL_COLUMNS = ["supplier", "php", "role_in_formulation", "notes"]

page_setup("Recipe Version Record")
init_db()
require_login()
logout_button()

st.title("Recipe Version Record")
session = get_session()

grades = session.query(FoamGrade).all()
if not grades:
    st.warning("Add a foam grade first (Product Family & Foam Grade page).")
    st.stop()


def _match_or_create_raw_material(name, supplier=None):
    """Look up a RawMaterial by name (case-insensitive); create one if it
    doesn't exist yet, so anything typed as a "new" material during recipe
    entry becomes available in the master list (and future dropdowns)
    immediately, not just a one-off string on this one component."""
    name = (name or "").strip()
    if not name:
        return None
    match = (
        session.query(RawMaterial)
        .filter(RawMaterial.name.ilike(name))
        .first()
    )
    if match:
        return match
    new_rm = RawMaterial(name=name, category="Other", default_supplier=supplier or "", active=True)
    session.add(new_rm)
    session.flush()
    return new_rm


# ---------------------------------------------------------------------------
# Recipe versions (header record)
# ---------------------------------------------------------------------------
tab_manual, tab_import = st.tabs(["Add recipe version", "CSV / Excel import"])

with tab_manual:
    with st.expander("Add recipe version", expanded=False):
        with st.form("add_recipe_version"):
            grade = st.selectbox("Foam grade *", grades, format_func=lambda g: g.grade_name)
            version_label = st.text_input("Version label * (e.g. 28-MH-05)")
            effective_date = st.date_input("Effective date", value=dt.date.today())
            change_note = st.text_area("Change note (why this version exists) *")
            approval_status = st.selectbox("Approval status", APPROVAL_STATUSES)
            created_by = st.text_input("Created by")
            submitted = st.form_submit_button("Save recipe version")
            if submitted:
                if not version_label or not change_note:
                    st.error("Version label and change note are required.")
                else:
                    session.add(
                        RecipeVersion(
                            foam_grade_id=grade.id,
                            version_label=version_label,
                            effective_date=effective_date,
                            change_note=change_note,
                            approval_status=approval_status,
                            created_by=created_by,
                        )
                    )
                    session.commit()
                    st.success(f"Recipe version '{version_label}' added.")
                    st.rerun()

with tab_import:
    st.caption("Bulk-create recipe version header records (e.g. migrating a formulation library).")
    df, filename = csv_excel_uploader(
        RECIPE_VERSION_REQUIRED_COLUMNS, RECIPE_VERSION_OPTIONAL_COLUMNS, key="recipe_version_upload"
    )
    if df is not None:
        valid_grade_ids = {g.id for g in grades}
        good_rows, bad_rows = [], []
        for _, row in df.iterrows():
            if row.get("foam_grade_id") in valid_grade_ids and str(row.get("version_label", "")).strip():
                good_rows.append(row)
            else:
                bad_rows.append(row)

        st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
        if bad_rows:
            st.warning("Flagged rows reference an unknown foam_grade_id or have no version_label.")
            st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

        if good_rows and st.button("Confirm import", key="confirm_recipe_version_import"):
            for row in good_rows:
                status = str(row.get("approval_status", "") or "").strip()
                eff_date = pd.to_datetime(row.get("effective_date"), errors="coerce")
                session.add(
                    RecipeVersion(
                        foam_grade_id=int(row["foam_grade_id"]),
                        version_label=str(row["version_label"]).strip(),
                        effective_date=eff_date.date() if not pd.isna(eff_date) else None,
                        change_note=str(row.get("change_note", "") or ""),
                        approval_status=status if status in APPROVAL_STATUSES else "Draft",
                        created_by=str(row.get("created_by", "") or ""),
                    )
                )
            session.commit()
            st.success(f"Imported {len(good_rows)} recipe version(s) from {filename}.")
            st.rerun()

st.divider()
st.subheader("Recipe versions")

versions = session.query(RecipeVersion).order_by(RecipeVersion.created_at.desc()).all()
if not versions:
    st.info("No recipe versions recorded yet.")

for v in versions:
    with st.container(border=True):
        st.markdown(f"**{v.version_label}** — {v.foam_grade.grade_name} · status: `{v.approval_status}`")
        st.caption(f"Effective {v.effective_date or '—'} | Created by {v.created_by or '—'}")
        st.write(v.change_note)

        with st.expander("Edit / delete this recipe version"):
            with st.form(f"edit_version_{v.id}"):
                e_grade = st.selectbox(
                    "Foam grade *", grades,
                    index=next((i for i, g in enumerate(grades) if g.id == v.foam_grade_id), 0),
                    format_func=lambda g: g.grade_name, key=f"edit_version_grade_{v.id}",
                )
                e_label = st.text_input("Version label *", value=v.version_label, key=f"edit_version_label_{v.id}")
                e_effective = st.date_input(
                    "Effective date", value=v.effective_date or dt.date.today(), key=f"edit_version_eff_{v.id}"
                )
                e_change_note = st.text_area("Change note *", value=v.change_note or "", key=f"edit_version_note_{v.id}")
                e_status = st.selectbox(
                    "Approval status", APPROVAL_STATUSES,
                    index=APPROVAL_STATUSES.index(v.approval_status) if v.approval_status in APPROVAL_STATUSES else 0,
                    key=f"edit_version_status_{v.id}",
                )
                e_created_by = st.text_input("Created by", value=v.created_by or "", key=f"edit_version_by_{v.id}")
                if st.form_submit_button("Save changes"):
                    if not e_label.strip() or not e_change_note.strip():
                        st.error("Version label and change note are required.")
                    else:
                        v.foam_grade_id = e_grade.id
                        v.version_label = e_label.strip()
                        v.effective_date = e_effective
                        v.change_note = e_change_note
                        v.approval_status = e_status
                        v.created_by = e_created_by
                        session.commit()
                        st.success("Recipe version updated.")
                        st.rerun()

            counts = recipe_version_dependency_counts(session, v.id)
            total_related = sum(counts.values())
            if total_related:
                detail = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
                warning = f"Deleting this recipe version will also permanently delete {total_related} related record(s): {detail}."
            else:
                warning = "This recipe version has no related records — deleting it is safe."

            def _do_delete_version(_session=session, _id=v.id):
                delete_recipe_version_cascade(_session, _id)
                _session.commit()
                st.session_state.pop("rv_selected_id", None)

            delete_with_confirm(
                f"Recipe version '{v.version_label}'", _do_delete_version, key_prefix=f"version_{v.id}",
                extra_warning=warning,
            )

        with st.expander(f"Recipe components ({len(v.components)})"):
            if v.components:
                comp_rows = [
                    {
                        "Raw material": c.raw_material_name,
                        "Supplier": c.supplier,
                        "php": c.php,
                        "Role": c.role_in_formulation,
                        "Notes": c.notes,
                    }
                    for c in v.components
                ]
                st.caption("Click a row to edit (and optionally delete) that component.")
                idx = clickable_table(comp_rows, key=f"components_table_{v.id}")
                if idx is not None:
                    st.session_state["comp_selected_id"] = v.components[idx].id

                selected_comp_id = st.session_state.get("comp_selected_id")
                selected_comp = next((c for c in v.components if c.id == selected_comp_id), None)

                if selected_comp:
                    st.markdown(f"**Edit component: {selected_comp.raw_material_name}**")
                    with st.form(f"edit_component_{selected_comp.id}"):
                        ec1, ec2, ec3 = st.columns(3)
                        e_name = ec1.text_input(
                            "Raw material name", value=selected_comp.raw_material_name, key=f"edit_comp_name_{selected_comp.id}"
                        )
                        e_supplier = ec2.text_input(
                            "Supplier", value=selected_comp.supplier or "", key=f"edit_comp_sup_{selected_comp.id}"
                        )
                        e_php = ec3.number_input(
                            "php", min_value=0.0, step=0.1, value=float(selected_comp.php or 0.0), key=f"edit_comp_php_{selected_comp.id}"
                        )
                        e_role = st.text_input(
                            "Role in formulation", value=selected_comp.role_in_formulation or "", key=f"edit_comp_role_{selected_comp.id}"
                        )
                        e_notes = st.text_input("Notes", value=selected_comp.notes or "", key=f"edit_comp_notes_{selected_comp.id}")
                        if st.form_submit_button("Save changes"):
                            if not e_name.strip():
                                st.error("Raw material name is required.")
                            else:
                                if e_name.strip() != selected_comp.raw_material_name:
                                    rm = _match_or_create_raw_material(e_name, e_supplier)
                                    selected_comp.raw_material_id = rm.id if rm else None
                                selected_comp.raw_material_name = e_name.strip()
                                selected_comp.supplier = e_supplier
                                selected_comp.php = e_php or None
                                selected_comp.role_in_formulation = e_role
                                selected_comp.notes = e_notes
                                session.commit()
                                st.success("Component updated.")
                                st.rerun()

                    def _do_delete_comp(_session=session, _id=selected_comp.id):
                        _session.query(RecipeComponent).filter(RecipeComponent.id == _id).delete(synchronize_session=False)
                        _session.commit()
                        st.session_state.pop("comp_selected_id", None)

                    delete_with_confirm(
                        f"component '{selected_comp.raw_material_name}'", _do_delete_comp,
                        key_prefix=f"comp_{selected_comp.id}",
                        extra_warning="This is a leaf record — deleting it has no other effects.",
                    )

                    if st.button("Clear selection", key=f"clear_comp_selection_{v.id}"):
                        st.session_state.pop("comp_selected_id", None)
                        st.rerun()

            active_raw_materials = (
                session.query(RawMaterial)
                .filter(RawMaterial.active.is_(True))
                .order_by(RawMaterial.name)
                .all()
            )
            raw_material_choice = st.selectbox(
                "Raw material",
                [None] + active_raw_materials,
                format_func=lambda m: "— type a new one below —"
                if m is None
                else (f"{m.name} ({m.category})" if m.category else m.name),
                key=f"rm_select_{v.id}",
            )
            with st.form(f"add_component_{v.id}"):
                c1, c2, c3 = st.columns(3)
                raw_material_other = c1.text_input(
                    "Or a new raw material not in the list above", key=f"rm_other_{v.id}"
                )
                supplier_default = raw_material_choice.default_supplier if raw_material_choice else ""
                supplier = c2.text_input("Supplier", value=supplier_default or "", key=f"sup_{v.id}")
                php = c3.number_input("php", min_value=0.0, step=0.1, key=f"php_{v.id}")
                role = st.text_input(
                    "Role in formulation (e.g. polyol, TDI, catalyst, surfactant)", key=f"role_{v.id}"
                )
                notes = st.text_input("Notes", key=f"notes_{v.id}")
                add_component = st.form_submit_button("Add component")
                if add_component:
                    final_name = raw_material_other.strip() or (
                        raw_material_choice.name if raw_material_choice else ""
                    )
                    if not final_name:
                        st.error("Pick a raw material from the list, or type a new one.")
                    else:
                        if raw_material_other.strip():
                            rm = _match_or_create_raw_material(final_name, supplier)
                        else:
                            rm = raw_material_choice
                        session.add(
                            RecipeComponent(
                                recipe_version_id=v.id,
                                raw_material_id=rm.id if rm else None,
                                raw_material_name=final_name,
                                supplier=supplier,
                                php=php or None,
                                role_in_formulation=role,
                                notes=notes,
                            )
                        )
                        session.commit()
                        st.success("Component added.")
                        st.rerun()

st.divider()
st.subheader("Bulk import recipe components")
st.caption(
    "Import a whole formulation sheet at once. Each row needs the recipe_version_id it belongs to "
    "(see the recipe version list above for IDs) and a raw material name — unmatched raw material "
    "names are automatically added to the Raw Materials master list."
)
comp_df, comp_filename = csv_excel_uploader(
    COMPONENT_REQUIRED_COLUMNS, COMPONENT_OPTIONAL_COLUMNS, key="component_upload"
)
if comp_df is not None:
    valid_version_ids = {v.id for v in versions}
    good_rows, bad_rows = [], []
    for _, row in comp_df.iterrows():
        if row.get("recipe_version_id") in valid_version_ids and str(row.get("raw_material_name", "")).strip():
            good_rows.append(row)
        else:
            bad_rows.append(row)

    st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
    if bad_rows:
        st.warning("Flagged rows reference an unknown recipe_version_id or have no raw_material_name.")
        st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

    if good_rows and st.button("Confirm import", key="confirm_component_import"):
        for row in good_rows:
            name_val = str(row["raw_material_name"]).strip()
            supplier_val = str(row.get("supplier", "") or "")
            rm = _match_or_create_raw_material(name_val, supplier_val)
            session.add(
                RecipeComponent(
                    recipe_version_id=int(row["recipe_version_id"]),
                    raw_material_id=rm.id if rm else None,
                    raw_material_name=name_val,
                    supplier=supplier_val,
                    php=row.get("php") if not pd.isna(row.get("php")) else None,
                    role_in_formulation=str(row.get("role_in_formulation", "") or ""),
                    notes=str(row.get("notes", "") or ""),
                )
            )
        session.commit()
        st.success(f"Imported {len(good_rows)} recipe component(s) from {comp_filename}.")
        st.rerun()

