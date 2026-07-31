"""Screen 3: Product Family and Foam Grade Profile"""

import pandas as pd
import streamlit as st

from auth import current_user, logout_button, require_login
from cascades import (
    delete_foam_grade_cascade,
    delete_product_family_cascade,
    foam_grade_dependency_counts,
    product_family_dependency_counts,
)
from db import FoamGrade, FoamGradeTargetProperty, PhysicalPropertyDefinition, Plant, ProductFamily, get_session, init_db
from helpers import (
    clickable_table,
    csv_excel_uploader,
    dedupe_import_rows,
    delete_with_confirm,
    page_setup,
    render_data_table,
    render_function_action_intro,
    set_pending_banner,
    show_pending_banner,
)
from tenant_scope import apply_scope, company_picker, family_ids_for_plants, plant_ids_for_company

GRADE_REQUIRED_COLUMNS = ["product_family_id", "grade_name"]
GRADE_OPTIONAL_COLUMNS = ["target_density", "target_hardness", "notes"]

# Density and 40% IFD/hardness already have dedicated foam_grades columns
# (every grade has them, and the grade-naming code itself encodes them) - so
# they're excluded from the "other target properties" picker below to avoid
# a grade carrying two different numbers for the same property.
DENSITY_HARDNESS_PROPERTY_NAMES = {"density", "40% ifd / hardness"}

page_setup("Product Family & Foam Grade")
init_db()
require_login()
logout_button()

st.title("Product Family & Foam Grade Profile")
render_function_action_intro(
    function_text=(
        "This page organizes your product catalog on two levels: product families (a market "
        "segment or application grouping under a plant, e.g. mattress comfort layer) and the "
        "individual foam grades within each family, each carrying its own target density and "
        "target hardness (40% ILD), plus any other target physical properties worth specifying "
        "(resilience, tensile strength, ...) even before the actual value is known. Every recipe "
        "version, production run, and quality result recorded downstream is tied to one of these "
        "foam grades, so this is where a new grade starts its life in the system."
    ),
    action_text=(
        "Add a product family under the right plant first, then add each foam grade under it one "
        "at a time, or bring in a batch through the CSV/Excel import tab if you're loading many "
        "grades at once. Set target density and hardness on each grade so the Industrial "
        "Intelligence pages have a target to compare actual results against, then use the 'other "
        "target properties' list on the edit screen for anything beyond those two. Click a row in "
        "either table to edit or delete it - deleting a product family or foam grade cascades to "
        "everything recorded under it, with the count shown before you confirm."
    ),
)
session = get_session()
user = current_user()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="pfg_company_filter"
)
company_id = company.id if company else None
plant_ids = plant_ids_for_company(session, company_id)
family_ids = family_ids_for_plants(session, plant_ids)

plants = apply_scope(session.query(Plant), Plant.id, plant_ids).all()
if not plants:
    st.warning("Add a plant first (Plant & Foam Equipment Overview) before creating product families.")
    st.stop()

tab_family, tab_grade = st.tabs(["Product families", "Foam grades"])

with tab_family:
    with st.expander("Add product family"):
        with st.form("add_family"):
            plant = st.selectbox("Plant *", plants, format_func=lambda p: p.name)
            name = st.text_input("Product family name *")
            application = st.text_input("Application (e.g. mattress comfort layer)")
            customer_segment = st.text_input("Customer segment")
            description = st.text_area("Description")
            submitted = st.form_submit_button("Save product family")
            if submitted:
                if not name:
                    st.error("Product family name is required.")
                else:
                    session.add(
                        ProductFamily(
                            plant_id=plant.id,
                            name=name,
                            application=application,
                            customer_segment=customer_segment,
                            description=description,
                        )
                    )
                    session.commit()
                    st.success(f"Product family '{name}' added.")
                    st.rerun()

    st.divider()
    families = apply_scope(session.query(ProductFamily), ProductFamily.plant_id, plant_ids).all()
    if not families:
        st.info("No product families recorded yet.")
    else:
        family_rows = [
            {
                "Name": fam.name,
                "Plant": fam.plant.name,
                "Application": fam.application or "",
                "Customer segment": fam.customer_segment or "",
                "Foam grades": len(fam.foam_grades),
            }
            for fam in families
        ]
        st.caption("Click a row to edit (and optionally delete) that product family.")
        idx = clickable_table(family_rows, key="families_table")
        if idx is not None:
            st.session_state["family_selected_id"] = families[idx].id
        else:
            st.session_state.pop("family_selected_id", None)

        selected_family_id = st.session_state.get("family_selected_id")
        selected_family = next((f for f in families if f.id == selected_family_id), None)

        if selected_family:
            st.markdown(f"**Edit product family: {selected_family.name}**")
            with st.form(f"edit_family_{selected_family.id}"):
                e_plant = st.selectbox(
                    "Plant *", plants,
                    index=next((i for i, p in enumerate(plants) if p.id == selected_family.plant_id), 0),
                    format_func=lambda p: p.name, key=f"edit_family_plant_{selected_family.id}",
                )
                e_name = st.text_input("Product family name *", value=selected_family.name, key=f"edit_family_name_{selected_family.id}")
                e_application = st.text_input(
                    "Application", value=selected_family.application or "", key=f"edit_family_app_{selected_family.id}"
                )
                e_segment = st.text_input(
                    "Customer segment", value=selected_family.customer_segment or "", key=f"edit_family_seg_{selected_family.id}"
                )
                e_description = st.text_area(
                    "Description", value=selected_family.description or "", key=f"edit_family_desc_{selected_family.id}"
                )
                if st.form_submit_button("Save changes"):
                    if not e_name.strip():
                        st.error("Product family name is required.")
                    else:
                        selected_family.plant_id = e_plant.id
                        selected_family.name = e_name.strip()
                        selected_family.application = e_application
                        selected_family.customer_segment = e_segment
                        selected_family.description = e_description
                        session.commit()
                        st.success("Product family updated.")
                        st.rerun()

            counts = product_family_dependency_counts(session, selected_family.id)
            total_related = sum(counts.values())
            if total_related:
                detail = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
                warning = f"Deleting this product family will also permanently delete {total_related} related record(s): {detail}."
            else:
                warning = "This product family has no related records — deleting it is safe."

            def _do_delete_family(_session=session, _id=selected_family.id):
                delete_product_family_cascade(_session, _id)
                _session.commit()
                st.session_state.pop("family_selected_id", None)

            delete_with_confirm(
                f"'{selected_family.name}'", _do_delete_family, key_prefix=f"family_{selected_family.id}",
                extra_warning=warning,
            )

            if st.button("Clear selection", key="clear_family_selection"):
                st.session_state.pop("family_selected_id", None)
                st.rerun()

with tab_grade:
    families = apply_scope(session.query(ProductFamily), ProductFamily.plant_id, plant_ids).all()
    property_defs = (
        session.query(PhysicalPropertyDefinition)
        .order_by(PhysicalPropertyDefinition.is_common.desc(), PhysicalPropertyDefinition.sort_order)
        .all()
    )
    if not families:
        st.warning("Add a product family first.")
    else:
        tab_grade_manual, tab_grade_import = st.tabs(["Add foam grade", "CSV / Excel import"])

        with tab_grade_manual:
            with st.expander("Add foam grade", expanded=False):
                with st.form("add_grade"):
                    family = st.selectbox("Product family *", families, format_func=lambda f: f.name)
                    grade_name = st.text_input("Grade name / code *")
                    target_density = st.number_input("Target density (kg/m3)", min_value=0.0, step=0.5)
                    target_hardness = st.number_input("Target hardness (N, 40% ILD)", min_value=0.0, step=1.0)
                    notes = st.text_area("Notes")
                    submitted = st.form_submit_button("Save foam grade")
                    if submitted:
                        if not grade_name:
                            st.error("Grade name is required.")
                        else:
                            session.add(
                                FoamGrade(
                                    product_family_id=family.id,
                                    grade_name=grade_name,
                                    target_density=target_density or None,
                                    target_hardness=target_hardness or None,
                                    notes=notes,
                                )
                            )
                            session.commit()
                            st.success(f"Foam grade '{grade_name}' added. Add any other target physical "
                                       "properties from the table below.")
                            st.rerun()

        with tab_grade_import:
            show_pending_banner("grade_import_msg")
            df, filename = csv_excel_uploader(GRADE_REQUIRED_COLUMNS, GRADE_OPTIONAL_COLUMNS, key="grade_upload")
            if df is not None:
                valid_family_ids = {f.id for f in families}
                good_rows, bad_rows = [], []
                for _, row in df.iterrows():
                    if row.get("product_family_id") in valid_family_ids and str(row.get("grade_name", "")).strip():
                        good_rows.append(row)
                    else:
                        bad_rows.append(row)

                st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
                if bad_rows:
                    st.warning("Flagged rows reference an unknown product_family_id or have no grade_name.")
                    render_data_table(pd.DataFrame(bad_rows), max_height="300px")

                if good_rows and st.button("Confirm import", key="confirm_grade_import"):
                    existing_keys = {
                        (g.product_family_id, g.grade_name.strip().lower())
                        for g in apply_scope(
                            session.query(FoamGrade), FoamGrade.product_family_id, family_ids
                        ).all()
                    }
                    new_rows, dup_rows = dedupe_import_rows(
                        good_rows,
                        existing_keys,
                        key_func=lambda row: (int(row["product_family_id"]), str(row["grade_name"]).strip().lower()),
                    )
                    for row in new_rows:
                        session.add(
                            FoamGrade(
                                product_family_id=int(row["product_family_id"]),
                                grade_name=str(row["grade_name"]).strip(),
                                target_density=row.get("target_density") if not pd.isna(row.get("target_density")) else None,
                                target_hardness=row.get("target_hardness") if not pd.isna(row.get("target_hardness")) else None,
                                notes=str(row.get("notes", "") or ""),
                            )
                        )
                    session.commit()
                    msg = f"Imported {len(new_rows)} foam grade(s) from {filename}."
                    if dup_rows:
                        msg += f" Skipped {len(dup_rows)} row(s) already recorded for their product family (likely a repeat click)."
                    set_pending_banner("grade_import_msg", msg)
                    st.rerun()

        st.divider()
        grades = apply_scope(session.query(FoamGrade), FoamGrade.product_family_id, family_ids).all()
        if not grades:
            st.info("No foam grades recorded yet.")
        else:
            grade_rows = [
                {
                    "Grade": grade.grade_name,
                    "Family": grade.product_family.name,
                    "Target density (kg/m3)": grade.target_density,
                    "Target hardness (N, 40% ILD)": grade.target_hardness,
                    "Other target properties": len(grade.target_properties),
                }
                for grade in grades
            ]
            st.caption("Click a row to edit (and optionally delete) that foam grade.")
            idx = clickable_table(grade_rows, key="grades_table")
            if idx is not None:
                st.session_state["grade_selected_id"] = grades[idx].id
            else:
                st.session_state.pop("grade_selected_id", None)

            selected_grade_id = st.session_state.get("grade_selected_id")
            selected_grade = next((g for g in grades if g.id == selected_grade_id), None)

            if selected_grade:
                st.markdown(f"**Edit foam grade: {selected_grade.grade_name}**")
                with st.form(f"edit_grade_{selected_grade.id}"):
                    e_family = st.selectbox(
                        "Product family *", families,
                        index=next((i for i, f in enumerate(families) if f.id == selected_grade.product_family_id), 0),
                        format_func=lambda f: f.name, key=f"edit_grade_family_{selected_grade.id}",
                    )
                    e_grade_name = st.text_input(
                        "Grade name / code *", value=selected_grade.grade_name, key=f"edit_grade_name_{selected_grade.id}"
                    )
                    e_density = st.number_input(
                        "Target density (kg/m3)", min_value=0.0, step=0.5,
                        value=float(selected_grade.target_density or 0.0), key=f"edit_grade_density_{selected_grade.id}",
                    )
                    e_hardness = st.number_input(
                        "Target hardness (N, 40% ILD)", min_value=0.0, step=1.0,
                        value=float(selected_grade.target_hardness or 0.0), key=f"edit_grade_hardness_{selected_grade.id}",
                    )
                    e_notes = st.text_area("Notes", value=selected_grade.notes or "", key=f"edit_grade_notes_{selected_grade.id}")
                    if st.form_submit_button("Save changes"):
                        if not e_grade_name.strip():
                            st.error("Grade name is required.")
                        else:
                            selected_grade.product_family_id = e_family.id
                            selected_grade.grade_name = e_grade_name.strip()
                            selected_grade.target_density = e_density or None
                            selected_grade.target_hardness = e_hardness or None
                            selected_grade.notes = e_notes
                            session.commit()
                            st.success("Foam grade updated.")
                            st.rerun()

                st.markdown("**Other target physical properties**")
                st.caption(
                    "Anything beyond density and hardness this grade needs to hit (resilience, "
                    "tensile strength, ...). Leave 'Target value' blank if it's a property to track "
                    "but the number isn't known/agreed yet. Edit the table directly, then save."
                )
                property_choices = sorted(
                    p.name for p in property_defs if p.name.strip().lower() not in DENSITY_HARDNESS_PROPERTY_NAMES
                )
                target_props_df = (
                    pd.DataFrame(
                        [
                            {
                                "Property": tp.property_name,
                                "Target value": tp.target_value,
                                "Unit": tp.unit or "",
                                "Notes": tp.notes or "",
                            }
                            for tp in selected_grade.target_properties
                        ]
                    )
                    if selected_grade.target_properties
                    else pd.DataFrame(columns=["Property", "Target value", "Unit", "Notes"])
                )
                edited_props_df = st.data_editor(
                    target_props_df,
                    num_rows="dynamic",
                    use_container_width=True,
                    key=f"edit_target_properties_{selected_grade.id}",
                    column_config={
                        "Property": st.column_config.SelectboxColumn("Property", options=property_choices),
                        "Target value": st.column_config.NumberColumn("Target value", step=0.1),
                    },
                )
                if st.button("Save other target properties", key=f"save_target_properties_{selected_grade.id}"):
                    defs_by_name = {p.name.strip().lower(): p for p in property_defs}
                    session.query(FoamGradeTargetProperty).filter(
                        FoamGradeTargetProperty.foam_grade_id == selected_grade.id
                    ).delete(synchronize_session=False)
                    for _, row in edited_props_df.iterrows():
                        prop_name = str(row.get("Property") or "").strip()
                        if not prop_name:
                            continue
                        prop_def = defs_by_name.get(prop_name.lower())
                        session.add(
                            FoamGradeTargetProperty(
                                foam_grade_id=selected_grade.id,
                                property_definition_id=prop_def.id if prop_def else None,
                                property_name=prop_name,
                                target_value=row.get("Target value") if pd.notna(row.get("Target value")) else None,
                                unit=str(row.get("Unit") or ""),
                                notes=str(row.get("Notes") or ""),
                            )
                        )
                    session.commit()
                    st.success("Other target properties updated.")
                    st.rerun()

                counts = foam_grade_dependency_counts(session, selected_grade.id)
                total_related = sum(counts.values())
                if total_related:
                    detail = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
                    warning = f"Deleting this foam grade will also permanently delete {total_related} related record(s): {detail}."
                else:
                    warning = "This foam grade has no related records — deleting it is safe."

                def _do_delete_grade(_session=session, _id=selected_grade.id):
                    delete_foam_grade_cascade(_session, _id)
                    _session.commit()
                    st.session_state.pop("grade_selected_id", None)

                delete_with_confirm(
                    f"'{selected_grade.grade_name}'", _do_delete_grade, key_prefix=f"grade_{selected_grade.id}",
                    extra_warning=warning,
                )

                if st.button("Clear selection", key="clear_grade_selection"):
                    st.session_state.pop("grade_selected_id", None)
                    st.rerun()

