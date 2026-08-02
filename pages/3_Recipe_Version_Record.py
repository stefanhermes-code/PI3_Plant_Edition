"""Screen 4: Recipes (formulation memory)

Each foam grade has exactly one ACTIVE recipe at a time - a new version
replaces the previous one in production, they don't coexist. This page
leads with that: "Create Recipe" starts a brand new formulation for a
grade, "Edit Recipe" revises the current one (saving records it as a new
version automatically and retires the one it replaces). Full version
history - every retired version, its ingredients, who approved what and
when - stays fully intact below, it's just not the first thing you have
to manage by hand.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from access_control import can_use_page
from auth import current_user, logout_button, require_login
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
from helpers import (
    activate_recipe_version,
    clickable_table,
    csv_excel_uploader,
    dedupe_import_rows,
    delete_with_confirm,
    next_version_label,
    page_setup,
    render_data_table,
    render_function_action_intro,
    set_pending_banner,
    show_pending_banner,
    view_only_notice,
)
from tenant_scope import apply_scope, company_picker, grade_ids_for_company

RECIPE_VERSION_REQUIRED_COLUMNS = ["foam_grade_id", "version_label"]
RECIPE_VERSION_OPTIONAL_COLUMNS = ["effective_date", "change_note", "approval_status", "created_by"]

COMPONENT_REQUIRED_COLUMNS = ["recipe_version_id", "raw_material_name"]
COMPONENT_OPTIONAL_COLUMNS = ["supplier", "php", "role_in_formulation", "notes"]

page_setup("Recipes")
init_db()
require_login()
logout_button()

st.title("Recipes")
render_function_action_intro(
    function_text=(
        "Maintains the formulation history for each foam grade: the raw-material list with php "
        "dosage, supplier, and role for the currently active recipe, plus every retired version "
        "before it with who approved it and when. A foam grade has exactly one active recipe in "
        "production at a time - a new version replaces it rather than running alongside it - so "
        "this is the single source of truth Recipe Optimization, cost, and correlation pages all "
        "read from."
    ),
    action_text=(
        "Use 'Create Recipe' to start a brand-new formulation for a foam grade that doesn't have "
        "one yet, or 'Edit Recipe' to revise the currently active one - saving automatically "
        "records it as a new version and retires the one it replaces, so you don't have to manage "
        "version numbers or active flags by hand. Add raw materials to a recipe by name (typing a "
        "new one creates it in Raw Materials automatically) with its php and role in the "
        "formulation, or import a full component list via CSV/Excel for bulk loading. Older "
        "versions, their ingredient lists, and approval status stay available further down for "
        "audit."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("recipes", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="recipes_company_filter"
)
active_company_id = company.id if company else None
grade_ids = grade_ids_for_company(session, active_company_id)

grades = apply_scope(session.query(FoamGrade), FoamGrade.id, grade_ids).all()
if not grades:
    st.warning("Add a foam grade first (Product Family & Foam Grade page).")
    st.stop()


def _match_or_create_raw_material(name, supplier=None):
    """Look up a RawMaterial by name (case-insensitive); create one if it
    doesn't exist yet, so anything typed as a "new" material during recipe
    entry becomes available in the master list (and future dropdowns)
    immediately, not just a one-off string on this one component.

    Scoped to the company currently in view (the platform owner's company
    filter, or the logged-in user's own company) - without this, a case-
    insensitive name match could silently link a recipe component to a
    different company's raw material row (and its cost_per_kg), which
    would leak proprietary data across the tenant boundary."""
    name = (name or "").strip()
    if not name:
        return None
    match_query = session.query(RawMaterial).filter(RawMaterial.name.ilike(name))
    if active_company_id is not None:
        match_query = match_query.filter(RawMaterial.company_id == active_company_id)
    match = match_query.first()
    if match:
        return match
    new_rm = RawMaterial(
        company_id=active_company_id, name=name, category="Other", default_supplier=supplier or "", active=True
    )
    session.add(new_rm)
    session.flush()
    return new_rm


def _active_version(grade):
    return next((v for v in grade.recipe_versions if v.is_active), None)


# ---------------------------------------------------------------------------
# Recipe versions (header record)
# ---------------------------------------------------------------------------
tab_create, tab_edit, tab_import = st.tabs(["Create Recipe", "Edit Recipe", "CSV / Excel import"])

with tab_create:
    if not page_usable:
        st.caption("View-only access - creating a recipe is restricted for your role.")
    else:
        st.caption(
            "Start a brand new formulation for a foam grade. If this grade already has an active "
            "recipe, it will be retired the moment this one is saved."
        )
        with st.form("create_recipe"):
            grade = st.selectbox(
                "Foam grade *", grades, format_func=lambda g: g.grade_name, key="create_recipe_grade"
            )
            version_label = st.text_input("Version label * (e.g. 28-MH-05)")
            effective_date = st.date_input("Effective date", value=dt.date.today())
            change_note = st.text_area("Change note (why this recipe exists) *")
            approval_status = st.selectbox("Approval status", APPROVAL_STATUSES)
            created_by = st.text_input("Created by")
            submitted = st.form_submit_button("Save recipe")
            if submitted:
                if not version_label or not change_note:
                    st.error("Version label and change note are required.")
                else:
                    new_version = RecipeVersion(
                        foam_grade_id=grade.id,
                        version_label=version_label,
                        effective_date=effective_date,
                        change_note=change_note,
                        approval_status=approval_status,
                        created_by=created_by,
                        # Explicitly False at creation, not the column's own
                        # True default: the DB now enforces at most one
                        # active version per foam grade (see db.py's
                        # RecipeVersion.is_active comment), so this row must
                        # not be flushed as active while the grade's current
                        # version is still active too - activate_recipe_
                        # version() below deactivates that one first, then
                        # flips this one on.
                        is_active=False,
                    )
                    session.add(new_version)
                    session.flush()
                    activate_recipe_version(session, grade.id, new_version)
                    session.commit()
                    st.success(
                        f"Recipe '{version_label}' created and set as {grade.grade_name}'s active recipe. "
                        "Add its ingredients below in the recipe version list."
                    )
                    st.rerun()

with tab_edit:
    if not page_usable:
        st.caption("View-only access - editing a recipe is restricted for your role.")
    else:
        grades_with_active = [g for g in grades if _active_version(g)]
        if not grades_with_active:
            st.info("No foam grade has an active recipe yet - use 'Create Recipe' to start one.")
        else:
            grade_rows = [
                {
                    "Foam grade": g.grade_name,
                    "Active version": _active_version(g).version_label,
                    "Status": _active_version(g).approval_status,
                    "Effective date": _active_version(g).effective_date,
                }
                for g in grades_with_active
            ]
            edit_idx = clickable_table(grade_rows, key="edit_recipe_grade_table")
            if edit_idx is not None and edit_idx < len(grades_with_active):
                st.session_state["edit_recipe_grade_id"] = grades_with_active[edit_idx].id
            elif st.session_state.get("edit_recipe_grade_id") not in {g.id for g in grades_with_active}:
                st.session_state.pop("edit_recipe_grade_id", None)

            selected_grade_id = st.session_state.get("edit_recipe_grade_id")
            edit_grade = next((g for g in grades_with_active if g.id == selected_grade_id), None)

            if edit_grade is None:
                st.caption("Select a foam grade above to edit its recipe.")
            else:
                active_version = _active_version(edit_grade)

                components_df = (
                    pd.DataFrame(
                        [
                            {
                                "Raw material": c.raw_material_name,
                                "Supplier": c.supplier or "",
                                "php": c.php,
                                "Role": c.role_in_formulation or "",
                                "Notes": c.notes or "",
                            }
                            for c in active_version.components
                        ]
                    )
                    if active_version.components
                    else pd.DataFrame(columns=["Raw material", "Supplier", "php", "Role", "Notes"])
                )

                st.markdown("**Ingredients** — edit values directly, or use the row controls to add or remove ingredients.")
                edited_df = st.data_editor(
                    components_df,
                    num_rows="dynamic",
                    use_container_width=True,
                    key=f"edit_recipe_components_{edit_grade.id}_{active_version.id}",
                    column_config={
                        "php": st.column_config.NumberColumn("php", min_value=0.0, step=0.1),
                    },
                )

                suggested_label = next_version_label(active_version.version_label, len(edit_grade.recipe_versions))
                with st.form(f"edit_recipe_{edit_grade.id}"):
                    new_label = st.text_input("New version label *", value=suggested_label)
                    new_effective = st.date_input("Effective date", value=dt.date.today())
                    new_change_note = st.text_area("Change note * (what changed and why)")
                    new_status = st.selectbox("Approval status", APPROVAL_STATUSES, index=0)
                    new_created_by = st.text_input("Created by")
                    save_edit = st.form_submit_button("Save as new version")
                    if save_edit:
                        clean_rows = [
                            row for _, row in edited_df.iterrows() if str(row.get("Raw material") or "").strip()
                        ]
                        if not new_label.strip() or not new_change_note.strip():
                            st.error("Version label and change note are required.")
                        elif not clean_rows:
                            st.error("At least one ingredient is required.")
                        else:
                            new_version = RecipeVersion(
                                foam_grade_id=edit_grade.id,
                                version_label=new_label.strip(),
                                effective_date=new_effective,
                                change_note=new_change_note,
                                approval_status=new_status,
                                created_by=new_created_by,
                                # See the identical note in the Create tab above:
                                # must not flush as active while this grade's
                                # current version still is - the DB now enforces
                                # at most one active version per grade.
                                is_active=False,
                            )
                            session.add(new_version)
                            session.flush()
                            for row in clean_rows:
                                name = str(row["Raw material"]).strip()
                                supplier = str(row.get("Supplier") or "")
                                rm = _match_or_create_raw_material(name, supplier)
                                session.add(
                                    RecipeComponent(
                                        recipe_version_id=new_version.id,
                                        raw_material_id=rm.id if rm else None,
                                        raw_material_name=name,
                                        supplier=supplier,
                                        php=row.get("php") if pd.notna(row.get("php")) else None,
                                        role_in_formulation=str(row.get("Role") or ""),
                                        notes=str(row.get("Notes") or ""),
                                    )
                                )
                            activate_recipe_version(session, edit_grade.id, new_version)
                            session.commit()
                            st.success(
                                f"'{new_label}' saved and is now the active recipe for {edit_grade.grade_name}."
                            )
                            st.rerun()

with tab_import:
    if not page_usable:
        st.caption("View-only access - importing recipes is restricted for your role.")
    else:
        st.caption(
            "Bulk-create recipe version HEADER records only (e.g. migrating a formulation library) - "
            "not the ingredients/components inside each version. For that, see 'Bulk import recipe "
            "components' further down this page - it's a separate upload with its own Confirm import "
            "button. A grade with no active recipe yet gets its first imported row for that grade "
            "marked active automatically; anything after that is imported as historical/inactive - use "
            "'Edit Recipe' or the recipe version list at the bottom of this page to change which one is "
            "active."
        )
        show_pending_banner("recipe_version_import_msg")
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
                render_data_table(pd.DataFrame(bad_rows), max_height="300px")

            if good_rows and st.button("Confirm import (recipe versions)", key="confirm_recipe_version_import"):
                existing_keys = {
                    (r.foam_grade_id, r.version_label.strip().lower())
                    for r in apply_scope(session.query(RecipeVersion), RecipeVersion.foam_grade_id, grade_ids).all()
                }
                new_rows, dup_rows = dedupe_import_rows(
                    good_rows,
                    existing_keys,
                    key_func=lambda row: (int(row["foam_grade_id"]), str(row["version_label"]).strip().lower()),
                )
                grades_with_active_ids = {
                    gid
                    for (gid,) in apply_scope(
                        session.query(RecipeVersion.foam_grade_id), RecipeVersion.foam_grade_id, grade_ids
                    )
                    .filter(RecipeVersion.is_active.is_(True))
                    .all()
                }
                activated_this_batch = set()
                for row in new_rows:
                    status = str(row.get("approval_status", "") or "").strip()
                    eff_date = pd.to_datetime(row.get("effective_date"), errors="coerce")
                    gid = int(row["foam_grade_id"])
                    make_active = gid not in grades_with_active_ids and gid not in activated_this_batch
                    if make_active:
                        activated_this_batch.add(gid)
                    session.add(
                        RecipeVersion(
                            foam_grade_id=gid,
                            version_label=str(row["version_label"]).strip(),
                            effective_date=eff_date.date() if not pd.isna(eff_date) else None,
                            change_note=str(row.get("change_note", "") or ""),
                            approval_status=status if status in APPROVAL_STATUSES else "Draft",
                            created_by=str(row.get("created_by", "") or ""),
                            is_active=make_active,
                        )
                    )
                session.commit()
                msg = f"Imported {len(new_rows)} recipe version(s) from {filename}."
                if dup_rows:
                    msg += f" Skipped {len(dup_rows)} row(s) already recorded for their foam grade + version label (likely a repeat click)."
                set_pending_banner("recipe_version_import_msg", msg)
                st.rerun()

# Queried once here (rather than inside the "Recipe versions" section below)
# because "Bulk import recipe components" also needs it for valid_version_ids
# - and that section now renders first, with "Recipe versions" moved to the
# bottom of the page.
versions = (
    apply_scope(session.query(RecipeVersion), RecipeVersion.foam_grade_id, grade_ids)
    .order_by(RecipeVersion.created_at.desc())
    .all()
)
version_ids = [v.id for v in versions]

# ---------------------------------------------------------------------------
# Bulk import recipe components (ingredients)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🧪 Bulk import recipe components (ingredients)")
if not page_usable:
    st.caption("View-only access - importing recipe components is restricted for your role.")
else:
    st.caption(
        "A separate import from 'CSV / Excel import' above - that one creates recipe version "
        "headers, this one fills in the raw materials/php/role inside a version that already "
        "exists. Each row needs the recipe_version_id it belongs to (see the recipe version list "
        "at the bottom of this page for IDs) and a raw material name — unmatched raw material "
        "names are automatically added to the Raw Materials master list."
    )
    show_pending_banner("recipe_component_import_msg")
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
            render_data_table(pd.DataFrame(bad_rows), max_height="300px")

        if good_rows and st.button("Confirm import (recipe components)", key="confirm_component_import"):
            existing_keys = {
                (c.recipe_version_id, c.raw_material_name.strip().lower())
                for c in apply_scope(
                    session.query(RecipeComponent), RecipeComponent.recipe_version_id, version_ids
                ).all()
            }
            new_rows, dup_rows = dedupe_import_rows(
                good_rows,
                existing_keys,
                key_func=lambda row: (int(row["recipe_version_id"]), str(row["raw_material_name"]).strip().lower()),
            )
            for row in new_rows:
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
            msg = f"Imported {len(new_rows)} recipe component(s) from {comp_filename}."
            if dup_rows:
                msg += f" Skipped {len(dup_rows)} row(s) already recorded for their recipe version (likely a repeat click)."
            set_pending_banner("recipe_component_import_msg", msg)
            st.rerun()

# ---------------------------------------------------------------------------
# Recipe versions (full history + detail/edit/delete) - kept at the bottom of
# the page on purpose: Create/Edit Recipe above cover the day-to-day flow,
# this is the audit trail underneath it.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Recipe versions")
st.caption(
    "Full formulation history across every foam grade. Click a row to view or manage that "
    "version's details, ingredients, or delete it."
)

if not versions:
    st.info("No recipe versions recorded yet.")
else:
    version_rows = [
        {
            "Version": v.version_label,
            "Foam grade": v.foam_grade.grade_name if v.foam_grade else "—",
            "Active": "Yes" if v.is_active else "No",
            "Status": v.approval_status,
            "Effective date": v.effective_date,
            "Created by": v.created_by,
        }
        for v in versions
    ]
    idx = clickable_table(version_rows, key="recipe_versions_table")
    if idx is not None and idx < len(versions):
        st.session_state["rv_selected_id"] = versions[idx].id
    elif st.session_state.get("rv_selected_id") not in {v.id for v in versions}:
        st.session_state.pop("rv_selected_id", None)

    selected_id = st.session_state.get("rv_selected_id")
    v = next((x for x in versions if x.id == selected_id), None)

    if v is None:
        st.caption("Select a row above to view or manage that recipe version.")
    else:
        st.markdown(
            f"### {v.version_label} — {v.foam_grade.grade_name if v.foam_grade else '—'} "
            + ("🟢 Active" if v.is_active else "")
        )
        st.caption(f"Effective {v.effective_date or '—'} | Created by {v.created_by or '—'} | Status `{v.approval_status}`")
        st.write(v.change_note)

        if not v.is_active and page_usable:
            if st.button("Set as active recipe", key=f"activate_{v.id}"):
                activate_recipe_version(session, v.foam_grade_id, v)
                session.commit()
                st.success(f"'{v.version_label}' is now the active recipe for {v.foam_grade.grade_name}.")
                st.rerun()

        with st.expander("Edit details / delete this recipe version"):
            if not page_usable:
                st.caption("View-only access - editing and deleting is restricted for your role.")
            else:
                st.caption(
                    "This edits this version's own header details in place (for example, fixing a typo "
                    "or updating its approval status) - it does not create a new version. To revise the "
                    "actual formulation, use the 'Edit Recipe' tab above instead."
                )
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
                comp_idx = clickable_table(comp_rows, key=f"components_table_{v.id}")
                if comp_idx is not None and comp_idx < len(v.components):
                    st.session_state["comp_selected_id"] = v.components[comp_idx].id
                elif st.session_state.get("comp_selected_id") in {c.id for c in v.components}:
                    # a component belonging to THIS version was selected before, but the
                    # table no longer reports a selection - clear the stale reference
                    # rather than leaving a phantom edit form. Scoped to this version's
                    # own component ids so it doesn't clobber a different version's
                    # live selection elsewhere in this same loop.
                    st.session_state.pop("comp_selected_id", None)

                selected_comp_id = st.session_state.get("comp_selected_id")
                selected_comp = next((c for c in v.components if c.id == selected_comp_id), None)

                if selected_comp:
                    st.markdown(f"**Edit component: {selected_comp.raw_material_name}**")
                    if not page_usable:
                        st.caption("View-only access - editing and deleting is restricted for your role.")
                    else:
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

            if not page_usable:
                st.caption("View-only access - adding a component is restricted for your role.")
            else:
                rm_query = session.query(RawMaterial)
                if active_company_id is not None:
                    rm_query = rm_query.filter(RawMaterial.company_id == active_company_id)
                active_raw_materials = (
                    rm_query
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
