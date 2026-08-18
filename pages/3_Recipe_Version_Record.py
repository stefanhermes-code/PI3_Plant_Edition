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
    log_export_click,
    next_version_label,
    page_setup,
    recipe_component_sort_index,
    render_data_table,
    render_function_action_intro,
    set_pending_banner,
    show_pending_banner,
    summarize_recipe_component_changes,
    view_only_notice,
)
from tenant_scope import apply_scope, company_picker, grade_ids_for_company
import reports

RECIPE_VERSION_REQUIRED_COLUMNS = ["foam_grade_id", "version_label"]
RECIPE_VERSION_OPTIONAL_COLUMNS = ["effective_date", "change_note", "approval_status", "created_by", "ratio_index"]

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


def _cell_text(value):
    """Free-text cell value as a clean string.

    `str(x or "")` is not safe here. A pandas NaN - which is what an empty
    cell in an uploaded spreadsheet or an untouched st.data_editor cell
    becomes - is truthy, so `str(nan or "")` evaluates to the three-character
    string "nan", and that is what gets written to the database. Every recipe
    component imported so far carries notes="nan" for exactly this reason.

    Also treats a literal "nan"/"none" that a previous import already stored
    as empty, so those rows read back blank instead of showing the artefact.
    """
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _lookup_raw_material(name):
    """Find a RawMaterial by name in the company currently in view. Returns
    None if there is no such material - it never creates one.

    Recipe editing must not be able to mint raw materials. A recipe is a bill
    of materials against the plant's actual master data; letting a typo in an
    ingredient grid create a new "raw material" produces near-duplicate rows
    (Kosmos T9 / KOSMOS T9 / Kosmus T9) that then split cost, supplier and
    usage reporting across records that are really the same thing. New
    materials are added on the Raw Materials page, deliberately, with their
    category and supplier.
    """
    name = _cell_text(name)
    if not name:
        return None
    q = session.query(RawMaterial).filter(RawMaterial.name.ilike(name))
    if active_company_id is not None:
        q = q.filter(RawMaterial.company_id == active_company_id)
    return q.first()


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
            ratio_index = st.number_input(
                "Ratio / index", min_value=0.0, step=0.01, format="%.3f",
                help="Stoichiometric ratio/index for this formulation - determines the isocyanate php. "
                "A property of the recipe, not of any single production run.",
            )
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
                        ratio_index=ratio_index or None,
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
                st.caption("Select a recipe above to edit it.")
            else:
                active_version = _active_version(edit_grade)
                st.markdown(
                    f"**{edit_grade.grade_name} — {active_version.version_label}** "
                    f"({active_version.approval_status})"
                )

                # --- recipe-level fields, directly under the recipe table -------
                ef1, ef2, ef3 = st.columns(3)
                new_effective = ef1.date_input(
                    "Effective date", value=dt.date.today(),
                    key=f"edit_recipe_effective_{edit_grade.id}",
                )
                # Default to the status the selected recipe actually has, not
                # index=0 ("Draft"). Showing Draft against an Approved recipe
                # misreports the record on screen, and silently demotes it to
                # Draft on save if the operator does not notice and change it
                # back. Falls back to the first status only if the stored value
                # is not one of APPROVAL_STATUSES.
                _status_index = (
                    APPROVAL_STATUSES.index(active_version.approval_status)
                    if active_version.approval_status in APPROVAL_STATUSES
                    else 0
                )
                new_status = ef2.selectbox(
                    "Approval status", APPROVAL_STATUSES, index=_status_index,
                    key=f"edit_recipe_status_{edit_grade.id}",
                )
                new_ratio_index = ef3.number_input(
                    "Ratio / index", min_value=0.0, step=0.01, format="%.3f",
                    value=float(active_version.ratio_index or 0.0),
                    key=f"edit_recipe_ratio_{edit_grade.id}",
                    help="Stoichiometric ratio/index for this formulation - determines the "
                    "isocyanate php. Carried over from the version being replaced; adjust if "
                    "this revision changes it.",
                )

                # --- delete, only once a recipe is actually selected ------------
                # Deliberately inside the `edit_grade is not None` branch: with
                # nothing selected there is no recipe for a delete control to act
                # on, and showing one would invite deleting the wrong thing.
                with st.expander(f"Delete recipe '{active_version.version_label}'"):
                    _counts = recipe_version_dependency_counts(session, active_version.id)
                    _total_related = sum(_counts.values())
                    if _total_related:
                        _detail = ", ".join(f"{n} {k}" for k, n in _counts.items() if n)
                        _warning = (
                            f"Deleting this recipe version will also permanently delete "
                            f"{_total_related} related record(s): {_detail}."
                        )
                    else:
                        _warning = "This recipe version has no related records — deleting it is safe."

                    def _do_delete_active_version(_session=session, _id=active_version.id):
                        delete_recipe_version_cascade(_session, _id)
                        _session.commit()
                        st.session_state.pop("edit_recipe_grade_id", None)

                    delete_with_confirm(
                        f"recipe version '{active_version.version_label}' for {edit_grade.grade_name}",
                        _do_delete_active_version,
                        key_prefix=f"edit_tab_version_{active_version.id}",
                        extra_warning=_warning,
                    )

                # --- ingredients ------------------------------------------------
                ordered_components = sorted(
                    active_version.components,
                    key=lambda c: recipe_component_sort_index(c.role_in_formulation, c.raw_material_name),
                )
                components_df = (
                    pd.DataFrame(
                        [
                            {
                                "Raw material": c.raw_material_name,
                                "Supplier": _cell_text(c.supplier),
                                "php": c.php,
                                "Role": _cell_text(c.role_in_formulation),
                                "Notes": _cell_text(c.notes),
                            }
                            for c in ordered_components
                        ]
                    )
                    if active_version.components
                    else pd.DataFrame(columns=["Raw material", "Supplier", "php", "Role", "Notes"])
                )

                # Raw material is a dropdown onto the raw-material master, so an
                # ingredient is added by picking something the plant actually holds
                # rather than retyping a name. Names already used by this recipe are
                # unioned in so a legacy component still renders.
                _rm_q = session.query(RawMaterial).filter(RawMaterial.active.is_(True))
                if active_company_id is not None:
                    _rm_q = _rm_q.filter(RawMaterial.company_id == active_company_id)
                raw_material_names = {rm.name for rm in _rm_q.all() if rm.name}
                raw_material_names.update(
                    c.raw_material_name for c in ordered_components if c.raw_material_name
                )
                raw_material_choices = sorted(raw_material_names, key=str.lower)

                # No st.form around any of this - deliberately. st.data_editor hands
                # its edits back on the NEXT rerun, and a form suppresses reruns
                # until submit, so the submit handler would still see the original
                # frame and write the original ingredients straight back. That was
                # the reason edits to this table never took. data_editor followed by
                # a plain st.button is the pattern the target-properties grid on
                # 2_Product_Family_Foam_Grade.py already uses.
                st.markdown(
                    "**Ingredients** — change a raw material or its php in place, add an "
                    "ingredient in the blank row at the bottom, or select a row and use the "
                    "grid's delete control to remove it."
                )
                edited_df = st.data_editor(
                    components_df,
                    num_rows="dynamic",
                    use_container_width=True,
                    key=f"edit_recipe_components_{edit_grade.id}_{active_version.id}",
                    column_config={
                        "Raw material": st.column_config.SelectboxColumn(
                            "Raw material", options=raw_material_choices, required=True,
                            help="Pick from the raw materials held for this company.",
                        ),
                        # step is 0.001, not 0.1: catalysts are dosed well below 0.1 php
                        # (Dabco BL11 at 0.03, 33LV at 0.09, Kosmos T9 at 0.19 on
                        # STD 25170). A 0.1 step rounded those to 0.00 / 0.00 / 0.10 on
                        # display and rejected anything finer as invalid on entry.
                        "php": st.column_config.NumberColumn(
                            "php", min_value=0.0, step=0.001, format="%.3f",
                            help="Parts per hundred polyol. Catalysts are typically 0.01-0.30.",
                        ),
                    },
                )

                suggested_label = next_version_label(active_version.version_label, len(edit_grade.recipe_versions))
                st.caption(f"Saving creates version **{suggested_label}** and retires the current one.")
                save_edit = st.button("Save as new version", key=f"save_recipe_{edit_grade.id}")
                if save_edit:
                    clean_rows = [
                        row for _, row in edited_df.iterrows()
                        if _cell_text(row.get("Raw material"))
                    ]
                    unknown_materials = sorted({
                        _cell_text(r["Raw material"]) for r in clean_rows
                        if _lookup_raw_material(r["Raw material"]) is None
                    })
                    if not clean_rows:
                        st.error("At least one ingredient is required.")
                    elif unknown_materials:
                        st.error(
                            "Not in the raw material database: "
                            + ", ".join(unknown_materials)
                            + ". Add it on the Raw Materials page first, then pick it here."
                        )
                    else:
                        new_label = suggested_label
                        new_change_note = summarize_recipe_component_changes(
                            active_version.components, clean_rows
                        )
                        new_created_by = user["display_name"] or user["username"] or ""
                        new_version = RecipeVersion(
                            foam_grade_id=edit_grade.id,
                            version_label=new_label,
                            effective_date=new_effective,
                            change_note=new_change_note,
                            approval_status=new_status,
                            created_by=new_created_by,
                            ratio_index=new_ratio_index or None,
                            # See the identical note in the Create tab above:
                            # must not flush as active while this grade's current
                            # version still is - the DB now enforces at most one
                            # active version per grade.
                            is_active=False,
                        )
                        session.add(new_version)
                        session.flush()
                        for row in clean_rows:
                            name = _cell_text(row["Raw material"])
                            supplier = _cell_text(row.get("Supplier"))
                            # Lookup only - see _lookup_raw_material. Validated above,
                            # so this cannot be None here.
                            rm = _lookup_raw_material(name)
                            session.add(
                                RecipeComponent(
                                    recipe_version_id=new_version.id,
                                    raw_material_id=rm.id if rm else None,
                                    raw_material_name=name,
                                    supplier=supplier,
                                    php=row.get("php") if pd.notna(row.get("php")) else None,
                                    role_in_formulation=_cell_text(row.get("Role")),
                                    notes=_cell_text(row.get("Notes")),
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
                            ratio_index=row.get("ratio_index") if pd.notna(row.get("ratio_index")) else None,
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
                supplier_val = _cell_text(row.get("supplier"))
                rm = _match_or_create_raw_material(name_val, supplier_val)
                session.add(
                    RecipeComponent(
                        recipe_version_id=int(row["recipe_version_id"]),
                        raw_material_id=rm.id if rm else None,
                        raw_material_name=name_val,
                        supplier=supplier_val,
                        php=row.get("php") if not pd.isna(row.get("php")) else None,
                        role_in_formulation=_cell_text(row.get("role_in_formulation")),
                        notes=_cell_text(row.get("notes")),
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
        st.caption(
            f"Ratio / index: **{v.ratio_index:.3f}**" if v.ratio_index is not None else "Ratio / index: not set"
        )
        st.write(v.change_note)

        if not v.is_active and page_usable:
            if st.button("Set as active recipe", key=f"activate_{v.id}"):
                activate_recipe_version(session, v.foam_grade_id, v)
                session.commit()
                st.success(f"'{v.version_label}' is now the active recipe for {v.foam_grade.grade_name}.")
                st.rerun()

        with st.expander("📄 Recipe / Formulation Record report"):
            st.caption(
                "Internal-use record for this recipe version - the formulation itself, quality specs "
                "vs. actual results over a date range you choose, and cost per kg. Not for external/"
                "customer use, since it includes the formulation."
            )
            frc1, frc2 = st.columns(2)
            report_date_from = frc1.date_input(
                "Quality results from", value=dt.date.today() - dt.timedelta(days=180), key=f"formrec_from_{v.id}"
            )
            report_date_to = frc2.date_input("to", value=dt.date.today(), key=f"formrec_to_{v.id}")
            report_data = reports.build_recipe_formulation_record_data(
                session, v.id, date_from=report_date_from, date_to=report_date_to
            )

            st.write("**Formulation**")
            render_data_table(pd.DataFrame(report_data["components"] or [{"—": "No data recorded"}]))
            st.write("**Quality specs vs. results**")
            render_data_table(pd.DataFrame(report_data["quality_rows"] or [{"—": "No data recorded"}]))
            if report_data["cost_per_kg"] is not None:
                st.metric("Cost per kg", report_data["cost_per_kg"])
            else:
                st.caption("No priced components yet - cost per kg cannot be calculated.")

            st.download_button(
                "Download Word", data=reports.render_recipe_formulation_record_docx(report_data),
                file_name=f"recipe_{v.id}_formulation_record.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"formrec_docx_{v.id}",
                on_click=log_export_click, args=("recipe_formulation_record_docx",),
                kwargs={"description": f"Recipe version #{v.id} ({v.version_label})"},
            )

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
                    e_ratio_index = st.number_input(
                        "Ratio / index", min_value=0.0, step=0.01, format="%.3f",
                        value=float(v.ratio_index or 0.0), key=f"edit_version_ratio_{v.id}",
                        help="Stoichiometric ratio/index for this formulation - determines the isocyanate php.",
                    )
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
                            v.ratio_index = e_ratio_index or None
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
                ordered_version_components = sorted(
                    v.components,
                    key=lambda c: recipe_component_sort_index(c.role_in_formulation, c.raw_material_name),
                )
                comp_rows = [
                    {
                        "Raw material": c.raw_material_name,
                        "Supplier": c.supplier,
                        "php": f"{c.php:.2f}" if c.php is not None else "",
                        "Role": c.role_in_formulation,
                        "Notes": c.notes,
                    }
                    for c in ordered_version_components
                ]
                st.caption("Click a row to edit (and optionally delete) that component.")
                comp_idx = clickable_table(comp_rows, key=f"components_table_{v.id}")
                if comp_idx is not None and comp_idx < len(ordered_version_components):
                    st.session_state["comp_selected_id"] = ordered_version_components[comp_idx].id
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
                                "php", min_value=0.0, step=0.001, format="%.3f",
                                value=float(selected_comp.php or 0.0), key=f"edit_comp_php_{selected_comp.id}",
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
                    php = c3.number_input("php", min_value=0.0, step=0.001, format="%.3f", key=f"php_{v.id}")
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

# ---------------------------------------------------------------------------
# Where Used Report - reverse lookup: which recipes use a given raw material.
# Kept at the very bottom since it's scoped by raw material, not by any
# recipe version selected above.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📄 Where Used Report")
st.caption(
    "Pick a raw material to see every recipe version - active and retired - that uses it, the "
    "target properties of the foam grades affected, and any Customer/Optimization Trial precedent "
    "tied to those recipes. Useful before considering a material substitution."
)
wu_rm_query = session.query(RawMaterial)
if active_company_id is not None:
    wu_rm_query = wu_rm_query.filter(RawMaterial.company_id == active_company_id)
wu_materials = wu_rm_query.order_by(RawMaterial.name).all()

if not wu_materials:
    st.info("No raw materials recorded yet.")
else:
    wu_material = st.selectbox(
        "Raw material", wu_materials,
        format_func=lambda m: f"{m.name} ({m.category})" if m.category else m.name,
        key="where_used_material_select",
    )
    wu_data = reports.build_where_used_report_data(session, wu_material.id)

    wc1, wc2, wc3 = st.columns(3)
    wc1.metric("Recipe versions using it", wu_data["recipe_version_count"])
    wc2.metric("Foam grades affected", wu_data["foam_grade_count"])
    wc3.metric("Product families affected", wu_data["product_family_count"])

    st.write("**Recipes using this material**")
    render_data_table(pd.DataFrame(wu_data["usage_rows"] or [{"—": "No data recorded"}]))
    st.write("**Target properties of affected foam grades**")
    render_data_table(pd.DataFrame(wu_data["target_rows"] or [{"—": "No data recorded"}]))
    st.write("**Trial precedent**")
    render_data_table(pd.DataFrame(wu_data["trial_rows"] or [{"—": "No data recorded"}]))

    st.download_button(
        "Download Word", data=reports.render_where_used_report_docx(wu_data),
        file_name=f"where_used_{wu_data['raw_material_id']}_report.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key="where_used_docx",
        on_click=log_export_click, args=("where_used_report_docx",),
        kwargs={"description": wu_data["raw_material_name"]},
    )
