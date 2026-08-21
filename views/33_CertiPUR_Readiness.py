"""Screen: CertiPUR Readiness

A pre-audit of one foam grade against the CertiPUR requirements, from the
evidence the plant already holds - its recipe, its raw materials and their
supplier documents.

WHAT THIS PAGE CAN AND CANNOT SAY
Europur approved PI3 doing this on 20 August 2026. Michel's own words for what
it produces: whether the foam "in principle complies with the criteria of
CertiPUR". That boundary is not a disclaimer bolted on at the end - it is the
structure of the requirements themselves, which set criteria in two kinds:

  - measurable upper limits on FINISHED FOAM, determined only by one of the two
    accredited laboratories CertiPUR names; and
  - prohibited substances, which the applicant DECLARES are not used.

The declared half is answerable from plant data and is what this page assesses.
The measured half is reported as Testing required, always, with the limit and
the method stated so the customer knows what they are buying before they buy
it. See certipur_assessment.py for why no indicative reading is offered.

Availability: Company.certipur_enabled, which is also what makes a safety data
sheet mandatory on a new raw material for that company. Deliberately an
explicit opt-in rather than a Function Availability row - see the note on that
column in db.py.
"""

import datetime as dt

import streamlit as st

import audit_log
import certipur_assessment as ca
import regulatory_import as ri
import regulatory_reference as rr
import certipur_criteria as cc
import document_store
import reports
from access_control import can_use_page
from auth import current_user, logout_button, require_login
from db import (
    DOCUMENT_TYPE_APPLICANT_DECLARATION,
    RegulatoryReferenceRecord,
    RegulatoryReferenceSet,
    CertipurAssessment,
    Company,
    FoamGrade,
    Plant,
    get_session,
    init_db,
)
from helpers import (
    page_setup,
    render_function_action_intro,
    set_pending_banner,
    show_pending_banner,
    view_only_notice,
)
from tenant_scope import apply_scope, company_picker, grade_ids_for_company

_STATUS_ICON = {
    ca.STATUS_MEETS: "🟢",
    ca.STATUS_POTENTIAL: "🔴",
    ca.STATUS_MISSING: "🟠",
    ca.STATUS_TESTING: "🔬",
    ca.STATUS_NA: "⚪",
}

page_setup("CertiPUR Readiness")
init_db()
require_login()
logout_button()

st.title("CertiPUR Readiness")
render_function_action_intro(
    function_text=(
        "Checks a foam grade against the CertiPUR requirements using the evidence already held "
        "in PI3 - the active recipe, the raw materials behind it, and their safety data sheets "
        "and supplier declarations. It indicates whether the foam in principle complies, and "
        "lists what is missing before a formal application."
    ),
    action_steps=[
        "Pick the foam grade. Its active recipe version and CertiPUR foam family are shown.",
        "Read the results. Anything red or amber has a stated reason and an action.",
        "Close the gaps - usually a missing safety data sheet or supplier declaration - and run "
        "it again to see the change.",
        "Save the pre-audit to keep it as a dated record, and download the report.",
    ],
    action_note=(
        "The four criteria marked for testing are limits on finished foam and can only be "
        "determined by an accredited laboratory. They are listed with their limits and methods "
        "so the scope of the formal test is clear, and they stay listed however good the rest "
        "of the result is."
    ),
)

session = get_session()
user = current_user()
page_usable = can_use_page(
    "certipur_readiness", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"]
)
if not page_usable:
    view_only_notice("saving a pre-audit")
show_pending_banner("certipur_banner")

company, _all = company_picker(
    st, session, user["is_platform_owner"], user["company_id"], key="certipur_company"
)

if company is None:
    st.info("Pick a single company to assess.")
elif not company.certipur_enabled:
    # Reachable for the platform owner, who sees every page. A customer without
    # the add-on never has this page in their navigation at all.
    st.info(
        "%s does not have CertiPUR Readiness. Switch it on for them on the Companies page - it "
        "also makes a safety data sheet mandatory on every raw material they add from that "
        "point on, which is what the assessment reads." % company.name
    )
else:
    criteria_set = ca.ensure_criteria_set(session)
    session.commit()

    # --- the applicant's own declaration ---------------------------------
    # Section 6 of the CertiPUR application form: the legal entity declares
    # that the prohibited substances are not intentionally added. It is signed
    # once and covers every foam family applied for, so it belongs to the
    # company rather than to a grade or a raw material.
    #
    # It is not decoration. Five criteria are satisfied BY this declaration,
    # with PI3's screening supporting it - so without it on record those five
    # cannot read as met however clean the screen is.
    # --- the controlled regulatory reference library (2026-08-21) ----------
    # Platform owner only. This is not customer data: it is the published
    # regulatory reference PI3 looks substances up in, and it is loaded from
    # the official file rather than typed or transcribed. Sitting on this page
    # because this is where its absence is reported - criterion 3.4 cannot
    # return a positive conclusion while the harmonised classification
    # reference is not loaded.
    if user["is_platform_owner"]:
        _refs = [
            (rr.REFERENCE_HARMONISED_CLP, "Annex VI to CLP", "xlsx",
             "Criterion 3.4, second limb. Without it 3.4 cannot read Meets requirement."),
            (rr.REFERENCE_RESTRICTED_AZO, "REACH Entry 43 appendices", "csv",
             "Criterion 3.2, the CAS identity step. Its absence adds no finding and removes none."),
        ]
        _loaded = {t: rr.reference_state(session, t) for t, _l, _e, _w in _refs}
        with st.expander(
            "Controlled regulatory reference library — %d of %d loaded"
            % (sum(1 for t in _loaded if _loaded[t][0]), len(_refs)),
            expanded=not all(_loaded[t][0] for t in _loaded),
        ):
            st.caption(
                "The published regulatory data PI3 resolves a CAS number against. Loaded from "
                "the official file, versioned and hashed, with the date somebody confirmed the "
                "source. PI3 does not decide whether a file is current; it records who said so "
                "and when, and every conclusion drawn from it names the version it used."
            )
            for _type, _label, _ext, _why in _refs:
                _ok, _state = _loaded[_type]
                st.markdown("**%s** — %s" % (_label, _state))
                st.caption("%s  ·  Source: %s" % (_why, rr.REFERENCE_SOURCES[_type]))
                if _type == rr.REFERENCE_RESTRICTED_AZO:
                    _app = st.selectbox(
                        "Which appendix", (ri.APPENDIX_8, ri.APPENDIX_9),
                        key="ref_app_%s" % _type,
                    )
                else:
                    _app = None
                _up = st.file_uploader(
                    "Official file (%s)" % _ext, type=[_ext], key="ref_up_%s" % _type
                )
                _checked = st.date_input(
                    "Date you confirmed this is the current official file",
                    value=dt.date.today(), key="ref_date_%s" % _type,
                )
                if _up is not None and st.button("Load %s" % _label, key="ref_go_%s" % _type):
                    try:
                        _up.seek(0)
                        _raw = _up.read()
                        if _type == rr.REFERENCE_HARMONISED_CLP:
                            _records, _meta = ri.parse_annex_vi(_raw)
                        else:
                            _records, _meta = ri.parse_entry_43(_raw, _app)
                        _added = rr.load_reference(
                            session, _type, _records, _meta, raw_bytes=_raw,
                            original_file_name=_up.name, source_checked_date=_checked,
                            loaded_by=user.get("display_name") or user.get("username"),
                        )
                        session.commit()
                        set_pending_banner(
                            "certipur_msg",
                            "%s loaded: %d records from %s, active as version %s. Any earlier "
                            "version is superseded and the assessments that used it are "
                            "unchanged." % (_label, _added.record_count, _up.name, _added.version),
                        )
                        st.rerun()
                    except ri.ImportRejected as exc:
                        # The whole point of the signature check: say what was
                        # wrong with the file, and store nothing.
                        st.error(str(exc))
                    except Exception:
                        session.rollback()
                        st.error(
                            "The file could not be loaded. Nothing has been stored. Ask your "
                            "administrator to check the error log."
                        )
                st.divider()

    _declaration = document_store.current_company_document(
        session, company.id, DOCUMENT_TYPE_APPLICANT_DECLARATION
    )
    with st.expander(
        "CertiPUR applicant declaration — %s"
        % ("on record" if _declaration is not None else "not recorded"),
        expanded=_declaration is None,
    ):
        st.caption(
            "The declaration signed on section 6 of the CertiPUR application form, that the "
            "prohibited substances in section 3 of the technical requirements are not "
            "intentionally added. Five criteria are satisfied by this declaration; PI3's "
            "screening of recipes, compositions and supplier documents supports it rather than "
            "replacing it."
        )
        if _declaration is not None:
            st.success(
                "Held: %s%s%s"
                % (
                    _declaration.file_name or "(no file name)",
                    (" · signed by %s" % _declaration.signed_by) if _declaration.signed_by else "",
                    (" · %s" % _declaration.document_date.isoformat())
                    if _declaration.document_date else "",
                )
            )
            if _declaration.file_bytes:
                st.download_button(
                    "Download the declaration on record",
                    data=bytes(_declaration.file_bytes),
                    file_name=_declaration.file_name or "applicant_declaration.pdf",
                    mime=_declaration.content_type or "application/pdf",
                    key="certipur_decl_dl",
                )
        else:
            st.warning(
                "Not recorded. The five declaration-backed criteria will report as an evidence "
                "gap until it is."
            )
        if page_usable:
            dc1, dc2 = st.columns(2)
            decl_signed_by = dc1.text_input("Signed by", key="certipur_decl_by")
            decl_date = dc2.date_input("Date signed", value=None, key="certipur_decl_date")
            decl_file = st.file_uploader(
                "Signed declaration (PDF)", type=["pdf"], key="certipur_decl_file"
            )
            if decl_file is not None and st.button("Record the declaration", key="certipur_decl_save"):
                try:
                    decl_file.seek(0)
                    raw = decl_file.read()
                    decl_file.seek(0)
                    document_store.store_company_document(
                        session, company, raw, decl_file.name,
                        DOCUMENT_TYPE_APPLICANT_DECLARATION,
                        uploaded_by=user.get("display_name") or user.get("username"),
                        extracted_text=None,
                        signed_by=decl_signed_by.strip() or None,
                        document_date=decl_date,
                    )
                    session.commit()
                    set_pending_banner(
                        "certipur_banner",
                        "CertiPUR applicant declaration recorded for %s." % company.name,
                    )
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    session.rollback()
                    st.error("The declaration could not be stored. Try again.")

    grade_ids = grade_ids_for_company(session, company.id)
    grades = (
        apply_scope(session.query(FoamGrade), FoamGrade.id, grade_ids)
        .order_by(FoamGrade.grade_name)
        .all()
    )

    if not grades:
        st.warning("No foam grade has been recorded for %s yet." % company.name)
    else:
        grade = st.selectbox(
            "Foam grade", grades, format_func=lambda g: g.grade_name, key="certipur_grade"
        )

        if grade is not None:
            if not grade.certipur_foam_family:
                # Not blocking. The applicability rules that would use it are
                # not family-specific in the 2026 edition, so an assessment is
                # still worth running - but the application form asks for a
                # family and the report has to be able to state one.
                st.warning(
                    "%s has no CertiPUR foam family set. CertiPUR is applied for per family, so "
                    "set it on Product Family & Foam Grade before a formal application."
                    % grade.grade_name
                )

            outcome = ca.assess(session, grade, criteria_set, company=company)
            resolved = outcome["resolved"]

            if outcome["blocking"]:
                st.error(outcome["blocking"])
            else:
                st.divider()
                st.subheader("Readiness summary")
                st.write(ca.readiness_headline(outcome["counts"]))

                m = st.columns(5)
                m[0].metric("Meets requirement", outcome["counts"][ca.STATUS_MEETS])
                m[1].metric("Potential issues", outcome["counts"][ca.STATUS_POTENTIAL])
                m[2].metric("Evidence missing", outcome["counts"][ca.STATUS_MISSING])
                m[3].metric("Testing required", outcome["counts"][ca.STATUS_TESTING])
                m[4].metric("Not applicable", outcome["counts"][ca.STATUS_NA])

                st.caption(
                    "Recipe version **%s** · criteria set **%s %s** · foam family **%s**"
                    % (
                        resolved["recipe_version"].version_label,
                        criteria_set.name, criteria_set.version,
                        grade.certipur_foam_family or "not set",
                    )
                )

                # --- what the assessment was run against ---------------------
                with st.expander(
                    "Recipe and raw materials (%d component%s)"
                    % (len(resolved["components"]), "" if len(resolved["components"]) == 1 else "s"),
                    expanded=False,
                ):
                    if resolved["unmapped_components"]:
                        st.warning(
                            "%d recipe component%s not linked to a raw material, so nothing can be "
                            "looked up for %s: %s"
                            % (
                                len(resolved["unmapped_components"]),
                                " is" if len(resolved["unmapped_components"]) == 1 else "s are",
                                "it" if len(resolved["unmapped_components"]) == 1 else "them",
                                ", ".join(c.raw_material_name or "(unnamed)" for c in resolved["unmapped_components"]),
                            )
                        )
                    rows = []
                    for comp in resolved["components"]:
                        material = next(
                            (m for m in resolved["materials"] if comp.raw_material_id == m.id), None
                        )
                        sds = resolved["sds_by_material"].get(material.id) if material else None
                        decl = resolved["declarations_by_material"].get(material.id) if material else None
                        rows.append({
                            "Component": comp.raw_material_name or "(unnamed)",
                            "Raw material": material.name if material else "not linked",
                            "Category": (material.category if material else "") or "—",
                            "php": comp.php,
                            "SDS": (
                                "—" if material is None
                                else ("Held" if sds is not None else "Missing")
                            ),
                            "Hazard codes": (sds.hazard_codes if sds is not None else "") or "—",
                            "Declaration": (
                                "—" if material is None
                                else ("Held" if decl is not None else "Not held")
                            ),
                        })
                    st.dataframe(rows, hide_index=True, use_container_width=True)

                # --- the results ---------------------------------------------
                st.divider()
                st.subheader("Requirement assessment")
                status_filter = st.multiselect(
                    "Show", list(ca.STATUSES), default=list(ca.STATUSES), key="certipur_filter"
                )
                shown = [i for i in outcome["items"] if i["status"] in status_filter]
                if not shown:
                    st.caption("No criterion matches the filter.")
                for item in shown:
                    criterion = item["criterion"]
                    with st.expander(
                        "%s  %s  %s — %s"
                        % (_STATUS_ICON.get(item["status"], ""), criterion.section,
                           criterion.title, item["status"]),
                        expanded=(item["status"] == ca.STATUS_POTENTIAL),
                    ):
                        st.markdown("**Requirement**")
                        st.write(criterion.requirement)
                        if criterion.limit_text:
                            st.caption("Limit: %s" % criterion.limit_text)
                        st.markdown("**Finding**")
                        st.write(item["rationale"])
                        if item.get("action"):
                            st.markdown("**Action**")
                            st.write(item["action"])
                        if criterion.note:
                            st.caption(criterion.note)
                        if item.get("evidence"):
                            st.markdown("**Evidence**")
                            st.dataframe(
                                [
                                    {
                                        "Type": e["evidence_type"],
                                        "Raw material": e.get("raw_material_name") or "—",
                                        "Document": e.get("document_reference") or "—",
                                        "What it showed": e.get("detail") or "—",
                                    }
                                    for e in item["evidence"]
                                ],
                                hide_index=True, use_container_width=True,
                            )

                # --- independent testing -------------------------------------
                st.divider()
                st.subheader("Independent laboratory testing")
                st.caption(
                    "These limits are on finished foam. CertiPUR accepts results only from these "
                    "two laboratories: %s" % "; ".join(cc.ACCREDITED_LABORATORIES)
                )
                st.dataframe(
                    [
                        {
                            "Section": i["criterion"].section,
                            "Requirement": i["criterion"].title,
                            "Limit": i["criterion"].limit_text or "—",
                            "Method": i["criterion"].test_method or "—",
                        }
                        for i in outcome["items"]
                        if i["criterion"].determination == cc.DETERMINATION_MEASURED
                    ],
                    hide_index=True, use_container_width=True,
                )

                # --- open actions --------------------------------------------
                open_items = [
                    i for i in outcome["items"]
                    if i["status"] in (ca.STATUS_POTENTIAL, ca.STATUS_MISSING)
                ]
                if open_items:
                    st.divider()
                    st.subheader("Open actions")
                    st.caption(
                        "Potential issues first - those are formulation changes. Evidence gaps "
                        "after them, since those are documents to collect."
                    )
                    st.dataframe(
                        [
                            {
                                "Priority": n + 1,
                                "Section": i["criterion"].section,
                                "Requirement": i["criterion"].title,
                                "Status": i["status"],
                                "Action": i.get("action") or "—",
                            }
                            for n, i in enumerate(
                                sorted(open_items, key=lambda x: 0 if x["status"] == ca.STATUS_POTENTIAL else 1)
                            )
                        ],
                        hide_index=True, use_container_width=True,
                    )

                # --- save ------------------------------------------------------
                st.divider()
                if page_usable:
                    st.subheader("Save this pre-audit")
                    st.caption(
                        "Saved as a dated snapshot that keeps the recipe version, the criteria "
                        "edition and the exact documents read. Running it again after corrective "
                        "action creates a new record and leaves this one unchanged."
                    )
                    save_notes = st.text_area("Notes for the record", key="certipur_notes")
                    if st.button("Save pre-audit", type="primary", key="certipur_save"):
                        plant = None
                        if grade.product_family is not None:
                            plant = session.get(Plant, grade.product_family.plant_id)
                        saved = ca.save_assessment(
                            session, outcome, company, plant, user=user, notes=save_notes or None
                        )
                        audit_log.log_export(
                            session, export_type="certipur_pre_audit",
                            description="CertiPUR pre-audit %d saved for %s (recipe %s)"
                                        % (saved.id, grade.grade_name, saved.recipe_version_label),
                            user_id=user.get("id"), company_id=company.id,
                        )
                        session.commit()
                        set_pending_banner(
                            "certipur_banner",
                            "Pre-audit saved for %s: %d met, %d potential issue(s), %d evidence "
                            "gap(s), %d awaiting laboratory testing."
                            % (grade.grade_name, saved.count_meets, saved.count_potential_issue,
                               saved.count_evidence_missing, saved.count_testing_required),
                        )
                        st.rerun()

            # --- history ---------------------------------------------------
            st.divider()
            st.subheader("Saved pre-audits")
            history = ca.assessments_for_grade(session, grade.id)
            if not history:
                st.caption("None saved for %s yet." % grade.grade_name)
            else:
                st.dataframe(
                    [
                        {
                            "Saved (UTC)": a.assessed_at,
                            "By": a.assessed_by or "not recorded",
                            "Recipe": a.recipe_version_label or "—",
                            "Criteria": a.criteria_set_label or "—",
                            "Met": a.count_meets,
                            "Issues": a.count_potential_issue,
                            "Gaps": a.count_evidence_missing,
                            "Testing": a.count_testing_required,
                        }
                        for a in history
                    ],
                    hide_index=True, use_container_width=True,
                )
                chosen = st.selectbox(
                    "Report for",
                    history,
                    format_func=lambda a: "%s · %s · %d issue(s), %d gap(s)"
                    % (
                        a.assessed_at.strftime("%Y-%m-%d %H:%M") if a.assessed_at else "—",
                        a.recipe_version_label or "—",
                        a.count_potential_issue or 0, a.count_evidence_missing or 0,
                    ),
                    key="certipur_history",
                )
                if chosen is not None:
                    st.download_button(
                        "Download the CertiPUR Readiness Pre-Audit report",
                        data=reports.render_certipur_pre_audit_docx(session, chosen),
                        file_name="CertiPUR_Readiness_Pre_Audit_%s_%s.docx"
                        % (
                            (chosen.foam_grade_name or "grade").replace(" ", "_"),
                            chosen.assessed_at.strftime("%Y%m%d") if chosen.assessed_at else "",
                        ),
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="certipur_report_dl",
                    )
