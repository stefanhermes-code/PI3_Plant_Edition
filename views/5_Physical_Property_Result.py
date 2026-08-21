"""Screen 6: Quality Test Result

Sample capture moved to its own pages on 2026-08-02 - this page had been
doing two jobs (samples and the result itself), which crowded a single
screen with tasks a reviewer does at different points in the workflow.
A lab result is still only comparable if it is tied to where in the block
the sample came from - that context now lives on the sample's own page
(Production Samples / Customer Trials & Samples / Optimization Trials &
Samples, all under Samples & Trials, per source); a result here links
back to a sample by id, same as before. Conditioning history was tracked
here too until 2026-08-04, when the whole conditioning feature was
eliminated per user direction - a result's traceability now stops at
which sample/zone it came from.

Keyed to exactly one of a production run, a customer trial, or an
optimization trial (see db.SAMPLE_SOURCE_TYPES) - the "Record against"
picker below decides which.

Redesigned 2026-08-02 (results browsing/edit make-over): the Add form now
sits behind a collapsed expander, same as the Quality Issue page, so
landing on this page shows the browsable table by default rather than an
always-open form. The old "Results by production run" section - one
bordered container with its own mini-table PER production run, stacked one
after another - is replaced by a single flat, filterable table across
every result (scoped to the active company), with one click-to-select/
edit/delete flow, matching the Quality Issue page's established pattern.
At real data volumes (100+ runs x several properties each) the per-run
version amounted to little more than "every result, one after another" -
a page of many small tables rather than one properly browsable list.
"""

import datetime as dt

import pandas as pd
import streamlit as st
from sqlalchemy import or_

from access_control import can_use_page
from auth import current_user, logout_button, require_login
from db import (
    SAMPLE_SOURCE_TYPES,
    CustomerTrial,
    FoamGrade,
    OptimizationTrial,
    PhysicalPropertyDefinition,
    PhysicalPropertyMethod,
    PhysicalPropertyResult,
    PhysicalPropertyUOM,
    ProductionRun,
    Sample,
    get_session,
    init_db,
    sample_source_fk_field,
)
from helpers import (
    clickable_table,
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
from quality_standards import compute_pass_fail, tolerance_label
import reports
from tenant_scope import (
    apply_scope,
    company_picker,
    customer_trial_ids_for_company,
    grade_ids_for_company,
    optimization_trial_ids_for_company,
    run_ids_for_company,
)

# A result belongs to exactly one parent - a production run, a customer
# trial, or an optimization trial (see db.SAMPLE_SOURCE_TYPES). CSV rows
# carry all three FK columns as optional and must set exactly one.
RESULT_REQUIRED_COLUMNS = ["property_name", "test_method", "unit", "actual_value"]
RESULT_OPTIONAL_COLUMNS = [
    "production_run_id", "customer_trial_id", "optimization_trial_id",
    "target_value", "sample_id", "method_revision",
    "replicate_no", "tested_at", "notes",
]


def _result_source_desc(result):
    """(source label, human-readable parent description) for a
    PhysicalPropertyResult, resolving whichever of the three FKs is set."""
    if result.production_run_id is not None:
        run = result.production_run
        desc = f"Run #{run.id} — {run.foam_grade.grade_name} · {run.run_date}" if run else f"Run #{result.production_run_id}"
        return "Production Run", desc
    if result.customer_trial_id is not None:
        t = result.customer_trial
        desc = f"Trial #{t.id} — {t.customer_name}" if t else f"Trial #{result.customer_trial_id}"
        return "Customer Trial", desc
    if result.optimization_trial_id is not None:
        t = result.optimization_trial
        ref = (t.improvement_initiative_reference or "(no reference)") if t else ""
        desc = f"Trial #{t.id} — {ref}" if t else f"Trial #{result.optimization_trial_id}"
        return "Optimization Trial", desc
    return "—", "—"


def _result_foam_grade_id(result):
    """foam_grade_id reachable from whichever of the three parents this
    result belongs to - used to apply the Foam scope filter/chart across
    all three sources uniformly."""
    if result.production_run_id is not None:
        return result.production_run.foam_grade_id if result.production_run else None
    if result.customer_trial_id is not None:
        return result.customer_trial.foam_grade_id if result.customer_trial else None
    if result.optimization_trial_id is not None:
        return result.optimization_trial.foam_grade_id if result.optimization_trial else None
    return None


def _samples_for_parent(session, source_type, parent_id):
    if not parent_id:
        return []
    fk_field = sample_source_fk_field(source_type)
    return session.query(Sample).filter(getattr(Sample, fk_field) == parent_id).all()

page_setup("Quality Test Result")
init_db()
require_login()
logout_button()

st.title("Quality Test Result")
render_function_action_intro(
    function_text=(
        "Records the lab results that prove out (or flag) a batch against the property/method/"
        "unit master list - density, 40% IFD/hardness, tensile strength, elongation, compression "
        "set, resilience, and so on - each compared to a target value and marked pass or fail. "
        "Link a result to a sample (recorded on its source's own page under Samples & Trials) for "
        "full traceability back to where in the block it was cut."
    ),
    action_text=(
        "Add the sample(s) first, on the relevant Samples & Trials page (Production Samples / "
        "Customer Trials & Samples / Optimization Trials & Samples), if you want results traceable "
        "back to block location. Then pick which of the three you're recording against (Production "
        "Run / Customer Trial / Optimization Trial), the property, "
        "test method, and unit from the master list here, and link the result back to its sample "
        "if one applies. Use the CSV/Excel import tab to bulk-load a batch of results at once "
        "instead of entering them one by one."
    ),
)
session = get_session()
user = current_user()
page_usable = can_use_page("quality_test_result", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()
company, _all_companies = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="qtr_company_filter"
)
active_company_id = company.id if company else None
run_ids = run_ids_for_company(session, active_company_id)
customer_trial_ids = customer_trial_ids_for_company(session, active_company_id)
optimization_trial_ids = optimization_trial_ids_for_company(session, active_company_id)

runs = (
    apply_scope(session.query(ProductionRun), ProductionRun.id, run_ids)
    .order_by(ProductionRun.created_at.desc())
    .all()
)
customer_trials = (
    apply_scope(session.query(CustomerTrial), CustomerTrial.id, customer_trial_ids)
    .order_by(CustomerTrial.created_at.desc())
    .all()
)
optimization_trials = (
    apply_scope(session.query(OptimizationTrial), OptimizationTrial.id, optimization_trial_ids)
    .order_by(OptimizationTrial.created_at.desc())
    .all()
)
# Where each source type is created, so the "Record against" picker below can
# say what to do rather than silently dropping a source the user was told to
# expect (Charlie's CR of 20 Aug 2026 - see the note above the picker).
SOURCE_ORIGIN_PAGE = {
    "Production Run": "Production Run",
    "Customer Trial": "Customer Trials & Samples",
    "Optimization Trial": "Optimization Trials & Samples",
}
records_for_source = {
    "Production Run": runs,
    "Customer Trial": customer_trials,
    "Optimization Trial": optimization_trials,
}

if not runs and not customer_trials and not optimization_trials:
    st.warning(
        "Create a production run, customer trial, or optimization trial first "
        "(Production Run / Customer Trials / Optimization Trials pages)."
    )
    # Deliberately no st.stop() here. A company that has not recorded anything
    # yet reaches this branch on EVERY render, and st.stop() on a path like
    # that leaves Streamlit's stop flag set - which skips close_out_session()
    # in app.py's finally and leaves the read transaction open. See the
    # Streamlit stop-deadlock note of 19 Aug 2026. The rest of the page copes
    # with having nothing to show: the picker explains what to create, and the
    # results list below is simply empty.

# ---------------------------------------------------------------------------
# Physical property results
# ---------------------------------------------------------------------------
st.divider()

property_defs = (
    session.query(PhysicalPropertyDefinition)
    .order_by(PhysicalPropertyDefinition.is_common.desc(), PhysicalPropertyDefinition.sort_order)
    .all()
)
if not property_defs:
    st.warning(
        "The physical property master list has not been loaded yet. Run the migration that seeds "
        "physical_property_definitions/methods/uoms before recording results."
    )

tab_result_manual, tab_result_import = st.tabs(["Add quality test result", "CSV / Excel import"])

with tab_result_manual:
    show_pending_banner("result_manual_msg")
    with st.expander("Add quality test result", expanded=False):
        if not page_usable:
            st.caption("View-only access - adding a quality test result is restricted for your role.")
        else:
            # All three source types are always offered, even when one has no
            # records in scope yet.
            #
            # This list used to be filtered down to the sources that actually
            # had records, which silently removed "Optimization Trial" for any
            # company with no optimization trial recorded - while this page's
            # own Function and Action text still told the user to expect three.
            # Reported as a defect by Charlie on 20 Aug 2026; the code was
            # correct in every other respect (manual entry, sample linkage and
            # the importer all handle optimization trials), and the cause was
            # an empty table. A missing option with no explanation is not a
            # useful answer to a user who was told the option exists, so the
            # option now stays and says what to create instead.
            source_type = st.selectbox(
                "Record against *", list(SAMPLE_SOURCE_TYPES), key="result_source_type",
            )
            source_records = records_for_source.get(source_type) or []
            if not source_records:
                st.info(
                    "No %s has been recorded for this company yet, so there is nothing to record a "
                    "result against. Create one on the %s page first."
                    % (source_type.lower(), SOURCE_ORIGIN_PAGE.get(source_type, source_type))
                )
                parent = None
            elif source_type == "Production Run":
                run = st.selectbox(
                    "Production run *", runs,
                    format_func=lambda r: f"Run #{r.id} — {r.foam_grade.grade_name} · {r.run_date}",
                    key="result_run_select",
                )
                parent = run
            elif source_type == "Customer Trial":
                parent = st.selectbox(
                    "Customer trial *", customer_trials,
                    format_func=lambda t: f"Trial #{t.id} — {t.foam_grade.grade_name} · {t.customer_name} · {t.trial_date or '—'}",
                    key="result_ct_select",
                )
            else:
                parent = st.selectbox(
                    "Optimization trial *", optimization_trials,
                    format_func=lambda t: (
                        f"Trial #{t.id} — {t.foam_grade.grade_name} · "
                        f"{t.improvement_initiative_reference or '(no reference)'} · {t.trial_date or '—'}"
                    ),
                    key="result_ot_select",
                )

            # Only when a source record is actually selected. The message
            # above has already said what to create otherwise.
            if parent is not None:
                samples_for_parent = _samples_for_parent(session, source_type, parent.id)
                # Mandatory for EVERY source, not only production (Charlie's
                # clarification of 21 Aug 2026, which fixed this as the
                # governing rule). A quality test result is a measurement of a
                # physical specimen, so the specimen is part of the record:
                #
                #   Production Run      -> Production Sample (Top/Middle/Bottom)
                #   Customer Trial      -> Customer Trial Sample
                #   Optimization Trial  -> Optimization Trial Sample
                #
                # There is no parent-sample or test-piece layer below this; the
                # sample record IS the specimen. That suggestion was withdrawn.
                _sample_required = True
                if not samples_for_parent:
                    st.warning(
                        "%s has no samples recorded, so a quality result cannot be linked to one. "
                        "Add its sample%s on the %s page first."
                        % (
                            ("Run #%d" % parent.id) if source_type == "Production Run"
                            else ("Trial #%d" % parent.id),
                            "s (Top, Middle and Bottom)" if source_type == "Production Run" else "",
                            SOURCE_ORIGIN_PAGE.get(source_type, source_type),
                        )
                    )
                sample = st.selectbox(
                    "Sample *",
                    list(samples_for_parent),
                    format_func=lambda s: "— not linked to a sample —" if s is None else f"Sample #{s.id} — {s.zone_label}",
                    key="result_sample_select",
                    help=(
                        "Every quality test result is a measurement of a physical specimen, so the "
                        "specimen is part of the record. For a production run that is one of its "
                        "three zone samples."
                    ),
                )
                property_def = st.selectbox(
                    "Property * (⭐ = most commonly tested; full list searchable below)",
                    property_defs,
                    format_func=lambda p: f"⭐ {p.name}" if p.is_common else p.name,
                    key="result_property_select",
                )
                if property_def:
                    st.caption(f"{property_def.what_it_measures} — category: {property_def.category}")

                methods_for_property = (
                    session.query(PhysicalPropertyMethod)
                    .filter(PhysicalPropertyMethod.property_definition_id == property_def.id)
                    .order_by(PhysicalPropertyMethod.sort_order)
                    .all()
                    if property_def
                    else []
                )
                uoms_for_property = (
                    session.query(PhysicalPropertyUOM)
                    .filter(PhysicalPropertyUOM.property_definition_id == property_def.id)
                    .order_by(PhysicalPropertyUOM.sort_order)
                    .all()
                    if property_def
                    else []
                )

                with st.form("add_property_result"):
                    c1, c2 = st.columns(2)
                    method_choice = c1.selectbox(
                        "Measuring method *",
                        methods_for_property,
                        format_func=lambda m: m.method_code,
                    )
                    method_other = c1.text_input("Or type a method not listed above")
                    uom_choice = c2.selectbox(
                        "Unit of measure *",
                        uoms_for_property,
                        format_func=lambda u: u.unit_label,
                    )
                    uom_other = c2.text_input("Or type a unit not listed above")

                    c3, c4, c5 = st.columns(3)
                    target_value = c3.number_input("Target value", step=0.1)
                    actual_value = c4.number_input("Actual value", step=0.1)
                    method_revision = c5.text_input("Method edition / revision (e.g. 2017)")
                    if property_def:
                        st.caption(f"Industry accepted tolerance for {property_def.name}: {tolerance_label(property_def.name, target_value)}")
                    replicate_no = st.number_input(
                        "Replicate no.", min_value=1, step=1, value=1,
                        help=(
                            "Which repeat this is when the same property is tested more than once on "
                            "the same sample (e.g. running tensile strength 3 times for a reliable "
                            "average) - use 1 for the first/only measurement, 2 for the second, and so "
                            "on. Leave at 1 if you're only testing this property once per sample."
                        ),
                    )
                    tested_at = st.date_input("Tested on", value=dt.date.today())
                    notes = st.text_area("Notes (e.g. specimen geometry, orientation, deflection, temperature)")
                    submitted = st.form_submit_button("Save result")
                    if submitted:
                        final_method = method_other.strip() or (method_choice.method_code if method_choice else "")
                        final_unit = uom_other.strip() or (uom_choice.unit_label if uom_choice else "")
                        if sample is None:
                            st.error(
                                "A quality test result must be linked to the sample it was "
                                "measured on. Record the sample first if it does not exist yet."
                            )
                        elif not property_def:
                            st.error("Select a property.")
                        elif not final_method:
                            st.error("A measuring method is required — pick one or type a custom one.")
                        else:
                            pass_fail = compute_pass_fail(property_def.name, target_value, actual_value)
                            new_result = PhysicalPropertyResult(
                                sample_id=sample.id if sample else None,
                                property_definition_id=property_def.id,
                                property_method_id=method_choice.id if (method_choice and not method_other.strip()) else None,
                                property_name=property_def.name,
                                target_value=target_value or None,
                                actual_value=actual_value or None,
                                unit=final_unit,
                                pass_fail=pass_fail,
                                test_method=final_method,
                                method_revision=method_revision,
                                replicate_no=int(replicate_no),
                                tested_at=tested_at,
                                notes=notes,
                            )
                            setattr(new_result, sample_source_fk_field(source_type), parent.id)
                            session.add(new_result)
                            session.commit()
                            # Stashed, not shown directly: st.rerun() below restarts the
                            # script and wipes an st.success() written just before it, so
                            # a successful save looked like nothing happened. See
                            # helpers.show_pending_banner.
                            set_pending_banner("result_manual_msg", "Quality test result saved.")
                            st.rerun()

with tab_result_import:
    show_pending_banner("result_import_msg")
    st.caption(
        "property_name must match a name in the physical property master list (case-insensitive). "
        "test_method and unit are stored as typed — they don't need to match an existing method/UOM. "
        "Every row must carry a sample_id belonging to the parent it names - a quality test "
        "result is a measurement of a physical specimen, so the specimen is part of the record. "
        "Each row needs exactly one of production_run_id / customer_trial_id / optimization_trial_id "
        "set, matching which of the three that result belongs to."
    )
    result_df, result_filename = csv_excel_uploader(
        RESULT_REQUIRED_COLUMNS, RESULT_OPTIONAL_COLUMNS, key="result_upload"
    )
    if result_df is not None:
        defs_by_name = {p.name.strip().lower(): p for p in property_defs}
        import_run_ids = {r.id for r in runs}
        import_ct_ids = {t.id for t in customer_trials}
        import_ot_ids = {t.id for t in optimization_trials}
        # Scoped to this company's parents - otherwise a CSV row could
        # attach a new result to a different company's sample (the
        # parent-id check alone doesn't catch that, since sample_id is an
        # independent column).
        samples_all = {
            s.id: s for s in session.query(Sample).filter(
                or_(
                    Sample.production_run_id.in_(import_run_ids),
                    Sample.customer_trial_id.in_(import_ct_ids),
                    Sample.optimization_trial_id.in_(import_ot_ids),
                )
            ).all()
        }
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
        for _, row in result_df.iterrows():
            try:
                prop_def = defs_by_name.get(str(row.get("property_name", "")).strip().lower())
                fk_field, _fk_val = _row_fk(row)
                sample_val = row.get("sample_id")
                sample_given = not pd.isna(sample_val) and str(sample_val).strip() != ""
                sample_ok = (not sample_given) or int(sample_val) in samples_all
                # A production row must carry a sample, and that sample must
                # belong to the run the row names. The second half matters as
                # much as the first: a sample_id that exists is not the same as
                # a sample_id that belongs here, and a spreadsheet is exactly
                # where a stale id gets copied from one run to another.
                # Required on every row, and the sample must belong to the
                # parent the row names. The second half matters as much as the
                # first: a sample_id that exists is not the same as one that
                # belongs here, and a spreadsheet is exactly where a stale id
                # gets copied from one parent to another.
                if not sample_given:
                    sample_ok = False
                elif sample_ok:
                    linked = samples_all.get(int(sample_val))
                    sample_ok = (
                        linked is not None
                        and getattr(linked, fk_field, None) == _fk_val
                    )
                has_method_unit_value = (
                    str(row.get("test_method", "")).strip()
                    and str(row.get("unit", "")).strip()
                    and not pd.isna(row.get("actual_value"))
                )
                ok = bool(prop_def and fk_field and sample_ok and has_method_unit_value)
            except (TypeError, ValueError):
                ok = False
            if ok:
                good_rows.append(row)
            else:
                bad_rows.append(row)

        st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged/rejected: **{len(bad_rows)}**")
        if bad_rows:
            st.warning(
                "Flagged rows have an unrecognized property_name, don't have exactly one in-scope "
                "production_run_id / customer_trial_id / optimization_trial_id set, an unrecognized "
                "sample_id, or are missing test_method / unit / actual_value. EVERY row must carry a "
                "sample_id, and that sample must belong to the parent the row names."
            )
            render_data_table(pd.DataFrame(bad_rows), max_height="300px")

        if good_rows and st.button("Confirm import", key="confirm_result_import", disabled=not page_usable):
            existing_keys = set()
            for r in session.query(PhysicalPropertyResult).all():
                src_label, _ = _result_source_desc(r)
                fk_col = sample_source_fk_field(src_label) if src_label in SAMPLE_SOURCE_TYPES else None
                if fk_col:
                    existing_keys.add((fk_col, getattr(r, fk_col), r.property_definition_id, r.sample_id, r.replicate_no))

            def _result_key(row):
                prop_def = defs_by_name[str(row["property_name"]).strip().lower()]
                fk_field, fk_val = _row_fk(row)
                sample_val = row.get("sample_id")
                replicate_val = row.get("replicate_no")
                return (
                    fk_field,
                    fk_val,
                    prop_def.id,
                    int(sample_val) if not pd.isna(sample_val) else None,
                    int(replicate_val) if not pd.isna(replicate_val) else 1,
                )

            new_rows, dup_rows = dedupe_import_rows(good_rows, existing_keys, key_func=_result_key)

            for row in new_rows:
                prop_def = defs_by_name[str(row["property_name"]).strip().lower()]
                test_method = str(row["test_method"]).strip()
                method_match = next(
                    (
                        m
                        for m in session.query(PhysicalPropertyMethod)
                        .filter(PhysicalPropertyMethod.property_definition_id == prop_def.id)
                        .all()
                        if m.method_code.strip().lower() == test_method.lower()
                    ),
                    None,
                )
                target_val = row.get("target_value")
                actual_val = row.get("actual_value")
                pass_fail = (
                    compute_pass_fail(prop_def.name, target_val, actual_val)
                    if not pd.isna(target_val) and not pd.isna(actual_val)
                    else None
                )
                sample_val = row.get("sample_id")
                replicate_val = row.get("replicate_no")
                tested_val = pd.to_datetime(row.get("tested_at"), errors="coerce")
                fk_field, fk_val = _row_fk(row)
                new_result = PhysicalPropertyResult(
                    sample_id=int(sample_val) if not pd.isna(sample_val) else None,
                    property_definition_id=prop_def.id,
                    property_method_id=method_match.id if method_match else None,
                    property_name=prop_def.name,
                    target_value=target_val if not pd.isna(target_val) else None,
                    actual_value=actual_val if not pd.isna(actual_val) else None,
                    unit=str(row["unit"]).strip(),
                    pass_fail=pass_fail,
                    test_method=test_method,
                    method_revision=str(row.get("method_revision", "") or ""),
                    replicate_no=int(replicate_val) if not pd.isna(replicate_val) else 1,
                    tested_at=tested_val.date() if not pd.isna(tested_val) else dt.date.today(),
                    notes=str(row.get("notes", "") or ""),
                )
                setattr(new_result, fk_field, fk_val)
                session.add(new_result)
            session.commit()
            msg = f"Imported {len(new_rows)} quality test result(s) from {result_filename}."
            if dup_rows:
                msg += f" Skipped {len(dup_rows)} row(s) already recorded for their source/property/sample (likely a repeat click)."
            set_pending_banner("result_import_msg", msg)
            st.rerun()

# ---------------------------------------------------------------------------
# Browse / edit / delete - one flat, filterable table across every result
# (scoped to the active company), not one mini-table per production run.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Quality test results")

filter_col1, filter_col2, filter_col3 = st.columns([1, 1, 1])
with filter_col1:
    pass_fail_options = ["Pass", "Fail", "Not computed"]
    pass_fail_filter = st.multiselect("Pass/Fail filter", pass_fail_options, default=pass_fail_options)
with filter_col2:
    property_name_options = sorted({p.name for p in property_defs}) if property_defs else []
    property_filter = (
        st.multiselect("Property filter", property_name_options, default=property_name_options)
        if property_name_options
        else None
    )
with filter_col3:
    # Foam scope - "All foam grades" pools the whole company's results (the
    # common case: which properties fail most, overall), while Foam
    # grade/family narrows to one grade or a whole family (e.g. "Comfort
    # foams that failed") - both this filter set and the Pareto chart below
    # read from the same scoped_grade_ids, so the chart always matches
    # exactly what the table above it shows.
    scoped_grades = (
        apply_scope(session.query(FoamGrade), FoamGrade.id, grade_ids_for_company(session, active_company_id))
        .order_by(FoamGrade.grade_name)
        .all()
    )
    foam_scope_mode = st.radio(
        "Foam scope", ["All foam grades", "Foam grade", "Foam family"], key="qtr_foam_scope_mode"
    )
    if foam_scope_mode == "All foam grades" or not scoped_grades:
        scope_grade_ids = None
        scope_label = "all foam grades"
    elif foam_scope_mode == "Foam grade":
        scope_grade = st.selectbox(
            "Foam grade", scoped_grades, format_func=lambda g: g.grade_name, key="qtr_foam_scope_grade"
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
                "Foam family", families, format_func=lambda f: f.name, key="qtr_foam_scope_family"
            )
            scope_grade_ids = [g.id for g in scoped_grades if g.product_family_id == scope_family.id]
            scope_label = scope_family.name

results_query = session.query(PhysicalPropertyResult)
if active_company_id is not None:
    results_query = results_query.filter(
        or_(
            PhysicalPropertyResult.production_run_id.in_(run_ids or []),
            PhysicalPropertyResult.customer_trial_id.in_(customer_trial_ids or []),
            PhysicalPropertyResult.optimization_trial_id.in_(optimization_trial_ids or []),
        )
    )
if property_filter is not None:
    results_query = results_query.filter(PhysicalPropertyResult.property_name.in_(property_filter))
all_results = results_query.order_by(PhysicalPropertyResult.tested_at.desc()).all()

# Pass/Fail is recomputed live rather than trusted from the stored column -
# see the same note in analytics.property_results_dataframe - so this
# filter is applied here in Python against the live verdict, not as a SQL
# WHERE clause against the stored (possibly stale) column. Foam scope is
# applied here too (rather than a SQL join) since a result's foam grade is
# reached through whichever of the three mutually-exclusive parents it
# has - see _result_foam_grade_id().
filtered_results = []
for r in all_results:
    if scope_grade_ids is not None and _result_foam_grade_id(r) not in (scope_grade_ids or []):
        continue
    live_pass_fail = compute_pass_fail(r.property_name, r.target_value, r.actual_value)
    if (live_pass_fail or "Not computed") in pass_fail_filter:
        filtered_results.append((r, live_pass_fail))

if not filtered_results:
    st.info("No quality test results match this filter.")
else:
    result_rows = []
    for r, live_pass_fail in filtered_results:
        source_label, source_desc = _result_source_desc(r)
        result_rows.append(
            {
                "Source": source_label,
                "Parent": source_desc,
                "Property": r.property_name,
                "Target": r.target_value,
                "Actual": r.actual_value,
                "Unit": r.unit,
                "Pass/Fail": live_pass_fail or "—",
                "Sample": f"#{r.sample_id} ({r.sample.zone_label})" if r.sample else "—",
                "Method": r.test_method,
                "Rev.": r.method_revision,
                "Replicate": r.replicate_no,
                "Tested": r.tested_at,
                "Notes": r.notes,
            }
        )
    st.caption(f"{len(filtered_results)} result(s). Click a row to edit (and optionally delete) that result.")
    idx = clickable_table(result_rows, key="results_table")
    if idx is not None and idx < len(filtered_results):
        st.session_state["result_selected_id"] = filtered_results[idx][0].id
    else:
        st.session_state.pop("result_selected_id", None)

    # -----------------------------------------------------------------------
    # Breakdown by property - same filtered set as the table above (Pass/
    # Fail, Property, and foam scope filters all apply), grouped by property
    # instead of listed row by row. Answers "which properties are behind
    # most of these results" at a glance - e.g. Pass/Fail = Fail, Foam scope
    # = a foam family, to see which property fails most often for that
    # family - rather than scrolling the raw table counting rows by eye.
    st.divider()
    st.subheader("Breakdown by property")
    st.caption(
        f"{len(filtered_results)} result(s) for {scope_label}, using the Pass/Fail and Property "
        "filters above."
    )
    property_counts = (
        pd.Series([r.property_name for r, _ in filtered_results], name="Property")
        .value_counts()
        .rename_axis("Property")
        .reset_index(name="Count")
    )
    render_pareto_chart(property_counts, category_col="Property", count_col="Count")

    # -------------------------------------------------------------------
    # Quality Test Result Report - exports exactly this selection (Pass/
    # Fail + Property + Foam scope filters above), aggregated into a
    # pass-rate summary, failure breakdown charts, and a curated table of
    # just the failing results - not a dump of every row in the table
    # above (the CSV export on that table already covers that). This
    # report lives here rather than on the Report page because it needs
    # this comprehensive selection built first, unlike the Report page's
    # other reports which are each a single dropdown choice.
    st.divider()
    st.subheader("Quality Test Result Report")
    if set(pass_fail_filter) == set(pass_fail_options):
        pass_fail_label = "All"
    elif pass_fail_filter:
        pass_fail_label = ", ".join(pass_fail_filter)
    else:
        pass_fail_label = "None selected"
    if property_filter is None or (property_name_options and set(property_filter) == set(property_name_options)):
        property_label = "All properties"
    elif not property_filter:
        property_label = "None selected"
    elif len(property_filter) <= 5:
        property_label = ", ".join(property_filter)
    else:
        property_label = f"{len(property_filter)} of {len(property_name_options)} properties"
    st.caption(f"Pass/Fail: {pass_fail_label} · Property: {property_label} · Foam scope: {scope_label}")

    report_data = reports.build_quality_test_report_data(
        session, [r.id for r, _ in filtered_results],
        {"pass_fail_label": pass_fail_label, "property_label": property_label, "foam_scope_label": scope_label},
    )
    rc1, rc2, rc3 = st.columns(3)
    rc1.metric("Results in selection", report_data["total_results"])
    rc2.metric("Pass rate", f"{report_data['pass_rate']}%" if report_data["pass_rate"] is not None else "—")
    rc3.metric("Failing results", report_data["fail_count"])
    st.download_button(
        "Download Word", data=reports.render_quality_test_report_docx(report_data),
        file_name="quality_test_result_report.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key="quality_test_report_docx",
        on_click=log_export_click, args=("quality_test_report_docx",),
        kwargs={"description": f"{pass_fail_label} · {property_label} · {scope_label}"},
    )

selected_result_id = st.session_state.get("result_selected_id")
selected_result = (
    session.query(PhysicalPropertyResult).filter(PhysicalPropertyResult.id == selected_result_id).first()
    if selected_result_id else None
)

if selected_result:
    st.divider()
    edit_source_label, edit_source_desc = _result_source_desc(selected_result)
    st.subheader(f"Edit quality test result #{selected_result.id}")
    st.caption(f"{edit_source_label}: {edit_source_desc} — which run/trial this belongs to can't be changed here.")
    # Same controlled method/UOM pickers as the Add form above, scoped to
    # this result's own property - previously this edit form used a free
    # text_input for both fields, which lost the structured picker the Add
    # form offers (see PI3_Gaps_and_Ambiguities.docx, findings 2.5/2.6).
    methods_for_edit = (
        session.query(PhysicalPropertyMethod)
        .filter(PhysicalPropertyMethod.property_definition_id == selected_result.property_definition_id)
        .order_by(PhysicalPropertyMethod.sort_order)
        .all()
        if selected_result.property_definition_id
        else []
    )
    uoms_for_edit = (
        session.query(PhysicalPropertyUOM)
        .filter(PhysicalPropertyUOM.property_definition_id == selected_result.property_definition_id)
        .order_by(PhysicalPropertyUOM.sort_order)
        .all()
        if selected_result.property_definition_id
        else []
    )
    method_match_idx = next(
        (i for i, m in enumerate(methods_for_edit) if m.method_code == selected_result.test_method), None
    )
    uom_match_idx = next(
        (i for i, u in enumerate(uoms_for_edit) if u.unit_label == selected_result.unit), None
    )
    with st.form(f"edit_result_{selected_result.id}"):
        samples_for_edit = _samples_for_parent(
            session, edit_source_label,
            selected_result.production_run_id or selected_result.customer_trial_id or selected_result.optimization_trial_id,
        ) if edit_source_label in SAMPLE_SOURCE_TYPES else []
        sample_options = [None] + samples_for_edit
        sample_default = next((i for i, s in enumerate(sample_options) if s and s.id == selected_result.sample_id), 0)
        e_sample = st.selectbox(
            "Sample (optional)" if selected_result.sample_id is None else "Sample *",
            sample_options, index=sample_default,
            format_func=lambda s: "— not linked to a sample —" if s is None else f"Sample #{s.id} — {s.zone_label}",
            key=f"edit_result_sample_{selected_result.id}",
        )
        if selected_result.production_run_id is not None and selected_result.sample_id is None:
            # Deliberately still saveable without a sample. This result predates
            # the rule and the zone it was measured on is not recorded anywhere,
            # so forcing one here would make somebody invent it - the exact
            # inference the instruction says to avoid. It can be linked by
            # anyone who knows the answer, and left alone by anyone who does not.
            st.caption(
                "This result was recorded before a sample link was required, and the zone it came "
                "from is not held. Link it if you know which sample it belongs to; leave it "
                "unlinked otherwise."
            )
        ec1, ec2 = st.columns(2)
        e_target = ec1.number_input(
            "Target value", step=0.1, value=float(selected_result.target_value or 0.0), key=f"edit_result_target_{selected_result.id}"
        )
        e_actual = ec2.number_input(
            "Actual value", step=0.1, value=float(selected_result.actual_value or 0.0), key=f"edit_result_actual_{selected_result.id}"
        )
        st.caption(
            f"Industry accepted tolerance for {selected_result.property_name}: "
            f"{tolerance_label(selected_result.property_name, selected_result.target_value)}"
        )

        emc1, emc2 = st.columns(2)
        if methods_for_edit:
            e_method_choice = emc1.selectbox(
                "Measuring method", methods_for_edit, index=method_match_idx or 0,
                format_func=lambda m: m.method_code, key=f"edit_result_method_select_{selected_result.id}",
            )
        else:
            e_method_choice = None
        e_method_other = emc1.text_input(
            "Or type a method not listed above",
            value=(selected_result.test_method or "") if method_match_idx is None else "",
            key=f"edit_result_method_other_{selected_result.id}",
        )
        if uoms_for_edit:
            e_uom_choice = emc2.selectbox(
                "Unit of measure", uoms_for_edit, index=uom_match_idx or 0,
                format_func=lambda u: u.unit_label, key=f"edit_result_uom_select_{selected_result.id}",
            )
        else:
            e_uom_choice = None
        e_uom_other = emc2.text_input(
            "Or type a unit not listed above",
            value=(selected_result.unit or "") if uom_match_idx is None else "",
            key=f"edit_result_uom_other_{selected_result.id}",
        )
        e_revision = st.text_input(
            "Method edition / revision", value=selected_result.method_revision or "", key=f"edit_result_rev_{selected_result.id}"
        )
        e_replicate = st.number_input(
            "Replicate no.", min_value=1, step=1, value=selected_result.replicate_no or 1,
            key=f"edit_result_replicate_{selected_result.id}",
            help=(
                "Which repeat this is when the same property is tested more than once on the same "
                "sample (e.g. running tensile strength 3 times for a reliable average) - use 1 for "
                "the first/only measurement, 2 for the second, and so on."
            ),
        )
        e_tested_at = st.date_input(
            "Tested on", value=selected_result.tested_at or dt.date.today(), key=f"edit_result_tested_{selected_result.id}"
        )
        e_notes = st.text_area("Notes", value=selected_result.notes or "", key=f"edit_result_notes_{selected_result.id}")
        if st.form_submit_button("Save changes", disabled=not page_usable) and page_usable:
            e_method = e_method_other.strip() or (e_method_choice.method_code if e_method_choice else "")
            e_unit = e_uom_other.strip() or (e_uom_choice.unit_label if e_uom_choice else "")
            if not e_method:
                st.error("A measuring method is required.")
            else:
                pass_fail = compute_pass_fail(selected_result.property_name, e_target, e_actual)
                selected_result.sample_id = e_sample.id if e_sample else None
                selected_result.target_value = e_target or None
                selected_result.actual_value = e_actual or None
                selected_result.unit = e_unit
                selected_result.pass_fail = pass_fail
                selected_result.test_method = e_method
                selected_result.property_method_id = (
                    e_method_choice.id if (e_method_choice and not e_method_other.strip()) else None
                )
                selected_result.method_revision = e_revision
                selected_result.replicate_no = int(e_replicate)
                selected_result.tested_at = e_tested_at
                selected_result.notes = e_notes
                session.commit()
                st.success("Quality test result updated.")
                st.rerun()

    def _do_delete_result(_session=session, _id=selected_result.id):
        _session.query(PhysicalPropertyResult).filter(PhysicalPropertyResult.id == _id).delete(synchronize_session=False)
        _session.commit()
        st.session_state.pop("result_selected_id", None)

    if page_usable:
        delete_with_confirm(
            f"result #{selected_result.id}", _do_delete_result, key_prefix=f"result_{selected_result.id}",
            extra_warning="This is a leaf record — deleting it has no other effects.",
        )
    else:
        st.caption("View-only access - deleting is restricted for your role.")

    if st.button("Clear selection", key="clear_result_selection"):
        st.session_state.pop("result_selected_id", None)
        st.rerun()
