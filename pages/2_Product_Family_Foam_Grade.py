"""Screen 3: Product Family and Foam Grade Profile"""

import streamlit as st

from auth import logout_button, require_login
from db import FoamGrade, Plant, ProductFamily, get_session, init_db
from helpers import page_setup, show_advisory_footer

page_setup("Product Family & Foam Grade")
init_db()
require_login()
logout_button()

st.title("Product Family & Foam Grade Profile")
session = get_session()

plants = session.query(Plant).all()
if not plants:
    st.warning("Add a plant first (Plant / Installation Overview) before creating product families.")
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
    for fam in families:
        with st.container(border=True):
            st.markdown(f"**{fam.name}**  ·  Plant: {fam.plant.name}")
            st.caption(f"{fam.application or '—'} | {fam.customer_segment or '—'}")
            st.caption(fam.description or "")
            st.write(f"Foam grades: {len(fam.foam_grades)}")

with tab_grade:
    families = session.query(ProductFamily).all()
    if not families:
        st.warning("Add a product family first.")
    else:
        with st.expander("Add foam grade"):
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

        st.divider()
        grades = session.query(FoamGrade).all()
        if not grades:
            st.info("No foam grades recorded yet.")
        for grade in grades:
            with st.container(border=True):
                st.markdown(f"**{grade.grade_name}**  ·  Family: {grade.product_family.name}")
                st.caption(
                    f"Target density: {grade.target_density or '—'} kg/m3 | "
                    f"Target hardness: {grade.target_hardness or '—'} N"
                )
                st.caption(grade.quality_specification or "")

show_advisory_footer()
