"""Screen 7: Quality Issue

Approved terminology: "Quality Issue", not "Defect Module". The
underlying QualityObservation model/table name is unchanged — this is a
display-text rename only.

Keyed primarily to the production run — routine batches get quality
issues too, not just formal trials. Linking to a trial is optional.
"""

import datetime as dt

import pandas as pd
import streamlit as st
from sqlalchemy import or_

import quality_issue_taxonomy
from access_control import can_use_page
from db import (
    CONFIDENCE_LEVELS,
    SAMPLE_SOURCE_TYPES,
    SEVERITIES,
    CustomerTrial,
    FoamGrade,
    OptimizationTrial,
    ProductionRun,
    QualityObservation,
    get_session,
    init_db,
    sample_source_fk_field,
)
from auth import current_user, logout_button, require_login
from helpers import (
    clickable_table,
    confidence_badge,
    csv_excel_uploader,
    dedupe_import_rows,
    delete_with_confirm,
    log_export_click,
    page_setup,
    render_data_table,
    render_function_action_intro,
    render_pareto_chart,
    set_pending_banner,
    show_pending_banner,
    view_only_notice,
)
import reports
from tenant_scope import (
    apply_scope,
    company_picker,
    customer_trial_ids_for_company,
    grade_ids_for_company,
    optimization_trial_ids_for_company,
    run_ids_for_company,
)

# An issue belongs to exactly one parent - a production run, a customer
# trial, or an optimization trial (see db.SAMPLE_SOURCE_TYPES). CSV rows
# carry all three FK columns as optional and must set exactly one.
OBSERVATION_REQUIRED_COLUMNS = ["observation_type"]
OBSERVATION_OPTIONAL_COLUMNS = [
    "production_run_id", "customer_trial_id", "optimization_trial_id",
    "severity", "frequency", "location_in_block", "suspected_cause",
    "confidence_level", "product_impact", "customer_impact", "notes", "observed_at",
]


def _obs_source_desc(obs):
    """(source label, human-readable parent description) for a
    QualityObservation, resolving whichever of the three FKs is set."""
    if obs.production_run_id is not None:
        run = obs.production_run
        desc = f"Run #{run.id} — {run.foam_grade.grade_name} · {run.run_date}" if run else f"Run #{obs.production_run_id}"
        return "Production Run", desc
    if obs.customer_trial_id is not None:
        t = obs.customer_trial
        desc = f"Trial #{t.id} — {t.customer_name}" if t else f"Trial #{obs.customer_trial_id}"
        return "Customer Trial", desc
    if obs.optimization_trial_id is not None:
        t = obs.optimization_trial
        ref = (t.improvement_initiative_reference or "(no reference)") if t else ""
        desc = f"Trial #{t.id} — {ref}" if t else f"Trial #{obs.optimization_trial_id}"
        return "Optimization Trial", desc
    return "—", "—"


def _obs_foam_grade_id(obs):
    """foam_grade_id reachable from whichever of the three parents this
    issue belongs to - used to apply the Foam scope filter/chart across
    all three sources uniformly."""
    if obs.production_run_id is not None:
        return obs.production_run.foam_grade_id if obs.production_run else None
    if obs.customer_trial_id is not None:
        return obs.customer_trial.foam_grade_id if obs.customer_trial else None
    if obs.optimization_trial_id is not None:
        return obs.optimization_trial.foam_grade_id if obs.optimization_trial else None
    return None


def _issue_type_picker(key_prefix, current_value=None):
    """Category -> Issue type dependent picker for the controlled "Issue
    type" vocabulary (see quality_issue_taxonomy.py). Deliberately rendered
    OUTSIDE any st.form - Streamlit forms only rerun on submit, so a
    dependent second selectbox (whose options depend on the first) placed
    inside a form would show stale options for the category last picked,
    not the one just chosen. These widgets react immediately instead, and
    the resolved issue-type string is read back into the enclosing form's
    submit handler as a plain variable (safe, since the whole script reruns
    top-to-bottom on every interaction, form submit included).

    current_value pre-selects the matching category/issue type when
    editing an existing row. A legacy value that predates this taxonomy (or
    was CSV-imported before it existed) won't match any entry - it falls
    back to "Other / not yet classified" with the existing text preserved
    in the free-text box rather than silently discarded.

    Returns (resolved_issue_type, typical_causes_or_None).
    """
    match = quality_issue_taxonomy.lookup(current_value) if current_value else None
    cats = quality_issue_taxonomy.categories()
    if match:
        default_category = match["category"]
    elif current_value:
        default_category = "Other / not yet classified"
    else:
        default_category = cats[0]
    category = st.selectbox(
        "Issue category *",
        cats,
        index=cats.index(default_category) if default_category in cats else 0,
        key=f"{key_prefix}_category",
    )
    issue_options = quality_issue_taxonomy.issue_types_for_category(category)
    issue_names = [it["name"] for it in issue_options]
    default_issue_index = (
        issue_names.index(match["name"])
        if match and match["category"] == category and match["name"] in issue_names
        else 0
    )
    issue_name = st.selectbox(
        "Issue type *", issue_names, index=default_issue_index, key=f"{key_prefix}_issue_name",
    )
    entry = quality_issue_taxonomy.lookup(issue_name)

    if issue_name == quality_issue_taxonomy.OTHER_ISSUE_NAME:
        other_default = current_value if (current_value and not match) else ""
        other_text = st.text_input(
            "Describe the issue *",
            value=other_default,
            placeholder="e.g. unusual blue discoloration on the cut face",
            key=f"{key_prefix}_other_text",
        )
        return other_text.strip(), None

    return issue_name, (entry["typical_causes"] if entry else None)

page_setup("Quality Issue")
init_db()
require_login()
logout_button()

st.title("Quality Issue")
render_function_action_intro(
    function_text=(
        "Captures what went wrong (or was noticed) on a batch - the issue type, severity, "
        "frequency, where in the block it showed up, suspected cause, and how confident the "
        "report is - building a factual, confidence-rated history of quality issues per foam "
        "grade instead of word-of-mouth. Each issue belongs to exactly one source: a routine "
        "production run, a Customer Trial, or an Optimization Trial."
    ),
    action_text=(
        "Pick which of the three you're logging against (Production Run / Customer Trial / "
        "Optimization Trial), then log the issue type, severity, frequency, location in the block, "
        "suspected cause, and your confidence level in that assessment. Use the CSV/Excel import "
        "tab to bulk-load a batch of issues instead of entering them one by one."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("quality_issue", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="qi_company_filter"
)
active_company_id = company.id if company else None
scoped_run_ids = run_ids_for_company(session, active_company_id)
scoped_ct_ids = customer_trial_ids_for_company(session, active_company_id)
scoped_ot_ids = optimization_trial_ids_for_company(session, active_company_id)

runs = (
    apply_scope(session.query(ProductionRun), ProductionRun.id, scoped_run_ids)
    .order_by(ProductionRun.created_at.desc())
    .all()
)
customer_trials = (
    apply_scope(session.query(CustomerTrial), CustomerTrial.id, scoped_ct_ids)
    .order_by(CustomerTrial.created_at.desc())
    .all()
)
optimization_trials = (
    apply_scope(session.query(OptimizationTrial), OptimizationTrial.id, scoped_ot_ids)
    .order_by(OptimizationTrial.created_at.desc())
    .all()
)
if not runs and not customer_trials and not optimization_trials:
    st.warning(
        "Create a production run, customer trial, or optimization trial first "
        "(Production Run / Customer Trials / Optimization Trials pages)."
    )
    st.stop()

tab_obs_manual, tab_obs_import = st.tabs(["Add quality issue", "CSV / Excel import"])

with tab_obs_manual:
    with st.expander("Add quality issue", expanded=False):
        if not page_usable:
            st.caption("View-only access - adding a quality issue is restricted for your role.")
        else:
            st.caption(
                "Issue type is a controlled list drawn from Laader Berg's slabstock foaming "
                "troubleshooting guide, grouped by category - not free text, so the same fault "
                "always gets recorded the same way and can be counted/trended reliably."
            )
            observation_type, _typical_causes = _issue_type_picker("add_obs")
            if _typical_causes:
                st.caption(f"Typical causes/checks: {_typical_causes}")

            available_sources = [
                s for s in SAMPLE_SOURCE_TYPES
                if (s == "Production Run" and runs)
                or (s == "Customer Trial" and customer_trials)
                or (s == "Optimization Trial" and optimization_trials)
            ]
            source_type = st.selectbox("Record against *", available_sources, key="obs_source_type")
            if source_type == "Production Run":
                parent = st.selectbox(
                    "Production run *", runs,
                    format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
                    key="obs_run_select",
                )
            elif source_type == "Customer Trial":
                parent = st.selectbox(
                    "Customer trial *", customer_trials,
                    format_func=lambda t: f"Trial #{t.id} — {t.foam_grade.grade_name} · {t.customer_name} · {t.trial_date or '—'}",
                    key="obs_ct_select",
                )
            else:
                parent = st.selectbox(
                    "Optimization trial *", optimization_trials,
                    format_func=lambda t: (
                        f"Trial #{t.id} — {t.foam_grade.grade_name} · "
                        f"{t.improvement_initiative_reference or '(no reference)'} · {t.trial_date or '—'}"
                    ),
                    key="obs_ot_select",
                )
            with st.form("add_observation"):
                st.caption(f"Issue type: **{observation_type or '(describe the issue above)'}**")
                c1, c2 = st.columns(2)
                severity = c1.selectbox("Severity", SEVERITIES)
                frequency = c2.selectbox("Frequency", ["One-off", "Recurring"])
                location_in_block = st.text_input("Location in block")
                suspected_cause = st.text_area("Suspected cause")
                confidence_level = st.selectbox("Confidence level *", CONFIDENCE_LEVELS, index=2)
                product_impact = st.text_area("Product impact")
                customer_impact = st.text_area("Customer impact")
                notes = st.text_area("Notes")
                observed_at = st.date_input("Observed on", value=dt.date.today())
                submitted = st.form_submit_button("Save issue")
                if submitted:
                    if not observation_type:
                        st.error("Issue type is required.")
                    else:
                        new_obs = QualityObservation(
                            observation_type=observation_type,
                            severity=severity,
                            frequency=frequency,
                            location_in_block=location_in_block,
                            suspected_cause=suspected_cause,
                            confidence_level=confidence_level,
                            product_impact=product_impact,
                            customer_impact=customer_impact,
                            notes=notes,
                            observed_at=observed_at,
                        )
                        setattr(new_obs, sample_source_fk_field(source_type), parent.id)
                        session.add(new_obs)
                        session.commit()
                        st.success("Quality issue saved.")
                        st.rerun()

with tab_obs_import:
    show_pending_banner("observation_import_msg")
    with st.expander("Accepted issue-type names (must match exactly, case-insensitive)"):
        for _cat in quality_issue_taxonomy.categories():
            st.write(f"**{_cat}**")
            st.write(", ".join(it["name"] for it in quality_issue_taxonomy.issue_types_for_category(_cat)))
    st.caption(
        "Each row needs exactly one of production_run_id / customer_trial_id / optimization_trial_id "
        "set, matching which of the three that issue belongs to."
    )
    obs_df, obs_filename = csv_excel_uploader(
        OBSERVATION_REQUIRED_COLUMNS, OBSERVATION_OPTIONAL_COLUMNS, key="observation_upload"
    )
    if obs_df is not None:
        import_run_ids = {r.id for r in runs}
        import_ct_ids = {t.id for t in customer_trials}
        import_ot_ids = {t.id for t in optimization_trials}

        def _row_fk(row):
            """(fk_field, fk_value) if exactly one of the three FK columns
            is set to a value in-scope, else (None, None)."""
            candidates = []
            for field, id_set in (
                ("production_run_id", import_run_ids),
                ("customer_trial_id", import_ct_ids),
                ("optimization_trial_id", import_ot_ids),
            ):
                val = row.get(field)
                if pd.notna(val) and str(val).strip():
                    candidates.append((field, val, id_set))
            if len(candidates) != 1:
                return None, None
            field, val, id_set = candidates[0]
            try:
                val_int = int(val)
            except (TypeError, ValueError):
                return None, None
            return (field, val_int) if val_int in id_set else (None, None)

        good_rows, bad_rows = [], []
        for _, row in obs_df.iterrows():
            try:
                fk_field, _fk_val = _row_fk(row)
                # Issue type must match the controlled taxonomy (see
                # quality_issue_taxonomy.py) the same as the manual entry
                # form now requires - a CSV can't be used to sneak free
                # text back in. Matched case-insensitively since a
                # spreadsheet author might type "shrinkage" instead of the
                # exact stored casing "Shrinkage".
                issue_match = quality_issue_taxonomy.lookup_case_insensitive(
                    str(row.get("observation_type", "") or "")
                )
                ok = bool(fk_field and issue_match)
            except (TypeError, ValueError):
                ok = False
            if ok:
                good_rows.append(row)
            else:
                bad_rows.append(row)

        st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
        if bad_rows:
            st.warning(
                "Flagged rows don't have exactly one in-scope production_run_id / customer_trial_id "
                "/ optimization_trial_id set, or their observation_type doesn't match one of the "
                "controlled issue-type names (see the 'Add quality issue' dropdown above for the "
                "exact list of accepted values)."
            )
            render_data_table(pd.DataFrame(bad_rows), max_height="300px")

        if good_rows and st.button("Confirm import", key="confirm_observation_import", disabled=not page_usable):
            existing_keys = set()
            for o in session.query(QualityObservation).all():
                src_label, _ = _obs_source_desc(o)
                fk_col = sample_source_fk_field(src_label) if src_label in SAMPLE_SOURCE_TYPES else None
                if fk_col:
                    existing_keys.add((fk_col, getattr(o, fk_col), o.observation_type.strip().lower(), o.observed_at))

            def _obs_key(row):
                fk_field, fk_val = _row_fk(row)
                observed_val = pd.to_datetime(row.get("observed_at"), errors="coerce")
                return (
                    fk_field,
                    fk_val,
                    str(row["observation_type"]).strip().lower(),
                    observed_val.date() if not pd.isna(observed_val) else dt.date.today(),
                )

            new_rows, dup_rows = dedupe_import_rows(good_rows, existing_keys, key_func=_obs_key)

            for row in new_rows:
                severity_val = str(row.get("severity", "") or "").strip()
                frequency_val = str(row.get("frequency", "") or "").strip()
                confidence_val = str(row.get("confidence_level", "") or "").strip()
                observed_val = pd.to_datetime(row.get("observed_at"), errors="coerce")
                # Store the taxonomy's own canonical spelling/casing, not
                # whatever the CSV happened to contain - already validated
                # to match above, so this lookup can't come back empty here.
                canonical_issue_type = quality_issue_taxonomy.lookup_case_insensitive(
                    str(row["observation_type"])
                )["name"]
                fk_field, fk_val = _row_fk(row)
                new_obs = QualityObservation(
                    observation_type=canonical_issue_type,
                    severity=severity_val if severity_val in SEVERITIES else "Low",
                    frequency=frequency_val if frequency_val in ["One-off", "Recurring"] else "One-off",
                    location_in_block=str(row.get("location_in_block", "") or ""),
                    suspected_cause=str(row.get("suspected_cause", "") or ""),
                    confidence_level=confidence_val if confidence_val in CONFIDENCE_LEVELS else "Unconfirmed",
                    product_impact=str(row.get("product_impact", "") or ""),
                    customer_impact=str(row.get("customer_impact", "") or ""),
                    notes=str(row.get("notes", "") or ""),
                    observed_at=observed_val.date() if not pd.isna(observed_val) else dt.date.today(),
                )
                setattr(new_obs, fk_field, fk_val)
                session.add(new_obs)
            session.commit()
            msg = f"Imported {len(new_rows)} quality issue(s) from {obs_filename}."
            if dup_rows:
                msg += f" Skipped {len(dup_rows)} row(s) already recorded for their source/type/date (likely a repeat click)."
            set_pending_banner("observation_import_msg", msg)
            st.rerun()

st.divider()
st.subheader("Quality issues")

filter_col1, filter_col2 = st.columns([1, 1])
with filter_col1:
    severity_filter = st.multiselect("Severity filter", SEVERITIES, default=SEVERITIES)
with filter_col2:
    # Foam scope - same "All foam grades / Foam grade / Foam family" pattern
    # as the Quality Test Result page's breakdown chart, so both pages let
    # you ask "which issue is most common for this grade/family" the same
    # way. This filter set and the Pareto chart below both read from the
    # same scoped_grade_ids, so the chart always matches the table.
    scoped_grades = (
        apply_scope(session.query(FoamGrade), FoamGrade.id, grade_ids_for_company(session, active_company_id))
        .order_by(FoamGrade.grade_name)
        .all()
    )
    foam_scope_mode = st.radio(
        "Foam scope", ["All foam grades", "Foam grade", "Foam family"], key="qi_foam_scope_mode"
    )
    if foam_scope_mode == "All foam grades" or not scoped_grades:
        scope_grade_ids = None
        scope_label = "all foam grades"
    elif foam_scope_mode == "Foam grade":
        scope_grade = st.selectbox(
            "Foam grade", scoped_grades, format_func=lambda g: g.grade_name, key="qi_foam_scope_grade"
        )
        scope_grade_ids = [scope_grade.id] if scope_grade else []
        scope_label = scope_grade.grade_name if scope_grade else "—"
    else:
        families = sorted({g.product_family for g in scoped_grades if g.product_family}, key=lambda f: f.name)
        if not families:
            st.caption("No foam family available for these grades yet.")
            scope_grade_ids = []
            scope_label = "—"
        else:
            scope_family = st.selectbox(
                "Foam family", families, format_func=lambda f: f.name, key="qi_foam_scope_family"
            )
            scope_grade_ids = [g.id for g in scoped_grades if g.product_family_id == scope_family.id]
            scope_label = scope_family.name

observations_query = session.query(QualityObservation).filter(QualityObservation.severity.in_(severity_filter))
if active_company_id is not None:
    observations_query = observations_query.filter(
        or_(
            QualityObservation.production_run_id.in_(scoped_run_ids or []),
            QualityObservation.customer_trial_id.in_(scoped_ct_ids or []),
            QualityObservation.optimization_trial_id.in_(scoped_ot_ids or []),
        )
    )
all_observations = observations_query.order_by(QualityObservation.observed_at.desc()).all()
# Foam scope applied here in Python (not a SQL join) - a result's foam
# grade is reached through whichever of the three mutually-exclusive
# parents it has - see _obs_foam_grade_id().
observations = (
    [o for o in all_observations if _obs_foam_grade_id(o) in (scope_grade_ids or [])]
    if scope_grade_ids is not None else all_observations
)

if not observations:
    st.info("No quality issues match this filter.")
else:
    obs_rows = []
    for o in observations:
        source_label, source_desc = _obs_source_desc(o)
        obs_rows.append(
            {
                "Issue": o.observation_type,
                "Source": source_label,
                "Parent": source_desc,
                "Severity": o.severity,
                "Frequency": o.frequency,
                "Confidence": o.confidence_level,
                "Observed": o.observed_at,
            }
        )
    st.caption("Click a row to edit (and optionally delete) that quality issue.")
    idx = clickable_table(obs_rows, key="obs_table")
    if idx is not None and idx < len(observations):
        st.session_state["obs_selected_id"] = observations[idx].id
    else:
        st.session_state.pop("obs_selected_id", None)

    # -------------------------------------------------------------------
    # Breakdown by issue - same filtered set as the table above (Severity
    # and foam scope both apply), grouped either by the specific issue type
    # or by its taxonomy category (see quality_issue_taxonomy.py) - e.g.
    # Severity = High + Foam scope = a foam family, grouped by issue type,
    # to see which specific fault is most common for that family; or
    # grouped by category for the coarser "which broad kind of problem"
    # view when individual issue types are too scattered to be actionable.
    st.divider()
    st.subheader("Breakdown by issue")
    group_by = st.radio("Group by", ["Issue type", "Issue category"], key="qi_breakdown_group_by", horizontal=True)
    if group_by == "Issue type":
        breakdown_labels = [o.observation_type for o in observations]
        breakdown_col = "Issue type"
    else:
        breakdown_labels = [
            (quality_issue_taxonomy.lookup(o.observation_type) or {}).get("category")
            or "Other / not yet classified"
            for o in observations
        ]
        breakdown_col = "Issue category"
    st.caption(f"{len(observations)} issue(s) for {scope_label}, using the Severity filter above.")
    breakdown_counts = (
        pd.Series(breakdown_labels, name=breakdown_col)
        .value_counts()
        .rename_axis(breakdown_col)
        .reset_index(name="Count")
    )
    render_pareto_chart(breakdown_counts, category_col=breakdown_col, count_col="Count")

    # -------------------------------------------------------------------
    # Quality Issue Report - exports exactly this selection (Severity +
    # Foam scope filters, Group by choice above), aggregated into a
    # severity/recurring-vs-one-off summary, a confidence-level breakdown,
    # an issues-by-type-or-category breakdown, and a curated table of just
    # the priority issues (High severity and/or Recurring) - not a dump of
    # every row in the table above (the CSV export on that table already
    # covers that). Lives here rather than on the Report page for the same
    # reason as the Quality Test Result report: it needs this comprehensive
    # selection built first, unlike the Report page's single-dropdown reports.
    st.divider()
    st.subheader("Quality Issue Report")
    if set(severity_filter) == set(SEVERITIES):
        severity_label = "All"
    elif severity_filter:
        severity_label = ", ".join(severity_filter)
    else:
        severity_label = "None selected"
    st.caption(f"Severity: {severity_label} · Foam scope: {scope_label} · Grouped by: {breakdown_col}")

    issue_report_data = reports.build_quality_issue_report_data(
        session, [o.id for o in observations],
        {"severity_label": severity_label, "foam_scope_label": scope_label, "group_by_label": breakdown_col},
    )
    ric1, ric2, ric3 = st.columns(3)
    ric1.metric("Issues in selection", issue_report_data["total_issues"])
    ric2.metric("Recurring", issue_report_data["recurring_count"])
    ric3.metric(
        "High severity",
        next((r["Count"] for r in issue_report_data["severity_breakdown"] if r["Severity"] == "High"), 0),
    )
    rdl1, rdl2 = st.columns(2)
    rdl1.download_button(
        "Download PDF", data=reports.render_quality_issue_report_pdf(issue_report_data),
        file_name="quality_issue_report.pdf", mime="application/pdf",
        key="quality_issue_report_pdf",
        on_click=log_export_click, args=("quality_issue_report_pdf",),
        kwargs={"description": f"{severity_label} · {scope_label} · {breakdown_col}"},
    )
    rdl2.download_button(
        "Download Excel", data=reports.render_quality_issue_report_excel(issue_report_data),
        file_name="quality_issue_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="quality_issue_report_excel",
        on_click=log_export_click, args=("quality_issue_report_excel",),
        kwargs={"description": f"{severity_label} · {scope_label} · {breakdown_col}"},
    )

    selected_id = st.session_state.get("obs_selected_id")
    selected = next((o for o in observations if o.id == selected_id), None) or (
        session.query(QualityObservation).filter(QualityObservation.id == selected_id).first()
        if selected_id else None
    )

    if selected:
        st.divider()
        st.subheader(f"Edit: {selected.observation_type}")

        # Source/parent + trial link are rendered OUTSIDE the form, same
        # reasoning as the issue-type picker below: the parent/trial
        # dropdowns' options depend on the source type and parent currently
        # selected, so they need to react immediately rather than waiting
        # for form submit. This also preserves a real gap-fix from before
        # the 3-source rework - which run this issue was recorded against
        # used to be fixed at creation time with no way to correct it
        # later; now the source (Production Run / Customer Trial /
        # Optimization Trial) and its parent can both be corrected too.
        current_source, _ = _obs_source_desc(selected)
        available_edit_sources = [
            s for s in SAMPLE_SOURCE_TYPES
            if (s == "Production Run" and runs)
            or (s == "Customer Trial" and customer_trials)
            or (s == "Optimization Trial" and optimization_trials)
            or s == current_source
        ]
        e_source_type = st.selectbox(
            "Record against *", available_edit_sources,
            index=available_edit_sources.index(current_source) if current_source in available_edit_sources else 0,
            key=f"edit_obs_source_{selected.id}",
        )
        if e_source_type == "Production Run":
            run_options = runs
            run_default = next((i for i, r in enumerate(run_options) if r.id == selected.production_run_id), 0)
            e_parent = st.selectbox(
                "Production run *", run_options, index=run_default,
                format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
                key=f"edit_obs_run_{selected.id}",
            )
        elif e_source_type == "Customer Trial":
            ct_default = next((i for i, t in enumerate(customer_trials) if t.id == selected.customer_trial_id), 0)
            e_parent = st.selectbox(
                "Customer trial *", customer_trials, index=ct_default,
                format_func=lambda t: f"Trial #{t.id} — {t.foam_grade.grade_name} · {t.customer_name} · {t.trial_date or '—'}",
                key=f"edit_obs_ct_{selected.id}",
            )
        else:
            ot_default = next((i for i, t in enumerate(optimization_trials) if t.id == selected.optimization_trial_id), 0)
            e_parent = st.selectbox(
                "Optimization trial *", optimization_trials, index=ot_default,
                format_func=lambda t: (
                    f"Trial #{t.id} — {t.foam_grade.grade_name} · "
                    f"{t.improvement_initiative_reference or '(no reference)'} · {t.trial_date or '—'}"
                ),
                key=f"edit_obs_ot_{selected.id}",
            )

        e_type, e_typical_causes = _issue_type_picker(f"edit_obs_{selected.id}", current_value=selected.observation_type)
        if e_typical_causes:
            st.caption(f"Typical causes/checks: {e_typical_causes}")

        with st.form(f"edit_obs_{selected.id}"):
            st.caption(f"Issue type: **{e_type or '(describe the issue above)'}**")
            ec1, ec2 = st.columns(2)
            e_severity = ec1.selectbox(
                "Severity", SEVERITIES,
                index=SEVERITIES.index(selected.severity) if selected.severity in SEVERITIES else 0,
                key=f"edit_obs_severity_{selected.id}",
            )
            e_frequency = ec2.selectbox(
                "Frequency", ["One-off", "Recurring"],
                index=["One-off", "Recurring"].index(selected.frequency) if selected.frequency in ["One-off", "Recurring"] else 0,
                key=f"edit_obs_frequency_{selected.id}",
            )
            e_location = st.text_input("Location in block", value=selected.location_in_block or "", key=f"edit_obs_location_{selected.id}")
            e_cause = st.text_area("Suspected cause", value=selected.suspected_cause or "", key=f"edit_obs_cause_{selected.id}")
            e_confidence = st.selectbox(
                "Confidence level *", CONFIDENCE_LEVELS,
                index=CONFIDENCE_LEVELS.index(selected.confidence_level) if selected.confidence_level in CONFIDENCE_LEVELS else 2,
                key=f"edit_obs_confidence_{selected.id}",
            )
            e_product_impact = st.text_area("Product impact", value=selected.product_impact or "", key=f"edit_obs_pimpact_{selected.id}")
            e_customer_impact = st.text_area("Customer impact", value=selected.customer_impact or "", key=f"edit_obs_cimpact_{selected.id}")
            e_notes = st.text_area("Notes", value=selected.notes or "", key=f"edit_obs_notes_{selected.id}")
            e_observed_at = st.date_input("Observed on", value=selected.observed_at or dt.date.today(), key=f"edit_obs_observed_{selected.id}")
            if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
                if not e_type:
                    st.error("Issue type is required.")
                elif not e_parent:
                    st.error(f"{e_source_type} is required.")
                else:
                    selected.production_run_id = None
                    selected.customer_trial_id = None
                    selected.optimization_trial_id = None
                    setattr(selected, sample_source_fk_field(e_source_type), e_parent.id)
                    selected.observation_type = e_type
                    selected.severity = e_severity
                    selected.frequency = e_frequency
                    selected.location_in_block = e_location
                    selected.suspected_cause = e_cause
                    selected.confidence_level = e_confidence
                    selected.product_impact = e_product_impact
                    selected.customer_impact = e_customer_impact
                    selected.notes = e_notes
                    selected.observed_at = e_observed_at
                    session.commit()
                    st.success("Quality issue updated.")
                    st.rerun()

        def _do_delete_obs(_session=session, _id=selected.id):
            _session.query(QualityObservation).filter(QualityObservation.id == _id).delete(synchronize_session=False)
            _session.commit()
            st.session_state.pop("obs_selected_id", None)

        if page_usable:
            delete_with_confirm(
                "this quality issue", _do_delete_obs, key_prefix=f"obs_{selected.id}",
                extra_warning="This is a leaf record — deleting it has no other effects.",
            )
        else:
            st.caption("View-only access - deleting is restricted for your role.")

        if st.button("Clear selection", key="clear_obs_selection"):
            st.session_state.pop("obs_selected_id", None)
            st.rerun()

