"""Screen: Raw Materials (master data)

A master list of raw materials so recipes can be built from a dropdown
instead of retyping the same material name (and its supplier) into every
recipe component. Supports manual entry and bulk CSV/Excel import, since
material lists commonly already exist as an ERP/supplier export.
"""

import pandas as pd
import streamlit as st

import ai_assistant
import certipur_criteria
import document_store
import regulatory_reference
from access_control import can_use_page
from auth import current_user, logout_button, require_login
from db import (
    DOCUMENT_TYPES,
    DOCUMENT_TYPE_SDS,
    DOCUMENT_TYPE_TDS,
    RAW_MATERIAL_CATEGORIES,
    Company,
    COMPOSITION_SOURCES,
    RawMaterial,
    RawMaterialComposition,
    RawMaterialDocument,
    RecipeComponent,
    Supplier,
    get_session,
    init_db,
)
from helpers import (
    clickable_table,
    csv_excel_uploader,
    delete_with_confirm,
    page_setup,
    parse_bool,
    render_data_table,
    render_function_action_intro,
    set_pending_banner,
    show_pending_banner,
    view_only_notice,
)
from tenant_scope import company_picker


def _extract_pdf_text(uploaded_file):
    """Best-effort text extraction from an uploaded PDF (TDS or SDS).
    Returns "" on any failure rather than raising, since a badly-scanned
    or image-only PDF shouldn't crash the page - the user still has the
    manual entry tab as a fallback."""
    try:
        import pdfplumber

        with pdfplumber.open(uploaded_file) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return ""

_ADD_NEW_SUPPLIER = "+ Add new supplier..."


def _supplier_names(session, company_id, include_inactive=False):
    q = session.query(Supplier)
    if company_id is not None:
        q = q.filter(Supplier.company_id == company_id)
    if not include_inactive:
        q = q.filter(Supplier.active == True)  # noqa: E712
    return [s.name for s in q.order_by(Supplier.name).all()]


def _supplier_picker(session, company_id, key_prefix, current_value=None):
    """Dropdown-with-type-new-fallback for a raw material's default supplier,
    mirroring the same pattern used elsewhere in the app for raw materials
    themselves. Deliberately rendered OUTSIDE any st.form (like the Edit
    Recipe data_editor on the Recipes page) so picking
    "+ Add new supplier..." can immediately reveal the free-text input on the
    same rerun - a selectbox inside a form only reruns on submit, which would
    hide that follow-up field until too late.

    Returns the resolved supplier name (str) or "" if none chosen. Any
    genuinely new name typed here is registered into the Supplier master
    list by the caller once the surrounding form is actually submitted (see
    _ensure_supplier_exists), not here, so browsing the dropdown without
    saving never creates orphan supplier rows.
    """
    names = _supplier_names(session, company_id)
    options = [""] + names + [_ADD_NEW_SUPPLIER]
    if current_value and current_value not in names and current_value != "":
        # Existing free-text value that isn't in the master list yet (legacy
        # data, or a typo-fix target) - keep it selectable/visible rather than
        # silently dropping it.
        options = [""] + sorted(set(names) | {current_value}) + [_ADD_NEW_SUPPLIER]
    default_index = options.index(current_value) if current_value in options else 0
    choice = st.selectbox("Default supplier", options, index=default_index, key=f"{key_prefix}_supplier_choice")
    if choice == _ADD_NEW_SUPPLIER:
        return st.text_input("New supplier name", key=f"{key_prefix}_supplier_new").strip()
    return choice


def _ensure_supplier_exists(session, company_id, name):
    """Register a supplier name into the master list (for the given company)
    if it's new. Safe to call with a blank name (no-op) or a name that
    already exists for that company (no-op)."""
    name = (name or "").strip()
    if not name:
        return
    q = session.query(Supplier).filter(Supplier.name == name)
    if company_id is not None:
        q = q.filter(Supplier.company_id == company_id)
    if not q.first():
        session.add(Supplier(company_id=company_id, name=name))


RAW_MATERIAL_REQUIRED_COLUMNS = ["name"]
RAW_MATERIAL_OPTIONAL_COLUMNS = ["category", "default_supplier", "cost_per_kg", "notes", "active"]

page_setup("Raw Materials")
init_db()
require_login()
logout_button()

st.title("Raw Materials")
render_function_action_intro(
    function_text=(
        "Maintains the master list of raw materials used across every recipe - polyols, "
        "isocyanates, catalysts, surfactants, additives, and so on - each with its category, "
        "default supplier, and cost per kg. Recipe components pick from this list rather than "
        "free-typing the same material name and supplier into every recipe, and the cost per kg "
        "recorded here is what Recipe Optimization uses to price out a formulation."
    ),
    action_text=(
        "Add a material manually, or upload a supplier's technical data sheet (TDS) under 'Add "
        "from TDS' to prefill its fields instead of retyping them. Attach the safety data sheet "
        "(SDS) on the Documents tab - it is what CertiPUR Readiness reads, and it is required "
        "before a material can be added if your company has that function. "
        "Use CSV/Excel import to bulk-load a material list from an "
        "ERP or supplier export. Set cost per kg on each material so Recipe Optimization can "
        "price formulations completely. The 'Default supplier' dropdown is maintained on the "
        "Suppliers page - keep that list curated so the same supplier doesn't end up entered "
        "twice under slightly different spellings."
    ),
)
session = get_session()
user = current_user()
is_platform_owner = user["is_platform_owner"]
own_company_id = user["company_id"]
page_usable = can_use_page("raw_materials", role_id=user["role_id"], session=session, is_super_admin=user["is_super_admin"])
if not page_usable:
    view_only_notice()

company_filter, all_companies = company_picker(
    st, session, is_platform_owner, own_company_id, key="rawmat_company_filter"
)
if not is_platform_owner and not company_filter:
    st.warning("Your account isn't linked to a company yet - contact the platform administrator.")
    st.stop()


def _target_company(key):
    """Company a new raw material/supplier should be created under. Locked
    to the user's own company for non-platform-owners; for the platform
    owner, uses the current company filter if one is picked, otherwise asks
    which company this new record belongs to (required when viewing 'All
    companies')."""
    if not is_platform_owner:
        return company_filter
    if company_filter is not None:
        return company_filter
    return st.selectbox("Company *", all_companies, format_func=lambda c: c.name, key=key)


tab_manual, tab_tds, tab_docs, tab_import = st.tabs(
    ["Manual entry", "Add from TDS", "Documents", "CSV / Excel import"]
)

# Whether a safety data sheet is required to create a raw material here. Only
# for a company that has opted into CertiPUR Readiness: the criteria are read
# out of the SDS, so without one the function cannot be provided at all
# (Stefan, 20 Aug 2026). Every other customer is unaffected.
_sds_required = document_store.certipur_required(company_filter)


def _sds_upload_controls(key_prefix):
    """The SDS uploader shown on the two create paths. Returns the uploaded
    file or None. Says plainly whether it is required and why."""
    if _sds_required:
        st.markdown("**Safety data sheet (SDS) \u2013 required**")
        st.caption(
            "%s has CertiPUR Readiness, and the readiness assessment is read out of the "
            "safety data sheet - the hazard classification in section 2 and the composition "
            "in section 3. A material cannot be added without one."
            % (company_filter.name if company_filter is not None else "This company")
        )
    else:
        st.markdown("**Safety data sheet (SDS) \u2013 optional**")
        st.caption(
            "Stored against the material with its hazard classification and composition. "
            "Not required today; it becomes required if CertiPUR Readiness is switched on."
        )
    return st.file_uploader(
        "Safety data sheet (PDF)", type=["pdf"], key="%s_sds" % key_prefix,
    )


def _store_uploaded_sds(material, uploaded, uploaded_by):
    """Read, store and extract one uploaded SDS. Returns a status line, or
    None when there was nothing to store. Never raises."""
    if uploaded is None:
        return None
    try:
        uploaded.seek(0)
        raw_bytes = uploaded.read()
        uploaded.seek(0)
        text = _extract_pdf_text(uploaded)
        doc = document_store.store_document(
            session, material, raw_bytes, uploaded.name, DOCUMENT_TYPE_SDS,
            uploaded_by=uploaded_by, extracted_text=text,
        )
        return document_store.extraction_summary(doc)
    except ValueError as exc:
        return "The safety data sheet was not stored: %s" % exc
    except Exception:
        return (
            "The safety data sheet could not be stored. The material was saved; attach the "
            "sheet again on the Documents tab."
        )

with tab_manual:
    show_pending_banner("rawmat_manual_msg")
    if not page_usable:
        st.caption("View-only access - adding a raw material is restricted for your role.")
    else:
        manual_target_company = _target_company("add_rawmat_company")
        add_supplier_choice = _supplier_picker(
            session, manual_target_company.id if manual_target_company else None, key_prefix="add_rawmat"
        )
        manual_sds = _sds_upload_controls("add_rawmat")
        with st.form("add_raw_material"):
            name = st.text_input("Raw material name *")
            c1, c3 = st.columns(2)
            category = c1.selectbox("Category", RAW_MATERIAL_CATEGORIES)
            cost_per_kg = c3.number_input(
                "Cost per kg",
                min_value=0.0,
                step=0.01,
                value=0.0,
                help="Leave at 0 if not known yet - recipe cost calculations skip materials with no cost recorded "
                "rather than treating them as free.",
            )
            notes = st.text_area("Notes")
            active = st.checkbox("Active", value=True)
            submitted = st.form_submit_button("Save raw material")
            if submitted:
                if not name.strip():
                    st.error("Raw material name is required.")
                elif not manual_target_company:
                    st.error("Pick a company for this raw material.")
                elif _sds_required and manual_sds is None:
                    # Refused rather than saved-and-flagged. A material created
                    # without its sheet is a compliance gap that looks like a
                    # complete record, and the whole point of the requirement is
                    # that the evidence arrives with the material.
                    st.error(
                        "A safety data sheet is required for this company. Attach it above, or "
                        "ask your administrator to turn CertiPUR Readiness off if this material "
                        "genuinely has no sheet."
                    )
                else:
                    _ensure_supplier_exists(session, manual_target_company.id, add_supplier_choice)
                    material = RawMaterial(
                        company_id=manual_target_company.id,
                        name=name.strip(),
                        category=category,
                        default_supplier=add_supplier_choice,
                        cost_per_kg=cost_per_kg or None,
                        notes=notes,
                        active=active,
                    )
                    session.add(material)
                    session.flush()
                    sds_note = _store_uploaded_sds(
                        material, manual_sds, user.get("display_name") or user.get("username")
                    )
                    session.commit()
                    set_pending_banner(
                        "rawmat_manual_msg",
                        "Raw material '%s' added." % name + ((" " + sds_note) if sds_note else ""),
                    )
                    st.rerun()

with tab_tds:
    show_pending_banner("rawmat_tds_msg")
    if not page_usable:
        st.caption("View-only access - adding a raw material is restricted for your role.")
    else:
        st.caption(
            "Upload a supplier technical data sheet (TDS) to prefill the fields below instead of "
            "retyping them. Both documents are stored against the material once it is saved."
        )
        tds_file = st.file_uploader("Technical data sheet (PDF) *", type=["pdf"], key="tds_upload")
        sds_file = st.file_uploader(
            "Safety data sheet (PDF) *" if _sds_required else "Safety data sheet (PDF, optional)",
            type=["pdf"], key="sds_upload",
        )
        if _sds_required:
            st.caption(
                "The safety data sheet is required for this company: CertiPUR Readiness reads the "
                "hazard classification from its section 2 and the composition from its section 3."
            )

        if tds_file is not None:
            if st.button("Extract from document(s)", key="extract_tds_btn"):
                tds_text = _extract_pdf_text(tds_file)
                sds_text = _extract_pdf_text(sds_file) if sds_file is not None else None
                if not tds_text.strip():
                    st.warning(
                        "Could not read any text from this PDF (it may be a scanned image). "
                        "Use Manual entry instead."
                    )
                elif ai_assistant.openai_key_configured():
                    with st.spinner("Using PI3 to read the document..."):
                        extracted = ai_assistant.extract_raw_material_from_tds(tds_text, sds_text)
                    if extracted:
                        st.session_state["tds_extracted"] = extracted
                        st.success("Extracted - review and adjust the fields below, then save.")
                else:
                    # No OpenAI key configured: hand the operator the raw text to
                    # copy from by hand rather than offering a feature that can't run.
                    st.session_state["tds_extracted"] = {
                        "name": "",
                        "category": "",
                        "default_supplier": "",
                        "notes": tds_text[:2000],
                    }
                    st.info(
                        "PI3 isn't configured on this deployment, so fields can't be auto-filled. "
                        "The extracted document text is in Notes below for you to copy from."
                    )

        tds_extracted = st.session_state.get("tds_extracted", {})
        tds_target_company = _target_company("tds_rawmat_company")
        t_supplier = _supplier_picker(
            session, tds_target_company.id if tds_target_company else None,
            key_prefix="tds_rawmat", current_value=tds_extracted.get("default_supplier", ""),
        )
        with st.form("add_raw_material_from_tds"):
            t_name = st.text_input("Raw material name *", value=tds_extracted.get("name", ""))
            tds_category = tds_extracted.get("category", "")
            t_category = st.selectbox(
                "Category",
                RAW_MATERIAL_CATEGORIES,
                index=RAW_MATERIAL_CATEGORIES.index(tds_category) if tds_category in RAW_MATERIAL_CATEGORIES else 0,
            )
            t_cost = st.number_input(
                "Cost per kg",
                min_value=0.0,
                step=0.01,
                value=0.0,
                help="A TDS doesn't carry pricing - enter it here if you know it, or leave at 0 and add it later.",
            )
            t_notes = st.text_area("Notes", value=tds_extracted.get("notes", ""), height=150)
            t_active = st.checkbox("Active", value=True, key="tds_active")
            if st.form_submit_button("Save raw material (from TDS)"):
                if not t_name.strip():
                    st.error("Raw material name is required.")
                elif not tds_target_company:
                    st.error("Pick a company for this raw material.")
                elif _sds_required and sds_file is None:
                    st.error(
                        "A safety data sheet is required for this company. Attach it above before "
                        "saving."
                    )
                else:
                    _ensure_supplier_exists(session, tds_target_company.id, t_supplier)
                    material = RawMaterial(
                        company_id=tds_target_company.id,
                        name=t_name.strip(),
                        category=t_category,
                        default_supplier=t_supplier,
                        cost_per_kg=t_cost or None,
                        notes=t_notes,
                        active=t_active,
                    )
                    session.add(material)
                    session.flush()
                    who = user.get("display_name") or user.get("username")
                    notes_out = []
                    # The TDS is kept too. It prefilled the fields above, and
                    # until now that was all it did - the document itself was
                    # discarded, so nothing could later show where a value came
                    # from.
                    if tds_file is not None:
                        try:
                            tds_file.seek(0)
                            document_store.store_document(
                                session, material, tds_file.read(), tds_file.name,
                                DOCUMENT_TYPE_TDS, uploaded_by=who,
                                extracted_text=_extract_pdf_text(tds_file), extract=False,
                            )
                        except Exception:
                            notes_out.append("The technical data sheet could not be stored.")
                    sds_note = _store_uploaded_sds(material, sds_file, who)
                    if sds_note:
                        notes_out.append(sds_note)
                    session.commit()
                    st.session_state.pop("tds_extracted", None)
                    set_pending_banner(
                        "rawmat_tds_msg",
                        "Raw material '%s' added. %s" % (t_name, " ".join(notes_out)),
                    )
                    st.rerun()

with tab_docs:
    # The backfill worklist, and the only place a document can be attached to a
    # material that already exists. A company that enables CertiPUR after its
    # raw materials are on the system needs exactly this - the mandatory rule on
    # the create paths governs new materials and can do nothing about old ones.
    show_pending_banner("rawmat_docs_msg")
    st.caption(
        "Safety data sheets, technical data sheets and supplier declarations held against each "
        "raw material. A new revision is stored beside the old one rather than replacing it, so "
        "an assessment made last month still points at the sheet it actually read."
    )

    doc_materials = (
        session.query(RawMaterial)
        .filter(RawMaterial.company_id == company_filter.id)
        .order_by(RawMaterial.name)
        .all()
        if company_filter is not None else []
    )

    if company_filter is None:
        st.info("Pick a single company above to work with its documents.")
    elif not doc_materials:
        st.info("No raw materials recorded for %s yet." % company_filter.name)
    else:
        with_sds = document_store.material_ids_with_document(
            session, [m.id for m in doc_materials], DOCUMENT_TYPE_SDS
        )
        missing = [m for m in doc_materials if m.id not in with_sds]
        if missing:
            st.warning(
                "%d of %d raw materials have no safety data sheet: %s"
                % (len(missing), len(doc_materials), ", ".join(m.name for m in missing))
            )
        else:
            st.success("Every raw material has a current safety data sheet.")

        st.dataframe(
            [
                {
                    "Raw material": m.name,
                    "Category": m.category or "\u2014",
                    "Supplier": m.default_supplier or "\u2014",
                    "SDS": "Held" if m.id in with_sds else "Missing",
                }
                for m in doc_materials
            ],
            hide_index=True, use_container_width=True,
        )

        st.divider()
        chosen = st.selectbox(
            "Raw material", doc_materials, format_func=lambda m: m.name, key="docs_material",
        )
        if chosen is not None:
            held = document_store.documents_for(session, chosen.id)
            if held:
                st.markdown("**Documents held**")
                for d in held:
                    label = "%s \u00b7 %s" % (d.document_type, d.file_name or "(no file name)")
                    if not d.is_current:
                        label += "  \u2014 superseded"
                    with st.expander(label, expanded=bool(d.is_current and d.document_type == DOCUMENT_TYPE_SDS)):
                        st.caption(
                            "Uploaded %s by %s\u2003\u00b7\u2003%.0f KB\u2003\u00b7\u2003sha256 %s"
                            % (
                                d.created_at.strftime("%Y-%m-%d %H:%M UTC") if d.created_at else "\u2014",
                                d.uploaded_by or "not recorded",
                                (d.file_size or 0) / 1024.0,
                                (d.file_hash or "")[:16],
                            )
                        )
                        if d.document_type == DOCUMENT_TYPE_SDS:
                            st.write(document_store.extraction_summary(d))
                            prohibited = certipur_criteria.prohibited_hazard_codes(d.hazard_codes)
                            if prohibited:
                                st.error(
                                    "CertiPUR section 3.4: this sheet carries %s, which is a CMR "
                                    "class 1a/1b or STOT SE 1 classification. A raw material "
                                    "carrying one may not be intentionally used in foam certified "
                                    "under CertiPUR." % ", ".join(prohibited)
                                )
                            if d.substances:
                                st.dataframe(
                                    [
                                        {
                                            "Substance": sub.name or "\u2014",
                                            "CAS": sub.cas_number or "\u2014",
                                            "EC": sub.ec_number or "\u2014",
                                            "Concentration": sub.concentration or "\u2014",
                                            "Hazard codes": sub.hazard_codes or "\u2014",
                                        }
                                        for sub in d.substances
                                    ],
                                    hide_index=True, use_container_width=True,
                                )
                        if d.file_bytes:
                            st.download_button(
                                "Download the original",
                                data=bytes(d.file_bytes),
                                file_name=d.file_name or "document.pdf",
                                mime=d.content_type or "application/pdf",
                                key="dl_doc_%d" % d.id,
                            )
            else:
                st.info("No documents held against %s." % chosen.name)

            if page_usable:
                st.markdown("**Attach a document**")
                up_type = st.selectbox(
                    "Document type",
                    list(DOCUMENT_TYPES),
                    key="docs_type",
                    help=(
                        "Type the document as what it actually is. Supplier-issued evidence - a "
                        "declaration, a specification, a certificate of analysis or a test report "
                        "- covers what a safety data sheet does not: heavy metal content of colour "
                        "pastes, azo dye compliance with REACH Restriction Entry 43, and the "
                        "chlorobenzene content of a diisocyanate. CertiPUR names the supplier as "
                        "the source for all three and requires no particular title, so record the "
                        "form the supplier actually issued rather than relabelling it."
                    ),
                )
                up_file = st.file_uploader(
                    "Document (PDF)", type=["pdf"], key="docs_upload_%d" % chosen.id
                )
                if up_file is not None and st.button("Store against %s" % chosen.name, key="docs_store"):
                    try:
                        up_file.seek(0)
                        raw_bytes = up_file.read()
                        up_file.seek(0)
                        doc = document_store.store_document(
                            session, chosen, raw_bytes, up_file.name, up_type,
                            uploaded_by=user.get("display_name") or user.get("username"),
                            extracted_text=_extract_pdf_text(up_file),
                            extract=(up_type == DOCUMENT_TYPE_SDS),
                        )
                        session.commit()
                        set_pending_banner(
                            "rawmat_docs_msg",
                            "%s stored against %s. %s" % (
                                up_type, chosen.name,
                                document_store.extraction_summary(doc)
                                if up_type == DOCUMENT_TYPE_SDS else "",
                            ),
                        )
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                    except Exception:
                        session.rollback()
                        st.error("The document could not be stored. Try again, or ask your administrator to check the error log.")

            # --- the controlled identity route (2026-08-21) -----------------
            # For a raw material where no supplier safety data sheet is held.
            # Water is the case that forced it: a known identity, no sheet in
            # the customer dataset, and previously a permanent gap on the
            # composition screens with no honest way to close it.
            #
            # It answers COMPOSITION questions - what the material contains -
            # and nothing else. It is not a classification and does not answer
            # criterion 3.4, which reads the classification a supplier states.
            held_comp = (
                session.query(RawMaterialComposition)
                .filter(RawMaterialComposition.raw_material_id == chosen.id,
                        RawMaterialComposition.is_current.is_(True))
                .order_by(RawMaterialComposition.id)
                .all()
            )
            with st.expander(
                "Controlled composition\u2003\u00b7\u2003%s"
                % ("%d substance(s) recorded" % len(held_comp) if held_comp else "none recorded"),
                expanded=False,
            ):
                st.caption(
                    "For a raw material where no supplier safety data sheet is held - water is "
                    "the usual one. The composition screens read this where no readable sheet is "
                    "available, and every assessment records which of the two routes it used. It "
                    "states what the material is, not how it is classified, so it does not "
                    "replace a supplier sheet where one exists and does not answer criterion 3.4."
                )
                if held_comp:
                    st.dataframe(
                        [
                            {
                                "Substance": c.name or "\u2014",
                                "CAS": c.cas_number or "\u2014",
                                "EC": c.ec_number or "\u2014",
                                "Concentration": c.concentration or "\u2014",
                                "Source": c.source or "\u2014",
                            }
                            for c in held_comp
                        ],
                        hide_index=True, use_container_width=True,
                    )
                    if st.button("Remove the recorded composition", key="comp_clear_%d" % chosen.id):
                        for c in held_comp:
                            c.is_current = False
                        session.commit()
                        set_pending_banner(
                            "rawmat_docs_msg",
                            "The controlled composition for %s is no longer current." % chosen.name,
                        )
                        st.rerun()
                    st.divider()
                cc1, cc2, cc3 = st.columns([3, 2, 2])
                comp_name = cc1.text_input("Substance", key="comp_name_%d" % chosen.id)
                comp_cas = cc2.text_input("CAS number", key="comp_cas_%d" % chosen.id)
                comp_conc = cc3.text_input("Concentration", key="comp_conc_%d" % chosen.id,
                                           placeholder="e.g. 100 %")
                comp_source = st.selectbox(
                    "Where the identity comes from", COMPOSITION_SOURCES, key="comp_src_%d" % chosen.id,
                )
                comp_note = st.text_input(
                    "Source note", key="comp_note_%d" % chosen.id,
                    placeholder="e.g. CAS Registry; the reference or record this identity came from",
                )
                if st.button("Record against %s" % chosen.name, key="comp_add_%d" % chosen.id):
                    if not (comp_name or "").strip() or not (comp_cas or "").strip():
                        st.error("A substance name and a CAS number are both needed.")
                    elif not regulatory_reference.cas_check_digit_ok(comp_cas):
                        # Checked here as well as at the point of use: a bad
                        # identifier caught at entry costs a retype, and one
                        # caught during an assessment costs a wrong conclusion.
                        st.error(
                            "%s is not a valid CAS registry number - the check digit does not "
                            "match. Confirm it against the source before recording it." % comp_cas
                        )
                    else:
                        session.add(RawMaterialComposition(
                            raw_material_id=chosen.id, company_id=chosen.company_id,
                            name=comp_name.strip(),
                            cas_number=regulatory_reference.normalise_cas(comp_cas),
                            concentration=(comp_conc or "").strip() or None,
                            source=comp_source, source_note=(comp_note or "").strip() or None,
                            recorded_by=user.get("display_name") or user.get("username"),
                            is_current=True,
                        ))
                        session.commit()
                        set_pending_banner(
                            "rawmat_docs_msg",
                            "%s recorded against %s as a controlled composition."
                            % (comp_name.strip(), chosen.name),
                        )
                        st.rerun()

with tab_import:
    if not page_usable:
        st.caption("View-only access - importing raw materials is restricted for your role.")
    else:
        import_target_company = _target_company("import_rawmat_company")
        show_pending_banner("rawmat_import_msg")
        df, filename = csv_excel_uploader(RAW_MATERIAL_REQUIRED_COLUMNS, RAW_MATERIAL_OPTIONAL_COLUMNS, key="rawmat_upload")
        if df is not None and not import_target_company:
            st.error("Pick a company above before importing.")
        elif df is not None:
            existing_query = session.query(RawMaterial).filter(RawMaterial.company_id == import_target_company.id)
            existing_names = {m.name.strip().lower() for m in existing_query.all()}
            good_rows, dup_rows = [], []
            for _, row in df.iterrows():
                name_val = str(row.get("name", "") or "").strip()
                if not name_val:
                    continue
                if name_val.lower() in existing_names:
                    dup_rows.append(row)
                else:
                    good_rows.append(row)
                    existing_names.add(name_val.lower())

            st.write(f"Rows ready to import: **{len(good_rows)}** | Rows flagged as duplicates: **{len(dup_rows)}**")
            if good_rows and document_store.certipur_required(import_target_company):
                # Bulk import is not blocked for a CertiPUR company, even though
                # manual entry is. A customer migrating a material list from an
                # ERP has no way to attach sixty safety data sheets one file at
                # a time through a spreadsheet, and refusing the import would
                # leave them unable to load their own data at all.
                #
                # What is not acceptable is letting it happen silently. Every
                # material created here arrives without a sheet, and the count
                # is stated before the button, not discovered later on the
                # Documents tab.
                st.warning(
                    "%s has CertiPUR Readiness. These %d material%s will be created without a "
                    "safety data sheet and will show as an evidence gap until one is attached on "
                    "the Documents tab. A material added one at a time cannot be saved without "
                    "its sheet; a bulk import can, because a spreadsheet cannot carry the "
                    "documents."
                    % (import_target_company.name, len(good_rows), "" if len(good_rows) == 1 else "s")
                )
            if dup_rows:
                st.warning("These rows match a raw material name already in the list and were skipped.")
                render_data_table(pd.DataFrame(dup_rows), max_height="400px")

            if good_rows and st.button("Confirm import", key="confirm_rawmat_import"):
                for row in good_rows:
                    cat = str(row.get("category", "") or "").strip()
                    cost_val = row.get("cost_per_kg")
                    supplier_val = str(row.get("default_supplier", "") or "").strip()
                    _ensure_supplier_exists(session, import_target_company.id, supplier_val)
                    session.add(
                        RawMaterial(
                            company_id=import_target_company.id,
                            name=str(row["name"]).strip(),
                            category=cat if cat in RAW_MATERIAL_CATEGORIES else (cat or "Other"),
                            default_supplier=supplier_val,
                            cost_per_kg=float(cost_val) if not pd.isna(cost_val) else None,
                            notes=str(row.get("notes", "") or ""),
                            active=True if pd.isna(row.get("active")) else parse_bool(row.get("active")),
                        )
                    )
                session.commit()
                set_pending_banner("rawmat_import_msg", f"Imported {len(good_rows)} raw material(s) from {filename}.")
                st.rerun()

st.divider()
st.subheader("Raw materials")

materials_query = session.query(RawMaterial)
if company_filter is not None:
    materials_query = materials_query.filter(RawMaterial.company_id == company_filter.id)
materials = materials_query.order_by(RawMaterial.name).all()
if not materials:
    st.info("No raw materials recorded yet.")
else:
    df = pd.DataFrame(
        [
            {
                **({"Company": m.company.name if m.company else "—"} if is_platform_owner else {}),
                "Name": m.name,
                "Category": m.category or "—",
                "Default supplier": m.default_supplier or "",
                "Cost/kg": m.cost_per_kg,
                "Active": m.active,
                "Notes": m.notes or "",
            }
            for m in materials
        ]
    )

    st.caption("Filter by column:")
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    name_filter = c1.text_input("Name contains", key="rawmat_filter_name")
    category_filter = c2.multiselect(
        "Category", sorted(df["Category"].unique()), key="rawmat_filter_category"
    )
    supplier_filter = c3.text_input("Supplier contains", key="rawmat_filter_supplier")
    active_filter = c4.selectbox("Active", ["All", "Yes", "No"], key="rawmat_filter_active")
    notes_filter = st.text_input("Notes contains", key="rawmat_filter_notes")

    mask = pd.Series(True, index=df.index)
    if name_filter:
        mask &= df["Name"].str.contains(name_filter, case=False, na=False)
    if category_filter:
        mask &= df["Category"].isin(category_filter)
    if supplier_filter:
        mask &= df["Default supplier"].str.contains(supplier_filter, case=False, na=False)
    if active_filter == "Yes":
        mask &= df["Active"]
    elif active_filter == "No":
        mask &= ~df["Active"]
    if notes_filter:
        mask &= df["Notes"].str.contains(notes_filter, case=False, na=False)

    filtered_materials = [m for m, keep in zip(materials, mask) if keep]
    filtered_df = df[mask]

    st.caption(
        f"Showing {len(filtered_df)} of {len(df)} raw material(s). "
        "Click a row to edit (and optionally delete) that material."
    )
    idx = clickable_table(filtered_df.to_dict("records"), key="rawmat_table")
    if idx is not None and idx < len(filtered_materials):
        st.session_state["rawmat_selected_id"] = filtered_materials[idx].id
    else:
        st.session_state.pop("rawmat_selected_id", None)

    selected_id = st.session_state.get("rawmat_selected_id")
    selected = next((m for m in materials if m.id == selected_id), None)

    if selected:
        st.divider()
        st.subheader(f"Edit: {selected.name}")
        if not page_usable:
            st.caption("View-only access - editing and deleting is restricted for your role.")
        else:
            e_supplier = _supplier_picker(
                session, selected.company_id, key_prefix=f"edit_rawmat_{selected.id}",
                current_value=selected.default_supplier or "",
            )
            with st.form(f"edit_rawmat_{selected.id}"):
                if is_platform_owner:
                    e_company = st.selectbox(
                        "Company *", all_companies,
                        index=next((i for i, c in enumerate(all_companies) if c.id == selected.company_id), 0),
                        format_func=lambda c: c.name, key=f"edit_rawmat_company_{selected.id}",
                    )
                else:
                    e_company = company_filter
                e_name = st.text_input("Raw material name *", value=selected.name, key=f"edit_rawmat_name_{selected.id}")
                ec1, ec2 = st.columns(2)
                e_category = ec1.selectbox(
                    "Category",
                    RAW_MATERIAL_CATEGORIES,
                    index=RAW_MATERIAL_CATEGORIES.index(selected.category) if selected.category in RAW_MATERIAL_CATEGORIES else 0,
                    key=f"edit_rawmat_category_{selected.id}",
                )
                e_cost = ec2.number_input(
                    "Cost per kg", min_value=0.0, step=0.01, value=float(selected.cost_per_kg or 0.0),
                    key=f"edit_rawmat_cost_{selected.id}",
                )
                e_notes = st.text_area("Notes", value=selected.notes or "", key=f"edit_rawmat_notes_{selected.id}")
                e_active = st.checkbox("Active", value=selected.active, key=f"edit_rawmat_active_{selected.id}")
                if st.form_submit_button("Save changes"):
                    if not e_name.strip():
                        st.error("Raw material name is required.")
                    else:
                        target_company_id = e_company.id if e_company else selected.company_id
                        _ensure_supplier_exists(session, target_company_id, e_supplier)
                        selected.company_id = target_company_id
                        selected.name = e_name.strip()
                        selected.category = e_category
                        selected.default_supplier = e_supplier
                        selected.cost_per_kg = e_cost or None
                        selected.notes = e_notes
                        selected.active = e_active
                        session.commit()
                        st.success("Raw material updated.")
                        st.rerun()

            linked_components = (
                session.query(RecipeComponent).filter(RecipeComponent.raw_material_id == selected.id).count()
            )
            if linked_components:
                warning = (
                    f"{linked_components} recipe component(s) reference this raw material. Deleting it will unlink "
                    "them (their component name/role stays, but the raw-material link is cleared) rather than "
                    "deleting those recipe components."
                )
            else:
                warning = "No recipe components reference this raw material — deleting it is safe."

            def _do_delete_rawmat(_session=session, _id=selected.id):
                _session.query(RecipeComponent).filter(RecipeComponent.raw_material_id == _id).update(
                    {"raw_material_id": None}, synchronize_session="fetch"
                )
                _session.query(RawMaterial).filter(RawMaterial.id == _id).delete(synchronize_session=False)
                _session.commit()
                st.session_state.pop("rawmat_selected_id", None)

            delete_with_confirm(
                selected.name, _do_delete_rawmat, key_prefix=f"rawmat_{selected.id}", extra_warning=warning
            )

        if st.button("Clear selection", key="clear_rawmat_selection"):
            st.session_state.pop("rawmat_selected_id", None)
            st.rerun()
