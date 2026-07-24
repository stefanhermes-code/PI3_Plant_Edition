"""Screen 3: Product Family and Foam Grade Profile"""

import pandas as pd
import streamlit as st

from auth import logout_button, require_login
from cascades import (
    delete_foam_grade_cascade,
    delete_product_family_cascade,
    foam_grade_dependency_counts,
    product_family_dependency_counts,
)
from db import FoamGrade, Plant, ProductFamily, get_session, init_db
from helpers import clickable_table, csv_excel_uploader, delete_with_confirm, page_setup

GRADE_REQUIRED_COLUMNS = ["product_family_id", "grade_name"]
GRADE_OPTIONAL_COLUMNS = ["target_density", "target_hardness", "quality_specification", "notes"]

page_setup("Product Family & Foam Grade")
init_db()
require_login()
logout_button()

st.title("Product Family & Foam Grade Profile")
session = get_session()

plants = session.query(Plant).all()
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
    families = session.query(ProductFamily).all()
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
    families = session.query(ProductFamily).all()
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
                    target_hardness = st.number_input("Target hardness (N)", min_value=0.0, step=1.0)
                    quality_specification = st.text_area("Quality specification")
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
                                    quality_specification=quality_specification,
                                    notes=notes,
                                )
                            )
                            session.commit()
                            st.success(f"Foam grade '{grade_name}' added.")
                            st.rerun()

        with tab_grade_import:
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
                    st.dataframe(pd.DataFrame(bad_rows), use_container_width=True)

                if good_rows and st.button("Confirm import", key="confirm_grade_import"):
                    for row in good_rows:
                        session.add(
                            FoamGrade(
                                product_family_id=int(row["product_family_id"]),
                                grade_name=str(row["grade_name"]).strip(),
                                target_density=row.get("target_density") if not pd.isna(row.get("target_density")) else None,
                                target_hardness=row.get("target_hardness") if not pd.isna(row.get("target_hardness")) else None,
                                quality_specification=str(row.get("quality_specification", "") or ""),
                                notes=str(row.get("notes", "") or ""),
                            )
                        )
                    session.commit()
                    st.success(f"Imported {len(good_rows)} foam grade(s) from {filename}.")
                    st.rerun()

        st.divider()
        grades = session.query(FoamGrade).all()
        if not grades:
            st.info("No foam grades recorded yet.")
        else:
            grade_rows = [
                {
                    "Grade": grade.grade_name,
                    "Family": grade.product_family.name,
                    "Target density (kg/m3)": grade.target_density,
                    "Target hardness (N)": grade.target_hardness,
                    "Quality spec": grade.quality_specification or "",
                }
                for grade in grades
            ]
            st.caption("Click a row to edit (and optionally delete) that foam grade.")
            idx = clickable_table(grade_rows, key="grades_table")
            if idx is not None:
                st.session_state["grade_selected_id"] = grades[idx].id

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
                        "Target hardness (N)", min_value=0.0, step=1.0,
                        value=float(selected_grade.target_hardness or 0.0), key=f"edit_grade_hardness_{selected_grade.id}",
                    )
                    e_spec = st.text_area(
                        "Quality specification", value=selected_grade.quality_specification or "",
                        key=f"edit_grade_spec_{selected_grade.id}",
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
                            selected_grade.quality_specification = e_spec
                            selected_grade.notes = e_notes
                            session.commit()
                            st.success("Foam grade updated.")
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

