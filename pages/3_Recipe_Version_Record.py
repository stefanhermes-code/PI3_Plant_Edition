"""Screen 4: Recipe Version Record (formulation memory)"""

import datetime as dt

import streamlit as st

from auth import logout_button, require_login
from db import APPROVAL_STATUSES, FoamGrade, RecipeComponent, RecipeVersion, get_session, init_db
from helpers import page_setup, show_advisory_footer

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

with st.expander("Add recipe version"):
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

        with st.expander(f"Recipe components ({len(v.components)})"):
            if v.components:
                st.dataframe(
                    [
                        {
                            "Raw material": c.raw_material_name,
                            "Supplier": c.supplier,
                            "php": c.php,
                            "Role": c.role_in_formulation,
                            "Notes": c.notes,
                        }
                        for c in v.components
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
            with st.form(f"add_component_{v.id}"):
                c1, c2, c3 = st.columns(3)
                raw_material = c1.text_input("Raw material *", key=f"rm_{v.id}")
                supplier = c2.text_input("Supplier", key=f"sup_{v.id}")
                php = c3.number_input("php", min_value=0.0, step=0.1, key=f"php_{v.id}")
                role = st.text_input("Role in formulation (e.g. polyol, TDI, catalyst, surfactant)", key=f"role_{v.id}")
                notes = st.text_input("Notes", key=f"notes_{v.id}")
                add_component = st.form_submit_button("Add component")
                if add_component:
                    if not raw_material:
                        st.error("Raw material name is required.")
                    else:
                        session.add(
                            RecipeComponent(
                                recipe_version_id=v.id,
                                raw_material_name=raw_material,
                                supplier=supplier,
                                php=php or None,
                                role_in_formulation=role,
                                notes=notes,
                            )
                        )
                        session.commit()
                        st.success("Component added.")
                        st.rerun()

show_advisory_footer()
