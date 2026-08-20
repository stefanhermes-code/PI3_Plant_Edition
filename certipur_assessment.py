# -*- coding: utf-8 -*-
"""The CertiPUR readiness pre-audit: what it concludes, and on what.

Reads a foam grade's active recipe, the raw materials behind it and the
supplier documents held against those, and returns one result per criterion.
Streamlit-free and screen-free, so it can be tested without a browser and
called from a report as easily as from a page.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
Absence of evidence is never a pass. Every path below that cannot find what it
needs returns Evidence missing and names what is missing; none of them fall
through to Meets requirement. That sounds obvious and is exactly the failure a
readiness tool is most likely to have, because the happy path and the
no-data path look identical from the outside - both produce no findings.

WHAT IT WILL NOT DO
-------------------
The four measured criteria (sections 2.1 to 2.4) are returned as Testing
required every single time, with no indicative reading, no "looks likely", and
no partial credit from internal test data. Those limits are on finished foam
and only an accredited laboratory can determine them. A pre-audit that hints
at a VOC result would be worse than one that stays silent, because somebody
would rely on it.

STATUS MEANINGS
---------------
Meets requirement   Traceable evidence supports the criterion.
Potential issue     Evidence indicates a concern - a prohibited substance in
                    the formulation, or a prohibited hazard classification.
Evidence missing    The criterion is answerable once something is supplied,
                    and the result names exactly what.
Testing required    An accredited laboratory determines this. Always the four
                    measured criteria; never anything else.
Not applicable      The formulation excludes the criterion - no colourants, no
                    biocides, no isocyanate.
"""

import datetime as dt

import certipur_criteria as cc
import document_store
from db import (
    DOCUMENT_TYPE_DECLARATION,
    DOCUMENT_TYPE_SDS,
    CertipurAssessment,
    CertipurAssessmentEvidence,
    CertipurAssessmentItem,
    CertipurCriteriaSet,
    CertipurCriterion,
    CertipurCriterionSubstance,
    RawMaterial,
    RawMaterialDocument,
    RawMaterialSubstance,
    RecipeComponent,
)

STATUS_MEETS = "Meets requirement"
STATUS_POTENTIAL = "Potential issue"
STATUS_MISSING = "Evidence missing"
STATUS_TESTING = "Testing required"
STATUS_NA = "Not applicable"
STATUSES = (STATUS_MEETS, STATUS_POTENTIAL, STATUS_MISSING, STATUS_TESTING, STATUS_NA)

# Raw-material categories that make a criterion relevant. A criterion whose
# categories are absent from the formulation is Not applicable rather than
# passed - "we do not use colourants" is a different statement from "our
# colourants comply", and the report has to be able to tell them apart.
CATEGORY_COLOURANT = "Colorant / Pigment"
CATEGORY_BIOCIDE = "Biocide"
CATEGORY_ISOCYANATE = "Isocyanate"


# ---------------------------------------------------------------------------
# Seeding the versioned criteria set from the transcribed constant
# ---------------------------------------------------------------------------

def ensure_criteria_set(session):
    """The active criteria set, created from certipur_criteria.py if absent.

    Idempotent by (name, version). A later edition of the technical paper
    changes CRITERIA_SET_VERSION in that module, which makes a NEW set here and
    leaves every historical assessment pointing at the edition it was actually
    measured against. Nothing edits an existing set, ever."""
    existing = (
        session.query(CertipurCriteriaSet)
        .filter(
            CertipurCriteriaSet.name == cc.CRITERIA_SET_NAME,
            CertipurCriteriaSet.version == cc.CRITERIA_SET_VERSION,
        )
        .first()
    )
    if existing is not None:
        return existing

    # Anything older stops being the default for new assessments. It is not
    # deleted and its assessments are untouched.
    for older in session.query(CertipurCriteriaSet).filter(CertipurCriteriaSet.is_active.is_(True)).all():
        older.is_active = False

    cset = CertipurCriteriaSet(
        name=cc.CRITERIA_SET_NAME,
        version=cc.CRITERIA_SET_VERSION,
        source=cc.CRITERIA_SET_SOURCE,
        effective_from=None,
        is_active=True,
    )
    session.add(cset)
    session.flush()

    for order, c in enumerate(cc.CRITERIA):
        row = CertipurCriterion(
            criteria_set_id=cset.id,
            criterion_key=c["id"],
            section=c["section"],
            title=c["title"],
            requirement=c["requirement"],
            determination=c["determination"],
            assessment_method=c["method"],
            limit_text=c.get("limit"),
            test_method=c.get("test_method"),
            note=c.get("note"),
            sort_order=order,
        )
        session.add(row)
        session.flush()
        for name, cas, limit in c.get("substances") or ():
            session.add(
                CertipurCriterionSubstance(
                    criterion_id=row.id, name=name, cas_number=cas, individual_limit=limit
                )
            )
    return cset


def criteria_for(session, criteria_set):
    return (
        session.query(CertipurCriterion)
        .filter(CertipurCriterion.criteria_set_id == criteria_set.id)
        .order_by(CertipurCriterion.sort_order)
        .all()
    )


# ---------------------------------------------------------------------------
# Resolving what a foam grade is made of
# ---------------------------------------------------------------------------

def _active_recipe_version(foam_grade):
    versions = sorted(foam_grade.recipe_versions or [], key=lambda v: v.created_at or dt.datetime.min)
    if not versions:
        return None
    return next((v for v in versions if v.is_active), versions[-1])


def resolve_grade(session, foam_grade):
    """Everything the assessment needs about one foam grade, and everything it
    could not find.

    Returns a dict. `blocking` is set when there is no point continuing at all -
    which is exactly one case, no recipe version, because with no formulation
    there is nothing to assess. An unmapped component or a missing document is
    NOT blocking: those are the gaps the report exists to list."""
    out = {
        "foam_grade": foam_grade,
        "recipe_version": None,
        "components": [],
        "materials": [],
        "unmapped_components": [],
        "sds_by_material": {},
        "declarations_by_material": {},
        "materials_without_sds": [],
        "blocking": None,
    }

    version = _active_recipe_version(foam_grade)
    if version is None:
        out["blocking"] = (
            "%s has no recipe version, so there is no formulation to assess. Record the recipe "
            "first." % foam_grade.grade_name
        )
        return out
    out["recipe_version"] = version

    components = (
        session.query(RecipeComponent)
        .filter(RecipeComponent.recipe_version_id == version.id)
        .order_by(RecipeComponent.id)
        .all()
    )
    out["components"] = components
    if not components:
        out["blocking"] = (
            "Recipe version %s of %s has no components recorded, so there is nothing to screen."
            % (version.version_label, foam_grade.grade_name)
        )
        return out

    materials, unmapped = [], []
    for comp in components:
        material = session.get(RawMaterial, comp.raw_material_id) if comp.raw_material_id else None
        if material is None:
            # The component names a material in free text but is not linked to
            # the raw material master, so nothing about it can be looked up.
            unmapped.append(comp)
        else:
            materials.append(material)
    out["materials"] = materials
    out["unmapped_components"] = unmapped

    for material in materials:
        sds = document_store.current_document(session, material.id, DOCUMENT_TYPE_SDS)
        out["sds_by_material"][material.id] = sds
        if sds is None:
            out["materials_without_sds"].append(material)
        out["declarations_by_material"][material.id] = document_store.current_document(
            session, material.id, DOCUMENT_TYPE_DECLARATION
        )
    return out


def _substances_for(session, document):
    if document is None:
        return []
    return (
        session.query(RawMaterialSubstance)
        .filter(RawMaterialSubstance.document_id == document.id)
        .all()
    )


def _doc_reference(doc):
    if doc is None:
        return None
    bits = [doc.file_name or "(no file name)"]
    if doc.document_revision:
        bits.append("revision %s" % doc.document_revision)
    if doc.document_date:
        bits.append(doc.document_date.isoformat())
    return " · ".join(bits)


def _normalise_cas(value):
    """CAS numbers are written with and without separators and occasionally
    with stray spaces. Compared on digits alone so 108-01-0 and 108 01 0 and
    108010 are the same number, which they are."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())


# ---------------------------------------------------------------------------
# The per-criterion rules
# ---------------------------------------------------------------------------

def _result(status, rationale, action=None, evidence=None):
    return {"status": status, "rationale": rationale, "action": action, "evidence": evidence or []}


def _ev(evidence_type, detail, material=None, doc=None):
    return {
        "evidence_type": evidence_type,
        "raw_material_id": material.id if material is not None else None,
        "raw_material_name": material.name if material is not None else None,
        "document_id": doc.id if doc is not None else None,
        "document_reference": _doc_reference(doc),
        "detail": detail,
    }


def _measured(criterion):
    """The four laboratory criteria. Same answer every time, by design."""
    rationale = (
        "This limit is on finished foam and is determined only by an accredited laboratory "
        "testing foam samples. PI3 does not assess it. Limit: %s"
        % (criterion.limit_text or "see the CertiPUR technical requirements")
    )
    if criterion.test_method:
        rationale += "\nMethod: %s" % criterion.test_method
    action = (
        "Send foam samples for testing to one of the two accredited laboratories CertiPUR "
        "accepts: %s" % "; ".join(cc.ACCREDITED_LABORATORIES)
    )
    return _result(STATUS_TESTING, rationale, action)


def _hazard_classification(session, criterion, resolved):
    """Section 3.4 - the one criterion answered deterministically.

    Biocides are excluded here, not overlooked: section 3.5 says the 3.4 rule
    does not apply to them, so including a biocide would produce a failure the
    criteria do not support."""
    in_scope = [m for m in resolved["materials"] if (m.category or "") != CATEGORY_BIOCIDE]
    excluded = [m for m in resolved["materials"] if (m.category or "") == CATEGORY_BIOCIDE]

    if not in_scope:
        return _result(
            STATUS_NA,
            "Every raw material in this formulation is a biocide, and section 3.5 exempts "
            "biocides from this criterion.",
        )

    evidence, hits, missing = [], [], []
    for material in in_scope:
        doc = resolved["sds_by_material"].get(material.id)
        if doc is None:
            missing.append(material)
            evidence.append(_ev("None held", "No safety data sheet is held for this raw material.", material))
            continue
        prohibited = cc.prohibited_hazard_codes(doc.hazard_codes)
        if prohibited:
            hits.append((material, prohibited, doc))
            evidence.append(_ev(
                DOCUMENT_TYPE_SDS,
                "Carries %s - a CMR class 1a/1b or STOT SE 1 classification." % ", ".join(prohibited),
                material, doc,
            ))
        else:
            evidence.append(_ev(
                DOCUMENT_TYPE_SDS,
                "Hazard codes on the sheet: %s. None is CMR 1a/1b or STOT SE 1."
                % (doc.hazard_codes or "none printed"),
                material, doc,
            ))

    if excluded:
        evidence.append(_ev(
            "Formulation",
            "Excluded from this criterion under section 3.5 (biocides): %s"
            % ", ".join(m.name for m in excluded),
        ))

    if hits:
        # A prohibition takes precedence over a missing document: a known
        # failure is not softened by an incomplete file.
        named = "; ".join("%s (%s)" % (m.name, ", ".join(codes)) for m, codes, _ in hits)
        rationale = (
            "%d raw material%s carr%s a prohibited hazard classification: %s"
            % (len(hits), "" if len(hits) == 1 else "s", "ies" if len(hits) == 1 else "y", named)
        )
        if missing:
            rationale += (
                "\n%d further raw material%s has no safety data sheet and could not be checked: %s"
                % (len(missing), "" if len(missing) == 1 else "s", ", ".join(m.name for m in missing))
            )
        return _result(
            STATUS_POTENTIAL, rationale,
            "Substitute the named raw material, or ask the supplier whether a reclassification "
            "or a drop-in replacement is available. Europur can be asked for a transition period "
            "where no substitute exists.",
            evidence,
        )

    if missing:
        return _result(
            STATUS_MISSING,
            "%d of %d raw materials have no safety data sheet, so their hazard classification "
            "is unknown: %s" % (len(missing), len(in_scope), ", ".join(m.name for m in missing)),
            "Attach the safety data sheet for each named raw material on the Raw Materials "
            "Documents tab.",
            evidence,
        )

    return _result(
        STATUS_MEETS,
        "All %d raw materials in scope hold a current safety data sheet and none carries H340, "
        "H350, H360 or H370." % len(in_scope),
        None, evidence,
    )


def _substance_screen(session, criterion, resolved, applies_to_categories=None):
    """The CAS screen behind 3.3, 3.6 and 3.8, and the composition half of 3.1.

    Matches the criterion's own substance list against the composition read
    from each raw material's safety data sheet. A material with no sheet is a
    gap, because a screen that only sees the materials it happens to have
    documents for will always come back clean."""
    prohibited = {
        _normalise_cas(sub.cas_number): sub
        for sub in (criterion.substances or [])
        if sub.cas_number
    }

    materials = resolved["materials"]
    if applies_to_categories:
        materials = [m for m in materials if (m.category or "") in applies_to_categories]
        if not materials:
            return _result(
                STATUS_NA,
                "This formulation contains no %s, so the criterion does not apply."
                % " or ".join(c.lower() for c in applies_to_categories),
            )

    evidence, hits, missing, screened = [], [], [], 0
    for material in materials:
        doc = resolved["sds_by_material"].get(material.id)
        if doc is None:
            missing.append(material)
            evidence.append(_ev("None held", "No safety data sheet is held for this raw material.", material))
            continue
        subs = _substances_for(session, doc)
        if not subs:
            # A stored sheet whose composition could not be read is not the
            # same as a sheet that lists nothing, and must not be counted as a
            # clean screen.
            missing.append(material)
            evidence.append(_ev(
                DOCUMENT_TYPE_SDS,
                "The sheet is held but no composition could be read from its section 3, so this "
                "material could not be screened.",
                material, doc,
            ))
            continue
        screened += 1
        found = []
        for sub in subs:
            match = prohibited.get(_normalise_cas(sub.cas_number))
            if match is not None:
                found.append((sub, match))
        if found:
            hits.append((material, found, doc))
            evidence.append(_ev(
                DOCUMENT_TYPE_SDS,
                "Contains " + "; ".join(
                    "%s (CAS %s)%s" % (m.name, s.cas_number, (" at %s" % s.concentration) if s.concentration else "")
                    for s, m in found
                ),
                material, doc,
            ))
        else:
            evidence.append(_ev(
                DOCUMENT_TYPE_SDS,
                "%d substance%s screened, none matching this criterion."
                % (len(subs), "" if len(subs) == 1 else "s"),
                material, doc,
            ))

    if hits:
        named = "; ".join(
            "%s contains %s" % (m.name, ", ".join(s.name or s.cas_number for s, _ in found))
            for m, found, _ in hits
        )
        return _result(
            STATUS_POTENTIAL,
            "A substance named by this criterion appears in the formulation: %s" % named,
            "Confirm with the supplier whether the substance is intentionally added, and "
            "substitute the raw material if it is.",
            evidence,
        )

    if missing:
        return _result(
            STATUS_MISSING,
            "%d of %d raw materials could not be screened because no composition is held for "
            "them: %s" % (len(missing), len(materials), ", ".join(m.name for m in missing)),
            "Attach the safety data sheet for each named raw material, or record its composition, "
            "on the Raw Materials Documents tab.",
            evidence,
        )

    note = ""
    if criterion.criterion_key == "CP-3.3-PHTHALATE-PROHIBITION":
        # Said out loud because the source document says it: a clean screen
        # supports the declaration, it does not prove it.
        note = (
            " The substance list in the source document is stated to be non-exhaustive, so this "
            "supports the declaration rather than proving it."
        )
    return _result(
        STATUS_MEETS,
        "The composition of all %d screened raw materials was checked against this criterion's "
        "substance list and none was found.%s" % (screened, note),
        None, evidence,
    )


def _supplier_statement(session, criterion, resolved, categories, what_is_needed):
    """3.1's colour paste half, 3.2, 3.5 and 3.7 - the criteria a safety data
    sheet is structurally silent on, where CertiPUR names the supplier as the
    source. All that can be checked here is whether such a statement is held."""
    materials = [m for m in resolved["materials"] if (m.category or "") in categories]
    if not materials:
        return _result(
            STATUS_NA,
            "This formulation contains no %s, so the criterion does not apply."
            % " or ".join(c.lower() for c in categories),
        )

    evidence, missing = [], []
    for material in materials:
        doc = resolved["declarations_by_material"].get(material.id)
        if doc is None:
            missing.append(material)
            evidence.append(_ev("None held", "No supplier declaration is held for this raw material.", material))
        else:
            evidence.append(_ev(
                DOCUMENT_TYPE_DECLARATION,
                "A supplier declaration is held.", material, doc,
            ))

    if missing:
        return _result(
            STATUS_MISSING,
            "A supplier declaration is needed for %d raw material%s and is not held: %s. %s"
            % (len(missing), "" if len(missing) == 1 else "s",
               ", ".join(m.name for m in missing), what_is_needed),
            "Ask the supplier for the statement and attach it as a Supplier Declaration on the "
            "Raw Materials Documents tab.",
            evidence,
        )
    return _result(
        STATUS_MEETS,
        "A supplier declaration is held for all %d relevant raw material%s. The declaration's "
        "CONTENT is not read by PI3 - what is recorded is that the evidence exists and where."
        % (len(materials), "" if len(materials) == 1 else "s"),
        None, evidence,
    )


_HEAVY_METAL_STATEMENT = (
    "CertiPUR notes that cadmium, chromium and lead can be components of pigments, and directs "
    "the applicant to ask colour paste suppliers for the metal concentrations they deliver - a "
    "safety data sheet does not carry that."
)
_AZO_STATEMENT = (
    "REACH Restriction Entry 43 lists the aromatic amines an azo dye may RELEASE, not the dyes "
    "themselves, so only the colourant supplier can confirm compliance."
)
_BIOCIDE_STATEMENT = (
    "Only biocides authorised under Regulation 528/2012 for product type 9 may be used, which is "
    "a statement the biocide supplier makes."
)
_CHLOROBENZENE_STATEMENT = (
    "CertiPUR states the evidence may be obtained from the raw material supplier: a limit of "
    "20 ppm total chlorobenzenes in the diisocyanate."
)


def evaluate_criterion(session, criterion, resolved):
    """One criterion's result. The dispatch table for the whole pre-audit."""
    if criterion.determination == cc.DETERMINATION_MEASURED:
        return _measured(criterion)

    key = criterion.criterion_key
    if key == "CP-3.4-HAZARD-CLASSIFICATION":
        return _hazard_classification(session, criterion, resolved)
    if key == "CP-3.1-HEAVY-METALS":
        # Two halves, and the weaker one decides. The composition screen covers
        # a metal named directly in a sheet; the colour paste declaration
        # covers what a sheet does not carry. Passing on the screen alone would
        # be the exact mistake the source document warns about.
        screen = _substance_screen(session, criterion, resolved)
        paste = _supplier_statement(
            session, criterion, resolved, {CATEGORY_COLOURANT}, _HEAVY_METAL_STATEMENT
        )
        if screen["status"] == STATUS_POTENTIAL:
            return screen
        if paste["status"] == STATUS_MISSING:
            merged = dict(paste)
            merged["evidence"] = (screen["evidence"] or []) + (paste["evidence"] or [])
            merged["rationale"] = (
                "The composition screen found no heavy metal named by this criterion, but "
                + paste["rationale"][0].lower() + paste["rationale"][1:]
            )
            return merged
        if screen["status"] == STATUS_MISSING:
            return screen
        merged = dict(screen)
        merged["evidence"] = (screen["evidence"] or []) + (paste["evidence"] or [])
        merged["rationale"] = screen["rationale"] + " " + paste["rationale"]
        return merged
    if key == "CP-3.2-AZO-DYES":
        return _supplier_statement(session, criterion, resolved, {CATEGORY_COLOURANT}, _AZO_STATEMENT)
    if key == "CP-3.5-BIOCIDES":
        return _supplier_statement(session, criterion, resolved, {CATEGORY_BIOCIDE}, _BIOCIDE_STATEMENT)
    if key == "CP-3.7-CHLOROBENZENES":
        return _supplier_statement(
            session, criterion, resolved, {CATEGORY_ISOCYANATE}, _CHLOROBENZENE_STATEMENT
        )
    # 3.3 ortho-phthalates, 3.6 blowing agents, 3.8 brominated diphenyl ethers.
    return _substance_screen(session, criterion, resolved)


def assess(session, foam_grade, criteria_set=None):
    """The whole pre-audit for one foam grade, unsaved.

    Returns {"resolved": ..., "criteria_set": ..., "items": [...],
    "counts": {...}, "blocking": str or None}."""
    criteria_set = criteria_set or ensure_criteria_set(session)
    resolved = resolve_grade(session, foam_grade)
    criteria = criteria_for(session, criteria_set)

    if resolved["blocking"]:
        return {
            "resolved": resolved, "criteria_set": criteria_set, "items": [],
            "counts": {s: 0 for s in STATUSES}, "blocking": resolved["blocking"],
        }

    items = []
    for criterion in criteria:
        result = evaluate_criterion(session, criterion, resolved)
        items.append({"criterion": criterion, **result})

    counts = {s: 0 for s in STATUSES}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    return {
        "resolved": resolved, "criteria_set": criteria_set, "items": items,
        "counts": counts, "blocking": None,
    }


def save_assessment(session, outcome, company, plant, user=None, notes=None):
    """Write the pre-audit as an immutable snapshot. Does not commit."""
    resolved = outcome["resolved"]
    grade = resolved["foam_grade"]
    version = resolved["recipe_version"]
    cset = outcome["criteria_set"]
    counts = outcome["counts"]

    assessment = CertipurAssessment(
        company_id=company.id if company is not None else None,
        plant_id=plant.id if plant is not None else None,
        foam_grade_id=grade.id,
        recipe_version_id=version.id if version is not None else None,
        criteria_set_id=cset.id,
        company_name=company.name if company is not None else None,
        plant_name=plant.name if plant is not None else None,
        foam_grade_name=grade.grade_name,
        recipe_version_label=version.version_label if version is not None else None,
        criteria_set_label="%s %s" % (cset.name, cset.version),
        certipur_foam_family=grade.certipur_foam_family,
        assessed_by=(user or {}).get("display_name") or (user or {}).get("username"),
        assessed_by_user_id=(user or {}).get("id"),
        assessed_at=dt.datetime.utcnow(),
        count_total=len(outcome["items"]),
        count_meets=counts.get(STATUS_MEETS, 0),
        count_potential_issue=counts.get(STATUS_POTENTIAL, 0),
        count_evidence_missing=counts.get(STATUS_MISSING, 0),
        count_testing_required=counts.get(STATUS_TESTING, 0),
        count_not_applicable=counts.get(STATUS_NA, 0),
        blocking_reason=outcome.get("blocking"),
        notes=notes,
    )
    session.add(assessment)
    session.flush()

    for order, item in enumerate(outcome["items"]):
        criterion = item["criterion"]
        row = CertipurAssessmentItem(
            assessment_id=assessment.id,
            criterion_id=criterion.id,
            criterion_key=criterion.criterion_key,
            section=criterion.section,
            title=criterion.title,
            requirement=criterion.requirement,
            determination=criterion.determination,
            status=item["status"],
            rationale=item["rationale"],
            action=item.get("action"),
            sort_order=order,
        )
        session.add(row)
        session.flush()
        for ev in item.get("evidence") or []:
            session.add(CertipurAssessmentEvidence(item_id=row.id, **ev))
    return assessment


def assessments_for_grade(session, foam_grade_id):
    return (
        session.query(CertipurAssessment)
        .filter(CertipurAssessment.foam_grade_id == foam_grade_id)
        .order_by(CertipurAssessment.assessed_at.desc())
        .all()
    )


def readiness_headline(counts, blocking=None):
    """One sentence for the top of the page and the report.

    Leads with what is unresolved, because a summary that leads with the number
    passed reads as an endorsement of a formulation nobody has finished
    checking."""
    if blocking:
        return blocking
    issues = counts.get(STATUS_POTENTIAL, 0)
    missing = counts.get(STATUS_MISSING, 0)
    testing = counts.get(STATUS_TESTING, 0)
    meets = counts.get(STATUS_MEETS, 0)
    if issues:
        lead = "%d criterion%s indicate%s a compliance concern" % (
            issues, "" if issues == 1 else "s", "s" if issues == 1 else ""
        )
    elif missing:
        lead = "No compliance concern found, but %d criteri%s cannot be answered yet" % (
            missing, "on" if missing == 1 else "a"
        )
    else:
        lead = "Every criterion PI3 can assess is supported by evidence"
    return (
        "%s. %d met, %d awaiting evidence, %d requiring independent laboratory testing."
        % (lead, meets, missing, testing)
    )
