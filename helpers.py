"""Shared UI helpers for PI3 Plant Edition pages."""

import datetime as dt
import json
import re

import altair as alt
import pandas as pd
import streamlit as st

import ai_assistant
import ai_governance
import audit_log
import reports
from auth import current_user
from db import (
    ExpertNote,
    FoamGrade,
    PI3InteractionLog,
    PI3InteractionReview,
    Plant,
    ProductFamily,
    ProductionRun,
    RecipeVersion,
    User,
    get_session,
)


def expert_note_plant_id_for_link(entity_type, entity_id, session):
    """Which plant a given Expert Note "link to" target belongs to, for the
    is_enabled_for_plant() check before pushing to PI3's vector store.
    Shared by views/20_Expert_Notes.py and render_save_to_expert_notes_button
    below, so both resolve a link the same way."""
    if entity_type == "production_run":
        r = session.get(ProductionRun, entity_id)
        return r.plant_id if r else None
    if entity_type == "foam_grade":
        g = session.get(FoamGrade, entity_id)
        return g.product_family.plant_id if g else None
    if entity_type == "product_family":
        f = session.get(ProductFamily, entity_id)
        return f.plant_id if f else None
    return None


def company_id_for_plant(plant_id, session):
    """Plant.company_id for a resolved plant_id (see
    expert_note_plant_id_for_link above), or None if plant_id is None or
    doesn't resolve. Used to tag every vector-store push with company_id
    alongside plant_id, so ai_assistant's file_search filters (see
    ai_assistant._file_search_filters(), fixed 2026-08-02 for Gate 3 Item
    21) can actually exclude a different company's notes - a plant_id tag
    alone isn't filtered on for that purpose, only company_id is."""
    if plant_id is None:
        return None
    plant = session.get(Plant, plant_id)
    return plant.company_id if plant else None


def expert_note_link_label(entity_type, entity_id, session):
    """Human-readable label for a given Expert Note "link to" target, used
    both on the Expert Notes screen and as the document title when a note
    is pushed into PI3's vector store."""
    if entity_type == "production_run":
        r = session.get(ProductionRun, entity_id)
        return f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}" if r else f"Run #{entity_id} (deleted)"
    if entity_type == "foam_grade":
        g = session.get(FoamGrade, entity_id)
        return f"Foam Grade: {g.grade_name}" if g else f"Foam Grade #{entity_id} (deleted)"
    if entity_type == "product_family":
        f = session.get(ProductFamily, entity_id)
        return f"Foam Family: {f.name}" if f else f"Foam Family #{entity_id} (deleted)"
    return f"{entity_type} #{entity_id}"


def expert_note_foam_grade_id_for_link(entity_type, entity_id, session):
    """Which foam grade (if any) a given Expert Note "link to" target
    belongs to - used to populate the "Foam grade" field when regenerating
    a PI3-sourced note's Word report on demand."""
    if entity_type == "foam_grade":
        return entity_id
    if entity_type == "production_run":
        r = session.get(ProductionRun, entity_id)
        return r.foam_grade_id if r else None
    return None


def analysis_unit_picker(grades, key_prefix):
    """Shared "Analyze by: Foam Grade / Foam Family" control for the three
    Industrial Intelligence pages built on analytics.py's pooled per-grade
    pipeline (Trend Analysis, Machine Settings vs Physical Properties
    Correlation, Machine Settings Optimization) - added 2026-08-02 so a
    reviewer can pool an entire product family's grades into one analysis
    instead of checking each grade one at a time. Recipe Optimization and
    Root-Cause Assistant don't use this: their sections (current
    formulation/cost, version diff, run-vs-prior-run diff) are inherently
    about one specific grade, not something that pools sensibly.

    `grades` must be the CALLER's already-scoped-and-filtered list of
    FoamGrade objects (e.g. already restricted to grades with quality test
    results) - foam families are derived from this same list via groupby,
    so a family only ever offers the grades that already passed the
    caller's own filter, and "Foam family X" never silently pulls in a
    grade that "Foam grade" mode wouldn't have offered on its own.

    Returns a dict:
    - mode: "grade" or "family"
    - label: display name (grade_name, or the family's name)
    - grade_ids: list of foam_grade_id(s) to pass into analytics.py
      functions (always a list, even in single-grade mode)
    - plant_id: for ai_assistant.is_enabled_for_plant() / availability_status()
    - link_type: "foam_grade" or "product_family" - for Expert Notes saving
      (see expert_note_plant_id_for_link/expert_note_link_label above)
    - entity_id: the grade's or family's own id, paired with link_type
    - state_key: unique string for namespacing st.session_state keys across
      grade/family selections (e.g. "grade-14" vs "family-3") so switching
      between them doesn't show a stale cached PI3 answer from the other mode
    - member_grade_names: sorted list of grade_name strings included in
      this unit (a single-item list in grade mode) - for prompts/captions
      that want to spell out exactly which grades were pooled
    """
    mode_choice = st.radio(
        "Analyze by", ["Foam grade", "Foam family"], key=f"{key_prefix}_unit_mode", horizontal=True
    )
    if mode_choice == "Foam grade":
        grade = st.selectbox(
            "Foam grade", grades, format_func=lambda g: g.grade_name, key=f"{key_prefix}_grade_select"
        )
        return {
            "mode": "grade",
            "label": grade.grade_name,
            "grade_ids": [grade.id],
            "plant_id": grade.product_family.plant_id if grade.product_family else None,
            "link_type": "foam_grade",
            "entity_id": grade.id,
            "state_key": f"grade-{grade.id}",
            "member_grade_names": [grade.grade_name],
        }

    families = sorted({g.product_family for g in grades if g.product_family}, key=lambda f: f.name)
    if not families:
        st.warning("No foam family available for these grades yet.")
        st.stop()
    family = st.selectbox(
        "Foam family", families,
        format_func=lambda f: f"{f.name} ({sum(1 for g in grades if g.product_family_id == f.id)} grade(s))",
        key=f"{key_prefix}_family_select",
    )
    family_grades = [g for g in grades if g.product_family_id == family.id]
    return {
        "mode": "family",
        "label": family.name,
        "grade_ids": [g.id for g in family_grades],
        "plant_id": family.plant_id,
        "link_type": "product_family",
        "entity_id": family.id,
        "state_key": f"family-{family.id}",
        "member_grade_names": sorted(g.grade_name for g in family_grades),
    }


def page_setup(title: str):
    """Kept for compatibility with existing pages, which all call this as
    their first Streamlit command. Page config, sidebar logo, and global
    styling are now set once in app.py (which runs first on every page view
    under st.navigation), so this is otherwise a no-op — calling
    st.set_page_config() a second time would raise an error.

    Also stashes the page's display title into session_state under
    "_current_page_title" - this is the one thing every page's call site
    already provides for free, and it's what auth.require_login()'s
    already-authenticated fast path reads to log page-view usage (Gate 6,
    Item 48) without needing every individual page file touched."""
    st.session_state["_current_page_title"] = title


def render_function_action_intro(function_text: str, action_text: str = None, action_steps=None, action_note: str = None):
    """Renders a page's opening explanation as a bordered 'Function / Action'
    box instead of a single caption line: Function is what this page lets
    you do, Action is what you actually need to do on it here, so both are
    stated explicitly instead of blended into one paragraph. Call this
    right after st.title(), in place of a plain st.caption() intro.

    Pass action_text for a plain one-paragraph Action (the original format).
    Pass action_steps (a list of strings) instead, when the page has a clear
    sequence to follow - each item is rendered as its own numbered "Step N."
    line instead of one dense paragraph, which reads much faster on pages
    with several tabs or a multi-part workflow. action_note is an optional
    closing line rendered under the steps (e.g. a tip that applies to every
    step, such as "manual entry and CSV/Excel import are both available
    throughout")."""
    with st.container(border=True):
        st.markdown(f"**Function:** {function_text}")
        if action_steps:
            st.markdown("**Action:**")
            for i, step in enumerate(action_steps, start=1):
                st.markdown(f"**Step {i}.** {step}")
            if action_note:
                st.markdown(action_note)
        else:
            st.markdown(f"**Action:** {action_text}")


CHART_ZOOM_HINT = "Tip: scroll to zoom in/out, click-and-drag to pan."


def render_scatter_chart_no_zero(df, x, y, color=None):
    """Same idea as st.scatter_chart(df, x=x, y=y), but with both axes
    scaled to the data's own min/max instead of always anchored at zero.
    Process settings and physical properties usually sit in a narrow band
    (e.g. an IFD/hardness result clustered around 170) - a zero-anchored
    axis squeezes that real spread into a thin sliver at the top of the
    chart, making points that are actually meaningfully different look
    identical. See _line_chart_no_zero on the Trend Analysis page for the
    same fix applied to a line chart.

    .interactive() keeps the same scroll-to-zoom / click-drag-to-pan
    behaviour the native st.scatter_chart already has, so swapping to this
    helper doesn't lose that."""
    encode_kwargs = {
        "x": alt.X(f"{x}:Q", scale=alt.Scale(zero=False)),
        "y": alt.Y(f"{y}:Q", scale=alt.Scale(zero=False)),
    }
    if color:
        encode_kwargs["color"] = alt.Color(f"{color}:N", title=color)
    chart = alt.Chart(df).mark_circle(size=90).encode(**encode_kwargs).interactive()
    st.altair_chart(chart, use_container_width=True)
    st.caption(CHART_ZOOM_HINT)


def render_pareto_chart(df, category_col, count_col, category_title=None):
    """Classic Pareto chart: bars = count per category (sorted highest to
    lowest, left to right), overlaid with a line marking the running
    cumulative percentage of the total - the standard quality-engineering
    view for "which few categories account for most of the total" (e.g.
    which properties account for most of a foam grade/family's failed
    quality test results). df must already be one row per category with
    its count in count_col; this function does the sorting and cumulative-%
    math itself, so callers just pass in raw counts.

    Two independent y-axes (count on the left, 0-100% on the right) since
    the two series are on unrelated scales - resolve_scale(y="independent")
    is what keeps Altair from forcing them to share one axis."""
    df = df.sort_values(count_col, ascending=False).reset_index(drop=True)
    total = df[count_col].sum()
    df = df.copy()
    df["Cumulative %"] = (df[count_col].cumsum() / total * 100) if total else 0.0
    order = df[category_col].tolist()

    bar = alt.Chart(df).mark_bar(color="#4C78A8").encode(
        x=alt.X(f"{category_col}:N", sort=order, title=category_title or category_col),
        y=alt.Y(f"{count_col}:Q", title="Count"),
        tooltip=[category_col, count_col],
    )
    line = alt.Chart(df).mark_line(color="#E45756", point=True).encode(
        x=alt.X(f"{category_col}:N", sort=order, title=category_title or category_col),
        y=alt.Y("Cumulative %:Q", title="Cumulative %", scale=alt.Scale(domain=[0, 100])),
        tooltip=[category_col, alt.Tooltip("Cumulative %:Q", format=".1f")],
    )
    chart = alt.layer(bar, line).resolve_scale(y="independent").properties(height=380)
    st.altair_chart(chart, use_container_width=True)
    st.caption(
        "Bars (left axis): count per category. Red line (right axis): cumulative % of the total, "
        "reading left to right - where it crosses 80% marks the few categories responsible for most "
        "of the total (the classic 80/20 pattern)."
    )


def activate_recipe_version(session, foam_grade_id, new_version):
    """Marks new_version as the active recipe for its foam grade, and
    deactivates whatever was active before it. Recipe versions don't
    coexist in production - a new one replaces the previous one - so
    exactly one version per foam grade should have is_active=True at a
    time. Call this right after adding+flushing new_version, before
    session.commit(). Does not touch approval_status - a version can be
    Approved but no longer active (superseded by a later revision)."""
    session.query(RecipeVersion).filter(
        RecipeVersion.foam_grade_id == foam_grade_id,
        RecipeVersion.id != new_version.id,
    ).update({"is_active": False}, synchronize_session=False)
    new_version.is_active = True


def next_version_label(current_label, existing_count):
    """Auto-generated label for the next recipe version: if the current
    label ends in a number (e.g. "28-MH-05"), increments that number
    (preserving its zero-padding width), matching the meaningful
    product-code style labels this app's users already use. Falls back to
    appending "-v{n}" if no trailing number is found. Applied directly to
    the new version - editing a recipe is not the place to be naming
    things by hand."""
    match = re.search(r"(\d+)$", current_label or "")
    if match:
        num = match.group(1)
        next_num = str(int(num) + 1).zfill(len(num))
        return current_label[: match.start()] + next_num
    return f"{(current_label or 'v').strip()}-v{existing_count + 1}"


def summarize_recipe_component_changes(old_components, new_rows, name_key="Raw material", php_key="php"):
    """Auto-generates a one-line "what changed" change note by comparing a
    recipe version's current components against the edited rows about to
    become its replacement - added/removed ingredients and any php changes,
    by name. Editing a recipe should not require writing a justification
    from scratch: the ingredient diff itself already is the record of what
    changed, exactly as the version history and Recipe Optimization's
    version-diff tool show it row-by-row - this just condenses that same
    comparison into the change_note field automatically.

    old_components: RecipeComponent objects (raw_material_name, php).
    new_rows: iterable of dict-like rows (e.g. a DataFrame's iterrows()
    values) with name_key/php_key columns. Returns "No ingredient
    changes." if the two ingredient lists are identical."""
    old_by_key = {
        c.raw_material_name.strip().lower(): (c.raw_material_name.strip(), c.php)
        for c in old_components
        if (c.raw_material_name or "").strip()
    }
    new_by_key = {}
    for row in new_rows:
        name = str(row.get(name_key) or "").strip()
        if not name:
            continue
        php_raw = row.get(php_key)
        php = float(php_raw) if pd.notna(php_raw) else None
        new_by_key[name.lower()] = (name, php)

    added, removed, changed = [], [], []
    for key, (name, new_php) in new_by_key.items():
        if key not in old_by_key:
            added.append(f"{name} ({new_php:.2f} php)" if new_php is not None else name)
        else:
            _, old_php = old_by_key[key]
            if round(old_php or 0, 2) != round(new_php or 0, 2):
                if old_php is not None and new_php is not None:
                    changed.append(f"{name} {old_php:.2f} -> {new_php:.2f} php")
                else:
                    changed.append(name)
    for key, (name, _old_php) in old_by_key.items():
        if key not in new_by_key:
            removed.append(name)

    parts = []
    if added:
        parts.append("Added " + ", ".join(added))
    if removed:
        parts.append("Removed " + ", ".join(removed))
    if changed:
        parts.append("Changed " + ", ".join(changed))
    return ("; ".join(parts) + ".") if parts else "No ingredient changes."


def render_data_table(df, max_height=None):
    """Renders a pandas DataFrame as a left-aligned, content-width HTML
    table with left-aligned cell text.

    st.dataframe(..., use_container_width=True) always stretches to the
    full page width no matter how little data there is, which spreads a
    handful of short rows across the whole screen and makes them harder
    to read, not easier. This sizes to the actual content instead and
    aligns it (and its cell text) to the left, matching how a plain data
    table normally reads. Deliberately avoids pandas' Styler (its
    HTML-rendering path requires jinja2, which isn't otherwise a
    dependency of this app) by building the HTML directly.

    max_height, if given (e.g. "400px"), wraps the table in a scrollable
    container - use this for tables that could have many rows, so a long
    listing doesn't push the rest of the page down indefinitely."""

    def _esc(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    header_cells = "".join(
        f"<th style='text-align:left; padding:6px 16px; border-bottom:2px solid #1B6FA8; "
        f"position:sticky; top:0; background:white;'>{_esc(c)}</th>"
        for c in df.columns
    )
    body_rows = []
    for _, row in df.iterrows():
        cells = "".join(
            f"<td style='text-align:left; padding:6px 16px; border-bottom:1px solid #E4ECF1;'>{_esc(v)}</td>"
            for v in row
        )
        body_rows.append(f"<tr>{cells}</tr>")
    table_html = (
        "<table style='border-collapse:collapse;'>"
        f"<thead><tr>{header_cells}</tr></thead><tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )
    if max_height:
        html = (
            f"<div style='overflow:auto; max-height:{max_height}; display:inline-block;'>{table_html}</div>"
        )
    else:
        html = f"<div style='display:inline-block;'>{table_html}</div>"
    st.markdown(html, unsafe_allow_html=True)


def confidence_badge(level: str) -> str:
    colors = {
        "Confirmed": "🟢",
        "Likely": "🟡",
        "Unconfirmed": "⚪",
        "Rejected": "🔴",
    }
    return f"{colors.get(level, '⚪')} {level or 'Unconfirmed'}"


def to_df(rows, columns=None):
    if not rows:
        return pd.DataFrame(columns=columns or [])
    return pd.DataFrame([r.__dict__ for r in rows]).drop(columns=["_sa_instance_state"], errors="ignore")


def selectbox_from_query(label, session, model, name_field="name", allow_none=True, key=None):
    """Render a selectbox populated from a DB query, return the selected object (or None)."""
    records = session.query(model).all()
    options = [None] if allow_none else []
    options += records
    return st.selectbox(
        label,
        options,
        format_func=lambda r: "—" if r is None else getattr(r, name_field, str(r)),
        key=key,
    )


def combine_date_time(label, key_prefix, default_date=None, default_time=None):
    """Render a date_input + time_input pair side by side and return a
    combined datetime.datetime. Used wherever a phase boundary, event, or
    sample timestamp needs both a date and a time from the operator."""
    c1, c2 = st.columns(2)
    d = c1.date_input(f"{label} — date", value=default_date or dt.date.today(), key=f"{key_prefix}_date")
    t = c2.time_input(f"{label} — time", value=default_time or dt.datetime.now().time(), key=f"{key_prefix}_time")
    return dt.datetime.combine(d, t)


def parse_dt(value):
    """Best-effort parse of a CSV/Excel cell into a datetime, or None."""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def parse_bool(value):
    """Best-effort parse of a CSV/Excel cell into a bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def selection_rows(event):
    """Best-effort extraction of selected row indices from a
    st.dataframe(..., on_select="rerun") return value, tolerant of the
    exact attribute/dict shape Streamlit uses."""
    if event is None:
        return []
    sel = getattr(event, "selection", None)
    if sel is None:
        try:
            sel = event["selection"]
        except Exception:
            return []
    rows = getattr(sel, "rows", None)
    if rows is None:
        try:
            rows = sel["rows"]
        except Exception:
            return []
    return list(rows or [])


def clickable_table(rows, key):
    """Render rows (list of dicts) as a single-row-selectable table. Returns
    the selected row's index, or None if nothing is selected. Used across
    every "list + edit + delete" page so row-selection works identically
    everywhere."""
    if not rows:
        return None
    event = st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    sel = selection_rows(event)
    return sel[0] if sel else None


RECIPE_COMPONENT_CATEGORY_ORDER = (
    "polyol",
    "isocyanate",
    "water",
    "catalyst",
    "surfactant",
    "color",
    "other",
)


def recipe_component_category(role_text, raw_material_name=""):
    """Classifies a recipe component into the standard formulation reading
    order a chemist expects: polyol(s) first, then isocyanate, water,
    catalysts, surfactant(s), then colors/other additives last. Buckets on
    the free-text role_in_formulation, falling back to raw_material_name for
    cases the role text alone would get wrong - e.g. water is often entered
    with a role like "chemical blowing agent", which would otherwise sort as
    an "other" additive instead of water. Returns one of
    RECIPE_COMPONENT_CATEGORY_ORDER."""
    role = (role_text or "").strip().lower()
    material = (raw_material_name or "").strip().lower()

    if material == "water" or material.split() == ["water"]:
        return "water"
    if "isocyanate" in role or "isocyanate" in material or re.search(r"\b(tdi|mdi)\b", role + " " + material):
        return "isocyanate"
    if "polyol" in role or "polyol" in material:
        return "polyol"
    if "catalyst" in role:
        return "catalyst"
    if "surfactant" in role or "stabiliz" in role or "stabilis" in role:
        return "surfactant"
    if any(word in role for word in ("colour", "color", "pigment", "colorant", "colourant")):
        return "color"
    return "other"


def recipe_component_sort_index(role_text, raw_material_name=""):
    """Sort key for ordering a recipe's components into the standard
    polyol -> isocyanate -> water -> catalyst -> surfactant -> color/other
    reading order. Pass to sorted(..., key=...); relies on sorted() being
    stable to preserve each category's original relative order."""
    category = recipe_component_category(role_text, raw_material_name)
    return RECIPE_COMPONENT_CATEGORY_ORDER.index(category)


def delete_with_confirm(label, on_confirm, key_prefix, extra_warning=""):
    """Render a checkbox + delete button gate, calling on_confirm() and
    rerunning only once the operator has explicitly ticked the confirm box.
    Shared by every page with a delete action so the confirmation UX (and
    the requirement to tick a box before the button becomes clickable) is
    consistent app-wide."""
    st.markdown(f"**Delete {label}**")
    if extra_warning:
        st.warning(extra_warning)
    confirm = st.checkbox(f"I understand — permanently delete {label}.", key=f"{key_prefix}_confirm")
    if st.button(f"Delete {label}", key=f"{key_prefix}_btn", type="primary", disabled=not confirm):
        on_confirm()
        st.success(f"{label} deleted.")
        st.rerun()


def page_access_grid(current_states, key_prefix):
    """Renders a Hidden / View only / Full access selectbox for every page
    in access_control.PAGE_CATALOG, each defaulting to its current state
    (current_states.get(page_key, ACCESS_FULL)). Meant to be called inside
    an st.form; returns {page_key: chosen_state} for the caller to hand to
    access_control.save_access_states() on submit. Shared by the Default
    User Roles (platform-owner templates) and User Roles (per-company)
    pages so both edit page access identically."""
    from access_control import ACCESS_FULL, ACCESS_HIDDEN, ACCESS_STATE_LABELS, ACCESS_VIEW_ONLY, PAGE_CATALOG

    order = [ACCESS_HIDDEN, ACCESS_VIEW_ONLY, ACCESS_FULL]
    selections = {}
    page_items = list(PAGE_CATALOG.items())
    cols = st.columns(2)
    for i, (page_key, title) in enumerate(page_items):
        with cols[i % 2]:
            current = current_states.get(page_key, ACCESS_FULL)
            selections[page_key] = st.selectbox(
                title, order, index=order.index(current),
                format_func=lambda s: ACCESS_STATE_LABELS[s],
                key=f"{key_prefix}_{page_key}",
            )
    return selections


def view_only_notice(action="adding, editing, and deleting"):
    """Standard banner shown once near the top of a page when
    access_control.can_use_page() says this role has View only access -
    i.e. it can see this page's data but shouldn't get any of its write
    controls. `action` can be customized for pages whose only "use" isn't
    add/edit/delete (e.g. an analysis page's Ask PI3 box, or Report's
    generate/download buttons)."""
    st.info(f"You have view-only access to this page - {action} is restricted for your role.")


def show_pending_banner(key):
    """Show a one-shot success banner stashed in session_state by an action
    that immediately called st.rerun() right after it. A plain st.success()
    called right before st.rerun() gets wiped before the user ever sees it,
    since the rerun restarts the script - this is why "Confirm import"
    buttons across the app could look like they silently did nothing, which
    led to operators clicking Confirm a second time and duplicating rows.
    Call this near the top of a page/section, before the action that might
    set the banner via set_pending_banner()."""
    msg = st.session_state.pop(key, None)
    if msg:
        st.success(msg)


def set_pending_banner(key, message):
    """Stash a success message so show_pending_banner() displays it after
    the immediate st.rerun() that follows a successful action."""
    st.session_state[key] = message


def dedupe_import_rows(rows, existing_keys, key_func):
    """Split CSV-import rows into (new_rows, duplicate_rows) based on
    key_func(row) already being present in existing_keys (a set, mutated in
    place as rows are accepted). Used by every "Confirm import" button so
    that clicking it twice - e.g. because the previous success message
    wasn't visibly persistent - can't silently insert the same rows again."""
    new_rows, dup_rows = [], []
    for row in rows:
        k = key_func(row)
        if k in existing_keys:
            dup_rows.append(row)
        else:
            new_rows.append(row)
            existing_keys.add(k)
    return new_rows, dup_rows


# PI3_Gaps_and_Ambiguities.docx finding 2.10: no CSV/Excel import anywhere
# enforced a file-size or row-count limit. These are deliberately generous
# (this app's imports are historical/bulk data loads, not per-transaction
# forms) - they exist to catch an accidental wrong-file upload (e.g. a
# multi-megabyte unrelated export) before it's parsed and held in memory,
# not to constrain a legitimate large historical migration. A caller that
# genuinely needs to import more than MAX_IMPORT_ROWS at once should split
# the source file rather than raising this constant.
MAX_UPLOAD_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB
MAX_IMPORT_ROWS = 20_000


def upload_within_size_limit(uploaded):
    """The file-size half of the MAX_UPLOAD_SIZE_BYTES/MAX_IMPORT_ROWS pair
    above, factored out for views/4_Production_Run_Trial_Record.py's 5 CSV
    import tabs - these predate csv_excel_uploader() and build their own
    st.file_uploader()/pd.read_csv() inline rather than calling it, so they
    need this check called explicitly right after their own file_uploader
    (the row-count half still has to be checked by each caller itself,
    after its own parse, since row-count depends on which columns/parser
    it uses). Shows an st.error and returns False if oversized; True
    otherwise (including when uploaded.size is unavailable)."""
    if uploaded.size and uploaded.size > MAX_UPLOAD_SIZE_BYTES:
        st.error(
            f"File is {uploaded.size / (1024 * 1024):.1f} MB, which is over the "
            f"{MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB limit for a single import. Split it into "
            "smaller files and import them one at a time."
        )
        return False
    return True


def import_within_row_limit(df):
    """The row-count half of the pair above, for the same 5 inline import
    tabs - call right after parsing, before doing anything with the rows.
    Shows an st.error and returns False if over MAX_IMPORT_ROWS; True
    otherwise."""
    if len(df) > MAX_IMPORT_ROWS:
        st.error(
            f"File has {len(df):,} rows, which is over the {MAX_IMPORT_ROWS:,}-row limit for a "
            "single import. Split it into smaller files and import them one at a time."
        )
        return False
    return True


def csv_excel_uploader(required_cols, optional_cols=None, key=None):
    """Render a file uploader for bulk CSV/Excel import, parse it, and check
    that the required columns are present. Used by every "CSV / Excel
    import" tab across the app so the upload/parse/column-check boilerplate
    (and its error messages) stay identical everywhere.

    Enforces MAX_UPLOAD_SIZE_BYTES (checked before parsing, from the
    uploaded file's own .size) and MAX_IMPORT_ROWS (checked after parsing,
    on row count) - see the constants above for why these exist and how
    generous they are.

    Returns (df, filename) once a valid file with all required columns has
    been uploaded, or (None, None) otherwise (an st.error/st.caption has
    already been shown as appropriate - callers don't need to repeat that).
    """
    optional_cols = optional_cols or []
    cols_caption = "Required columns: " + ", ".join(required_cols)
    if optional_cols:
        cols_caption += ". Optional columns: " + ", ".join(optional_cols)
    st.caption(cols_caption)

    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx"], key=key)
    if not uploaded:
        return None, None

    if not upload_within_size_limit(uploaded):
        return None, None

    try:
        df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
    except Exception as exc:
        st.error(f"Could not read file: {exc}")
        return None, None

    if not import_within_row_limit(df):
        return None, None

    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        st.error(f"File is missing required column(s): {', '.join(missing_cols)}. Import rejected.")
        return None, None

    return df, uploaded.name


def log_export_click(export_type, description=None):
    """Item 53 (Gate 6). Meant to be passed as a download button's
    on_click callback (args=(export_type,), kwargs={"description": ...}) -
    Streamlit's st.download_button supports on_click/args/kwargs in this
    app's installed version, so this fires exactly when the reviewer
    actually clicks Download, not merely when the button is rendered on
    screen. export_type is a short stable label (e.g. "production_run_report_pdf"),
    description an optional human-readable detail (e.g. the run number or
    report period) shown on the HTC pilot-analysis review page (Item 56)."""
    try:
        session = get_session()
        user = current_user()
        audit_log.log_export(
            session,
            export_type=export_type,
            description=description,
            user_id=user.get("id"),
            company_id=user.get("company_id"),
        )
    except Exception:
        pass


def pi3_verification_status(session, interaction_log_id):
    """Current review position for one PI3 answer, or None when the answer
    never required a recorded decision.

    Reviews are append-only (see db.PI3InteractionReview), so the newest row
    is the standing decision. An interaction that requires verification and
    has no review yet is Pending."""
    if not interaction_log_id:
        return None
    row = session.get(PI3InteractionLog, interaction_log_id)
    if row is None or not row.verification_required:
        return None
    latest = (
        session.query(PI3InteractionReview)
        .filter(PI3InteractionReview.pi3_interaction_log_id == interaction_log_id)
        .order_by(PI3InteractionReview.created_at.desc())
        .first()
    )
    return latest.review_status if latest else ai_governance.REVIEW_PENDING


def render_pi3_verification_panel(session, interaction_log_id, key_prefix):
    """The verification control that sits with a PI3 answer (CR of 19 Aug
    2026, sections 7 and 13).

    Shown only for interactions classified Process / Safety Relevant - those
    are the answers that can change what is dosed or how the line is set, so
    those are the ones that need a qualified person to decide before trial or
    operational use. An advisory answer keeps the ordinary caption the page
    already shows and gets no panel; adding one everywhere would turn the
    notice into wallpaper.

    Returns the current status string (or None), so the caller can carry it
    into the .docx export - a downloaded recommendation has to say where it
    stands, or it reads as approved once it leaves the screen.

    The decision is written through audit_log.log_pi3_review, which appends.
    Nothing here edits the interaction: the original question and answer stay
    exactly as generated, and a changed mind is a new row."""
    if not interaction_log_id:
        return None
    row = session.get(PI3InteractionLog, interaction_log_id)
    if row is None or not row.verification_required:
        return None

    # Record that the notice was actually put in front of someone. Written
    # once - the column stays True afterwards - and left for
    # close_out_session() to commit with the rest of the rerun.
    if not row.verification_message_shown:
        row.verification_message_shown = True
        session.flush()

    reviews = (
        session.query(PI3InteractionReview)
        .filter(PI3InteractionReview.pi3_interaction_log_id == interaction_log_id)
        .order_by(PI3InteractionReview.created_at.desc())
        .all()
    )
    latest = reviews[0] if reviews else None
    status = latest.review_status if latest else ai_governance.REVIEW_PENDING
    display = ai_governance.REVIEW_DISPLAY.get(status, "")

    with st.container(border=True):
        if latest is None:
            st.markdown(f"**Verification required.** {display}")
        else:
            reviewer = session.get(User, latest.reviewer_user_id) if latest.reviewer_user_id else None
            who = (reviewer.display_name or reviewer.email) if reviewer is not None else "an unrecorded user"
            when = latest.created_at.strftime("%Y-%m-%d %H:%M UTC") if latest.created_at else "an unrecorded time"
            # The status text already says what was decided, so REVIEW_DISPLAY
            # is used only for the Pending case - repeating it here read as
            # "Accepted... Accepted...".
            st.markdown(f"**{status}** — reviewed by {who} on {when}.")
            if latest.review_comment:
                st.caption(f"Comment: {latest.review_comment}")
            if latest.customer_final_action:
                st.caption(f"Action taken: {latest.customer_final_action}")

        # WHO MAY QUALIFY AN ANSWER - Stefan's ruling, 19 Aug 2026.
        #
        # Only the company that generated the answer can record a decision on
        # it. HTC, as the platform owner, must never be able to qualify a
        # customer's PI3 output: doing so would make HTC a party to accepting
        # the recommendation, which is precisely the responsibility this whole
        # CR exists to place with the customer. A customer that chooses not to
        # qualify an answer has made its own decision, and the audit trail
        # records that it was left Pending.
        #
        # The check is on company, not on role or on platform-owner status, so
        # a platform-owner user reviewing an answer their OWN company generated
        # still can - they own that one.
        viewer_company_id = current_user().get("company_id")
        may_record = viewer_company_id is not None and viewer_company_id == row.company_id

        if not may_record:
            st.caption(
                "The decision on this answer belongs to the company that generated it. "
                "This view is read-only."
            )
        if may_record:
            label = "Record a decision" if latest is None else "Record a further decision"
            with st.expander(label, expanded=False):
                st.caption(
                    "The decision is added to this answer's history. Previous decisions stay on "
                    "record, and the answer above is unchanged by it."
                )
                with st.form(f"{key_prefix}_verification_form"):
                    decision = st.selectbox(
                        "Decision *",
                        [x for x in ai_governance.REVIEW_STATUSES if x != ai_governance.REVIEW_PENDING],
                        key=f"{key_prefix}_verification_decision",
                    )
                    comment = st.text_area(
                        "Reviewer comment",
                        help="What was checked, and against what.",
                        key=f"{key_prefix}_verification_comment",
                    )
                    action = st.text_area(
                        "Customer action taken",
                        help="What was actually done, where that differs from the recommendation.",
                        key=f"{key_prefix}_verification_action",
                    )
                    if st.form_submit_button("Record decision"):
                        written = audit_log.log_pi3_review(
                            session,
                            pi3_interaction_log_id=interaction_log_id,
                            review_status=decision,
                            reviewer_user_id=current_user().get("id"),
                            review_comment=(comment or "").strip() or None,
                            customer_final_action=(action or "").strip() or None,
                        )
                        if written is None:
                            st.error(
                                "The decision was not recorded. Try again, or ask your administrator "
                                "to check the error log."
                            )
                        else:
                            session.commit()
                            st.rerun()

        if len(reviews) > 1:
            with st.expander(f"Earlier decisions ({len(reviews) - 1})", expanded=False):
                for r in reviews[1:]:
                    stamp = r.created_at.strftime("%Y-%m-%d %H:%M UTC") if r.created_at else "—"
                    st.markdown(f"- **{r.review_status}** · {stamp}")
                    if r.review_comment:
                        st.caption(r.review_comment)

    return status


def render_pi3_docx_download(
    session, plant_id, key_prefix, question_label, answer, tool_log=None,
    page_context="", foam_grade_id=None, interaction_log_id=None,
):
    """Shared 'Download as Word (.docx)' button for any PI3-generated
    answer - both the older fixed-prompt sections (Recipe Optimization's
    formulation recommendation, Trend Analysis's and Process-Property
    Correlation's interpretation) and the free-form Ask PI3 box below them
    call this, so every PI3 answer on every page can be exported the same
    way, with identical formatting (see reports.render_pi3_qa_report_docx).

    `question_label` is what appears as "Question asked" in the export -
    for the free-form box this is literally what the reviewer typed; for a
    fixed-prompt section there's no user-typed question, so callers pass a
    short description of what was requested instead (e.g. "PI3 formulation
    recommendation for <grade>"). `tool_log` is optional and only
    populated for the free-form box, which goes through the tool-calling
    agent - fixed-prompt sections call ai_assistant.ask_assistant()
    directly (file_search only, no tools), so they have none to show."""
    grade_name = None
    if foam_grade_id:
        grade = session.get(FoamGrade, foam_grade_id)
        grade_name = grade.grade_name if grade else None

    report_data = reports.build_pi3_qa_report_data(
        question=question_label,
        answer=answer,
        tool_log=tool_log or [],
        page_context=page_context,
        plant_name=reports.plant_label(session, plant_id),
        foam_grade_name=grade_name,
        asked_by=current_user().get("display_name"),
        # CR of 19 Aug 2026, section 13: a recommendation that leaves the
        # screen has to say where its verification stands, or the reader has
        # no way to tell an unreviewed answer from an accepted one.
        verification_status=pi3_verification_status(session, interaction_log_id),
    )
    st.download_button(
        "Download as Word (.docx)",
        data=reports.render_pi3_qa_report_docx(report_data),
        file_name=f"pi3_report_{key_prefix}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key=f"{key_prefix}_download_docx",
        on_click=log_export_click,
        args=("pi3_answer_docx",),
        kwargs={"description": question_label},
    )


def render_save_to_expert_notes_button(
    session, key_prefix, answer, question_label, link_type, entity_id, tool_log=None, disabled=False,
):
    """Shared 'Save to Expert Notes' button for any PI3-generated answer -
    lets the reviewer explicitly keep an answer worth remembering, rather
    than every PI3 interaction being saved automatically (which would fill
    Expert Notes with one-off/throwaway questions no one wants to see
    again). Saved notes are tagged source="PI3" and, same as a manually-
    typed expert note, pushed into PI3's own vector store if PI3 is enabled
    for the relevant plant - so a genuinely useful PI3 insight can surface
    again in future Root-Cause Assistant searches, same as human-authored
    knowledge.

    `link_type` is one of the Expert Notes "link to" types ("foam_grade",
    "production_run", "product_family"), `entity_id` the id of that record.

    Guards against saving the same answer twice: once saved, the button is
    replaced with a confirmation until a new answer replaces this one -
    callers must pop f"{key_prefix}_saved_note_id" from session_state
    whenever they store a new answer under f"{key_prefix}_answer" (or the
    page's equivalent), or this will keep showing "already saved" for an
    answer that was never actually saved."""
    if entity_id is None:
        return
    saved_id = st.session_state.get(f"{key_prefix}_saved_note_id")
    if saved_id:
        st.caption("✓ Saved to Expert Notes.")
        return
    if st.button("Save to Expert Notes", key=f"{key_prefix}_save_note_btn", disabled=disabled):
        plant_id = expert_note_plant_id_for_link(link_type, entity_id, session)
        note = ExpertNote(
            linked_entity_type=link_type,
            linked_entity_id=entity_id,
            note_text=answer,
            confidence_level="Unconfirmed",
            author=current_user().get("display_name"),
            source="PI3",
            pi3_question=question_label,
            pi3_tool_log_json=json.dumps(tool_log) if tool_log else None,
        )
        if ai_assistant.is_enabled_for_plant(session, plant_id):
            link_label = expert_note_link_label(link_type, entity_id, session)
            doc_text = f"PI3 insight on {link_label}\nQuestion: {question_label}\n\n{answer}"
            company_id = company_id_for_plant(plant_id, session)
            note.vector_store_file_id = ai_assistant.push_document_to_vector_store(
                link_label,
                doc_text,
                metadata={"plant_id": plant_id, "company_id": company_id} if plant_id else None,
            )
        session.add(note)
        session.commit()
        st.session_state[f"{key_prefix}_saved_note_id"] = note.id
        st.rerun()


def render_pi3_feedback_control(session, interaction_log_id, key_prefix):
    """Item 55 (Gate 6). Thumbs up/down + optional comment on one specific
    PI3 answer, linked back to the PI3InteractionLog row that answer was
    recorded under (see ai_assistant.ask_assistant/ask_plant_question,
    which both now return that row's id for exactly this purpose). Renders
    nothing if interaction_log_id is None - logging the interaction itself
    can fail (see audit_log's best-effort design), and a feedback control
    with nothing to link to would be worse than none at all.

    Once submitted, the buttons are replaced with a thank-you and the
    control won't re-render for this answer - callers don't need to guard
    against double submission themselves. Same dedup pattern as
    render_save_to_expert_notes_button: whenever a NEW answer replaces the
    one this feedback was about, pop f"{key_prefix}_feedback_submitted"
    from session_state (mirroring the existing "_saved_note_id" pop) or
    this will keep showing "thanks" for an answer that was never rated."""
    if interaction_log_id is None:
        return
    if st.session_state.get(f"{key_prefix}_feedback_submitted"):
        st.caption("✓ Thanks for the feedback.")
        return

    st.caption("Was this answer useful?")
    up_col, down_col, _ = st.columns([1, 1, 6])
    rating_key = f"{key_prefix}_feedback_rating"
    if up_col.button("👍", key=f"{key_prefix}_feedback_up"):
        st.session_state[rating_key] = "up"
    if down_col.button("👎", key=f"{key_prefix}_feedback_down"):
        st.session_state[rating_key] = "down"

    rating = st.session_state.get(rating_key)
    if rating:
        comment = st.text_input(
            "Anything to add? (optional)", key=f"{key_prefix}_feedback_comment"
        )
        if st.button("Submit feedback", key=f"{key_prefix}_feedback_submit"):
            audit_log.log_pi3_feedback(
                session, interaction_log_id, rating,
                user_id=current_user().get("id"), comment=comment or None,
            )
            st.session_state[f"{key_prefix}_feedback_submitted"] = True
            st.session_state.pop(rating_key, None)
            st.rerun()


def render_ask_pi3_section(
    session, plant_id, default_foam_grade_id, page_context, sample_questions, key_prefix,
    note_link_type="foam_grade", note_entity_id=None, disabled=False,
):
    """Free-form 'ask PI3 anything about this plant's data' box, shared by
    Recipe Optimization, Machine Settings vs Physical Properties
    Correlation, and Trend Analysis -
    this is the same spot on each page that already had a fixed, single-
    purpose PI3 prompt; this section sits alongside that one rather than
    replacing it, so the existing tested recommendation/interpretation
    still works exactly as before.

    Silently renders nothing if PI3 isn't configured or isn't enabled for
    this plant - the existing fixed-prompt section above this one on each
    page already shows the right explanation for that (see
    ai_assistant.availability_status), so this avoids showing the same
    "enable PI3" message twice on one page.

    `page_context` is a short plain-language string describing what page/
    grade/property the reviewer is currently looking at, so PI3 can
    disambiguate an underspecified question ("is this drifting" ->
    drifting for which property, on which page). `sample_questions` is a
    list of ready-made example questions shown in a dropdown - answers the
    "how would a user know what's answerable" problem without requiring
    them to write SQL-shaped questions themselves.
    """
    if ai_assistant.availability_status(session, plant_id) != "enabled":
        return

    st.markdown("**Ask PI3 your own question**")
    st.caption(
        "Ask anything about this plant's own production data - PI3 checks the actual recorded "
        "numbers (it never guesses) and can also draw on expert notes and historical cases for "
        "context. Answers are historical reference for your own investigation, not instructions."
    )

    sample_key = f"{key_prefix}_sample"
    question_key = f"{key_prefix}_question"

    def _apply_sample_question():
        # Widgets that pass both `key` and `value` only honor `value` on their
        # very first render - once session_state has an entry for that key
        # (which it does after the first rerun), later `value=` changes are
        # silently ignored. That meant picking a different sample question
        # from the dropdown below never actually updated the text area, so
        # it stayed empty and the "Ask PI3" button stayed disabled. Writing
        # straight into st.session_state[question_key] from this on_change
        # callback runs BEFORE the text_area widget is (re)built, so it picks
        # up the new value like any other externally-set session_state entry.
        chosen = st.session_state.get(sample_key)
        st.session_state[question_key] = "" if chosen in (None, "Type my own...") else chosen

    if sample_questions:
        st.selectbox(
            "Example questions",
            ["Type my own..."] + list(sample_questions),
            key=sample_key,
            on_change=_apply_sample_question,
        )

    question = st.text_area("Your question", key=question_key)

    if st.button("Ask PI3", key=f"{key_prefix}_ask_btn", disabled=disabled or not question.strip()):
        with st.spinner("Using PI3..."):
            answer, tool_log, interaction_log_id = ai_assistant.ask_plant_question(
                session,
                plant_id,
                question,
                default_foam_grade_id=default_foam_grade_id,
                page_context=page_context,
            )
        if answer:
            st.session_state[f"{key_prefix}_answer"] = answer
            st.session_state[f"{key_prefix}_tool_log"] = tool_log
            st.session_state[f"{key_prefix}_asked"] = question
            st.session_state[f"{key_prefix}_interaction_log_id"] = interaction_log_id
            st.session_state.pop(f"{key_prefix}_saved_note_id", None)
            st.session_state.pop(f"{key_prefix}_feedback_submitted", None)

    answer = st.session_state.get(f"{key_prefix}_answer")
    if answer:
        st.caption(f"You asked: {st.session_state.get(f'{key_prefix}_asked', '')}")
        st.write(answer)
        tool_log = st.session_state.get(f"{key_prefix}_tool_log") or []
        st.caption("Confirm through your own investigation before acting on this.")
        # A free-form question that asks for a formulation or process change is
        # classified Process / Safety Relevant by ai_governance, so the panel
        # appears here too - not only on the three fixed Intelligence
        # functions. An ordinary "what was the average density" question is
        # advisory and renders nothing.
        render_pi3_verification_panel(
            session, st.session_state.get(f"{key_prefix}_interaction_log_id"), key_prefix=key_prefix,
        )
        render_pi3_feedback_control(
            session, st.session_state.get(f"{key_prefix}_interaction_log_id"), key_prefix=key_prefix,
        )

        dl_col, save_col = st.columns([1, 1])
        with dl_col:
            render_pi3_docx_download(
                session,
                plant_id,
                key_prefix=key_prefix,
                question_label=st.session_state.get(f"{key_prefix}_asked", ""),
                answer=answer,
                tool_log=tool_log,
                interaction_log_id=st.session_state.get(f"{key_prefix}_interaction_log_id"),
                page_context=page_context,
                foam_grade_id=default_foam_grade_id,
            )
        with save_col:
            render_save_to_expert_notes_button(
                session,
                key_prefix=key_prefix,
                answer=answer,
                question_label=st.session_state.get(f"{key_prefix}_asked", ""),
                link_type=note_link_type,
                entity_id=note_entity_id if note_entity_id is not None else default_foam_grade_id,
                tool_log=tool_log,
                disabled=disabled,
            )
