"""Screen 2: Plant & Foam Equipment Overview"""

import streamlit as st

from access_control import can_use_page
from auth import current_user, logout_button, require_login
from cascades import delete_plant_cascade, plant_dependency_counts
from db import (
    MACHINE_OEMS,
    MAXFOAM_MODELS,
    OTHER_LAADER_BERG_MODEL,
    Company,
    Machine,
    Plant,
    ProductionRun,
    get_session,
    init_db,
)
from helpers import clickable_table, delete_with_confirm, page_setup, render_function_action_intro, view_only_notice
from tenant_scope import clear_scope_cache, company_picker

page_setup("Plant & Foam Equipment Overview")
init_db()
require_login()
logout_button()

st.title("Plant & Foam Equipment Overview")
render_function_action_intro(
    function_text=(
        "This is where you set up and maintain the plants and foaming lines that every recipe, "
        "production run, and quality result in the system is ultimately traced back to. It "
        "records each plant's name, code, and location, and each machine's OEM, model, and "
        "active status, and shows at a glance how many product families and machines sit under "
        "each plant."
    ),
    action_text=(
        "Add a plant before adding anything else under it, since product families, recipes, and "
        "production runs all key off it eventually. Then add each foaming line/machine at that "
        "plant with its OEM and model so it can be selected when a production run is logged. "
        "Click a row in either table to edit or delete it - deleting a plant permanently removes "
        "everything recorded under it (the count is shown before you confirm), while deleting a "
        "machine only unlinks it from any production runs that reference it."
    ),
)
session = get_session()
user = current_user()
is_platform_owner = user["is_platform_owner"]
own_company_id = user["company_id"]
page_usable = can_use_page("plant_overview", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()

company_filter, all_companies = company_picker(
    st, session, is_platform_owner, own_company_id, key="plant_company_filter"
)
if not is_platform_owner and not company_filter:
    st.warning("Your account isn't linked to a company yet - contact the platform administrator.")
    st.stop()

subscription = company_filter.subscription_type if company_filter else None
max_plants = subscription.max_plants if subscription else None
plant_count_for_company = (
    session.query(Plant).filter(Plant.company_id == company_filter.id).count() if company_filter else None
)
limit_reached = (
    company_filter is not None and max_plants is not None and plant_count_for_company >= max_plants
)

with st.expander("Add plant", expanded=False):
    if not page_usable:
        st.caption("View-only access - adding a plant is restricted for your role.")
    else:
        if limit_reached:
            st.warning(
                f"{company_filter.name} is at its subscription's plant limit ({max_plants} plants). "
                "Upgrade the subscription or remove an unused plant before adding another."
            )
        with st.form("add_plant"):
            if is_platform_owner:
                plant_company = st.selectbox(
                    "Company *", all_companies, format_func=lambda c: c.name,
                    index=(all_companies.index(company_filter) if company_filter in all_companies else 0),
                )
            else:
                plant_company = company_filter
                st.caption(f"Company: {plant_company.name if plant_company else '—'}")
            name = st.text_input("Plant name *")
            plant_code = st.text_input("Plant code")
            location = st.text_input("Location")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save plant", disabled=limit_reached and not is_platform_owner)
            if submitted:
                if not name:
                    st.error("Plant name is required.")
                elif not plant_company:
                    st.error("A company is required.")
                else:
                    session.add(
                        Plant(
                            company_id=plant_company.id,
                            name=name,
                            plant_code=plant_code,
                            location=location,
                            notes=notes,
                        )
                    )
                    session.commit()
                    clear_scope_cache()
                    st.success(f"Plant '{name}' added.")
                    st.rerun()

st.divider()
st.subheader("Plants")

plants_query = session.query(Plant)
if company_filter is not None:
    plants_query = plants_query.filter(Plant.company_id == company_filter.id)
plants = plants_query.all()
if not plants:
    st.info("No plants recorded yet.")
else:
    plant_rows = [
        {
            **({"Company": plant.company.name if plant.company else "—"} if is_platform_owner else {}),
            "Name": plant.name,
            "Code": plant.plant_code or "—",
            "Location": plant.location or "—",
            "Product families": len(plant.product_families),
            "Machines": session.query(Machine).filter(Machine.plant_id == plant.id).count(),
            "Notes": plant.notes or "",
        }
        for plant in plants
    ]
    st.caption("Click a row to edit (and optionally delete) that plant.")
    idx = clickable_table(plant_rows, key="plants_table")
    if idx is not None and idx < len(plants):
        st.session_state["plant_selected_id"] = plants[idx].id
    else:
        st.session_state.pop("plant_selected_id", None)

    selected_plant_id = st.session_state.get("plant_selected_id")
    selected_plant = next((p for p in plants if p.id == selected_plant_id), None)

    if selected_plant:
        st.markdown(f"**Edit plant: {selected_plant.name}**")
        if not page_usable:
            st.caption("View-only access - editing and deleting is restricted for your role.")
        else:
            with st.form(f"edit_plant_{selected_plant.id}"):
                if is_platform_owner:
                    e_company = st.selectbox(
                        "Company *", all_companies,
                        index=next((i for i, c in enumerate(all_companies) if c.id == selected_plant.company_id), 0),
                        format_func=lambda c: c.name, key=f"edit_plant_company_{selected_plant.id}",
                    )
                else:
                    e_company = company_filter
                e_name = st.text_input("Plant name *", value=selected_plant.name, key=f"edit_plant_name_{selected_plant.id}")
                e_code = st.text_input("Plant code", value=selected_plant.plant_code or "", key=f"edit_plant_code_{selected_plant.id}")
                e_location = st.text_input("Location", value=selected_plant.location or "", key=f"edit_plant_loc_{selected_plant.id}")
                e_notes = st.text_area("Notes", value=selected_plant.notes or "", key=f"edit_plant_notes_{selected_plant.id}")
                if st.form_submit_button("Save changes"):
                    if not e_name.strip():
                        st.error("Plant name is required.")
                    else:
                        selected_plant.company_id = e_company.id if e_company else selected_plant.company_id
                        selected_plant.name = e_name.strip()
                        selected_plant.plant_code = e_code
                        selected_plant.location = e_location
                        selected_plant.notes = e_notes
                        session.commit()
                        st.success("Plant updated.")
                        st.rerun()

            counts = plant_dependency_counts(session, selected_plant.id)
            total_related = sum(counts.values())
            if total_related:
                detail = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
                warning = f"Deleting this plant will also permanently delete {total_related} related record(s): {detail}."
            else:
                warning = "This plant has no related records — deleting it is safe."

            def _do_delete_plant(_session=session, _id=selected_plant.id):
                delete_plant_cascade(_session, _id)
                _session.commit()
                clear_scope_cache()
                st.session_state.pop("plant_selected_id", None)

            delete_with_confirm(
                f"'{selected_plant.name}'", _do_delete_plant, key_prefix=f"plant_{selected_plant.id}",
                extra_warning=warning,
            )

        if st.button("Clear selection", key="clear_plant_selection"):
            st.session_state.pop("plant_selected_id", None)
            st.rerun()

def _machine_model_picker(oem, current_model, key_prefix):
    """Model input for the Add/Edit machine forms below. Called *before*
    opening the surrounding st.form (same reason as the Expert Notes
    "Link to" picker: widgets inside a form don't trigger a rerun until
    submit, so if OEM lived inside the form, switching it wouldn't swap
    this field until after Save) - when OEM is Laader Berg this offers a
    controlled dropdown of the known Maxfoam generations (so
    expected_fallplate_section_count on the Production Run page reliably
    matches instead of depending on free text), with a free-text fallback
    for any generation not in that list; every other OEM stays plain free
    text as before."""
    if oem == "Laader Berg":
        default_index = MAXFOAM_MODELS.index(current_model) if current_model in MAXFOAM_MODELS else 0
        choice = st.selectbox("Model", MAXFOAM_MODELS, index=default_index, key=f"{key_prefix}_model_choice")
        if choice == OTHER_LAADER_BERG_MODEL:
            other_default = current_model if current_model and current_model not in MAXFOAM_MODELS else ""
            return st.text_input("Model (specify)", value=other_default, key=f"{key_prefix}_model_other")
        return choice
    return st.text_input("Model", value=current_model or "", key=f"{key_prefix}_model_text")


st.divider()
st.subheader("Machines / foaming lines")
st.caption(
    "Process parameters (conveyor speed, sidewall width, laydown mode, etc.) connect to the "
    "specific equipment that produced them. A production run picks one of these."
)

if not plants:
    st.info("Add a plant first before adding machines.")
else:
    with st.expander("Add machine / foaming line", expanded=False):
        if not page_usable:
            st.caption("View-only access - adding a machine is restricted for your role.")
        else:
            oem = st.selectbox("OEM / manufacturer", MACHINE_OEMS, key="add_machine_oem")
            model = _machine_model_picker(oem, "", "add_machine")
            with st.form("add_machine"):
                plant_for_machine = st.selectbox("Plant *", plants, format_func=lambda p: p.name)
                name = st.text_input("Machine / line name * (e.g. Line 1, Maxfoam A)")
                machine_code = st.text_input("Machine code")
                st.caption(f"OEM: **{oem}** · Model: **{model or '—'}** (change above, outside this form)")
                active = st.checkbox("Active", value=True)
                notes = st.text_area("Notes")
                submitted = st.form_submit_button("Save machine")
                if submitted:
                    if not name:
                        st.error("Machine / line name is required.")
                    else:
                        session.add(
                            Machine(
                                plant_id=plant_for_machine.id,
                                name=name,
                                machine_code=machine_code,
                                oem=oem,
                                model=model,
                                active=active,
                                notes=notes,
                            )
                        )
                        session.commit()
                        st.success(f"Machine '{name}' added.")
                        st.rerun()

    # Scoped to the same plant set as the "Plants" table above (`plants`,
    # already filtered by company_filter) - previously unfiltered here,
    # which meant every company's machines were visible to every other
    # company once a second company existed. Fixed 2026-08-04 (Duroflex
    # pilot readiness audit).
    machines = (
        session.query(Machine)
        .filter(Machine.plant_id.in_([p.id for p in plants]))
        .order_by(Machine.plant_id, Machine.name)
        .all()
    )
    if not machines:
        st.info("No machines recorded yet.")
    else:
        machine_rows = [
            {
                "Plant": m.plant.name,
                "Machine": m.name,
                "Code": m.machine_code or "—",
                "OEM": m.oem or "—",
                "Model": m.model or "—",
                "Active": m.active,
                "Notes": m.notes or "",
            }
            for m in machines
        ]
        st.caption("Click a row to edit (and optionally delete) that machine.")
        idx = clickable_table(machine_rows, key="machines_table")
        if idx is not None and idx < len(machines):
            st.session_state["machine_selected_id"] = machines[idx].id
        else:
            st.session_state.pop("machine_selected_id", None)

        selected_machine_id = st.session_state.get("machine_selected_id")
        selected_machine = next((m for m in machines if m.id == selected_machine_id), None)

        if selected_machine:
            st.markdown(f"**Edit machine: {selected_machine.name}**")
            if not page_usable:
                st.caption("View-only access - editing and deleting is restricted for your role.")
            else:
                # e_oem/e_model live outside the form, same reason as the
                # Add-machine picker above - switching OEM needs to swap
                # the Model widget immediately, which a form can't do
                # until submit.
                e_oem = st.selectbox(
                    "OEM / manufacturer", MACHINE_OEMS,
                    index=MACHINE_OEMS.index(selected_machine.oem) if selected_machine.oem in MACHINE_OEMS else 0,
                    key=f"edit_machine_oem_{selected_machine.id}",
                )
                e_model = _machine_model_picker(e_oem, selected_machine.model, f"edit_machine_{selected_machine.id}")
                with st.form(f"edit_machine_{selected_machine.id}"):
                    e_plant = st.selectbox(
                        "Plant *", plants,
                        index=next((i for i, p in enumerate(plants) if p.id == selected_machine.plant_id), 0),
                        format_func=lambda p: p.name, key=f"edit_machine_plant_{selected_machine.id}",
                    )
                    e_name = st.text_input("Machine / line name *", value=selected_machine.name, key=f"edit_machine_name_{selected_machine.id}")
                    e_code = st.text_input(
                        "Machine code", value=selected_machine.machine_code or "", key=f"edit_machine_code_{selected_machine.id}"
                    )
                    st.caption(f"OEM: **{e_oem}** · Model: **{e_model or '—'}** (change above, outside this form)")
                    e_active = st.checkbox("Active", value=selected_machine.active, key=f"edit_machine_active_{selected_machine.id}")
                    e_notes = st.text_area("Notes", value=selected_machine.notes or "", key=f"edit_machine_notes_{selected_machine.id}")
                    if st.form_submit_button("Save changes"):
                        if not e_name.strip():
                            st.error("Machine / line name is required.")
                        else:
                            selected_machine.plant_id = e_plant.id
                            selected_machine.name = e_name.strip()
                            selected_machine.machine_code = e_code
                            selected_machine.oem = e_oem
                            selected_machine.model = e_model
                            selected_machine.active = e_active
                            selected_machine.notes = e_notes
                            session.commit()
                            st.success("Machine updated.")
                            st.rerun()

                linked_runs = session.query(ProductionRun).filter(ProductionRun.machine_id == selected_machine.id).count()
                if linked_runs:
                    warning = (
                        f"{linked_runs} production run(s) reference this machine. Deleting it will unlink them "
                        "(the runs stay, the machine reference is cleared), not delete those runs."
                    )
                else:
                    warning = "No production runs reference this machine — deleting it is safe."

                def _do_delete_machine(_session=session, _id=selected_machine.id):
                    _session.query(ProductionRun).filter(ProductionRun.machine_id == _id).update(
                        {"machine_id": None}, synchronize_session="fetch"
                    )
                    _session.query(Machine).filter(Machine.id == _id).delete(synchronize_session=False)
                    _session.commit()
                    st.session_state.pop("machine_selected_id", None)

                delete_with_confirm(
                    f"'{selected_machine.name}'", _do_delete_machine, key_prefix=f"machine_{selected_machine.id}",
                    extra_warning=warning,
                )

            if st.button("Clear selection", key="clear_machine_selection"):
                st.session_state.pop("machine_selected_id", None)
                st.rerun()

