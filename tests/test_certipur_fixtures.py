"""Controlled UAT fixture suite for the CertiPUR Readiness CR.

Every case states its input and its expected outcome. Deterministic: no model
call, no network. Run with `python3 fixtures.py`.
"""
import sys, os, datetime as dt, hashlib
sys.path.insert(0, '.')
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
import db as m, certipur_assessment as ca, certipur_criteria as cc, document_store as ds
import regulatory_reference as rr

PASS, FAIL = [], []
def check(case, expect, got, detail=""):
    ok = expect == got
    (PASS if ok else FAIL).append(case)
    print(f'  [{"PASS" if ok else "FAIL"}] {case}\n         expected {expect!r}, got {got!r}' + (f'\n         {detail}' if detail else ''))

from sqlalchemy.pool import StaticPool
def fresh():
    """A private in-memory database per case, so no case can see another's rows."""
    eng = sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    m.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()

def load_reference(s, reference_type, rows, version='2026-08'):
    """A controlled reference set, as a loader would create it. Fixture data:
    the real sets are loaded from the official ECHA files."""
    rs = m.RegulatoryReferenceSet(reference_type=reference_type, name=reference_type,
                                  version=version, source='UAT fixture', file_hash='0'*64,
                                  parser_name='fixture', parser_version='v1',
                                  record_count=len(rows), is_active=True, loaded_by='uat')
    s.add(rs); s.flush()
    for n, row in enumerate(rows, start=1):
        cas, name, codes, entry = row[:4]
        applies = row[4] if len(row) > 4 else None
        s.add(m.RegulatoryReferenceRecord(reference_set_id=rs.id, cas_number=cas,
              cas_normalised=rr.normalise_cas(cas), substance_name=name,
              classification_codes=codes, entry_reference=entry,
              in_application_date=applies, source_row_number=n))
    s.commit()
    return rs


# A harmonised-classification reference with nothing prohibited in it, used
# wherever a case needs the 3.4 second limb to have RUN and found nothing.
CLP_CLEAN = [("9082-00-2", "Polyoxyalkylene triol", "H319", "Annex VI")]


def build(s, *, colour_doc=None, iso_doc=None, sds_hazards=None, extra_subs=None,
          declaration=False, biocide=False, blowing_cas=None, clp=None, azo=None,
          water=None, water_sds=False, unreadable_sds=False):
    """One UAT company, one grade, one recipe, three materials."""
    s.add(m.Company(id=1, name="UAT Foam Co", certipur_enabled=True))
    s.add(m.Plant(id=1, company_id=1, name="UAT Plant"))
    s.add(m.ProductFamily(id=1, plant_id=1, name="UAT Family"))
    s.add(m.FoamGrade(id=1, product_family_id=1, grade_name="UAT-SDE-01",
                      certipur_foam_family="Standard Ether foams (SDE)"))
    s.add(m.RecipeVersion(id=1, foam_grade_id=1, version_label="v1", is_active=True))
    mats = [(1, "UAT Polyol", "Polyol"), (2, "UAT TDI", "Isocyanate"),
            (3, "UAT Colour Paste", "Colorant / Pigment")]
    if biocide: mats.append((4, "UAT Biocide", "Biocide"))
    if water is not None or water_sds:
        mats.append((5, "UAT Water", "Blowing agent"))
    for i, n, c in mats:
        s.add(m.RawMaterial(id=i, company_id=1, name=n, category=c))
        s.add(m.RecipeComponent(recipe_version_id=1, raw_material_id=i,
                                raw_material_name=n, php=10.0))
    # one SDS per material, each with a readable composition
    for i, n, _c in mats:
        if i == 5 and not water_sds:
            continue   # the point of this material: no supplier issues a sheet
        doc = m.RawMaterialDocument(id=100 + i, raw_material_id=i, company_id=1,
            document_type=m.DOCUMENT_TYPE_SDS, file_name=f"{n} SDS.pdf",
            document_revision="1.0", document_date=dt.date(2026, 1, 1),
            hazard_codes=(sds_hazards or {}).get(i), is_current=True,
            supplier_name="UAT Supplier", extraction_status="Extracted")
        s.add(doc)
        s.add(m.RawMaterialSubstance(document_id=100 + i, name=f"{n} base", cas_number="9082-00-2"))
    if unreadable_sds:
        # a sheet that is held but whose section 3 could not be read
        s.query(m.RawMaterialSubstance).filter_by(document_id=101).delete()
    if water is not None:
        for name, cas in water:
            s.add(m.RawMaterialComposition(raw_material_id=5, company_id=1, name=name,
                  cas_number=cas, source=m.COMPOSITION_SOURCE_PUBLIC,
                  source_note="CAS Registry", recorded_by="uat", is_current=True))
    if blowing_cas:
        s.add(m.RawMaterialSubstance(document_id=101, name="Methylene chloride", cas_number=blowing_cas))
    for did, name, cas in (extra_subs or []):
        s.add(m.RawMaterialSubstance(document_id=did, name=name, cas_number=cas))
    def supplier(rmid, text, dtype=m.DOCUMENT_TYPE_DECLARATION):
        s.add(m.RawMaterialDocument(raw_material_id=rmid, company_id=1, document_type=dtype,
            file_name=f"supplier-{rmid}.pdf", document_revision="1.0",
            document_date=dt.date(2026, 2, 1), is_current=True, supplier_name="UAT Supplier",
            extracted_text=text, extraction_status="Extracted"))
    if colour_doc: supplier(3, colour_doc)
    if iso_doc: supplier(2, iso_doc, m.DOCUMENT_TYPE_COA)
    if biocide:
        supplier(4, "This biocide is authorised under Regulation 528/2012 for product type 9 (PT9).")
    if declaration:
        s.add(m.CompanyDocument(company_id=1, document_type=m.DOCUMENT_TYPE_APPLICANT_DECLARATION,
            file_name="CertiPUR applicant declaration.pdf", document_date=dt.date(2026, 3, 1),
            signed_by="A. Director", is_current=True))
    s.commit()
    if clp is not None:
        load_reference(s, rr.REFERENCE_HARMONISED_CLP, clp)
    if azo is not None:
        load_reference(s, rr.REFERENCE_RESTRICTED_AZO, azo)
    return s.get(m.FoamGrade, 1), s.get(m.Company, 1)

CLEAN_COLOUR = ("Colour paste analysis. Cadmium < 1 ppm. Lead < 1 ppm. Chromium < 1 ppm. "
                "This azo colourant does not release any of the aromatic amines restricted "
                "under REACH Restriction Entry 43 (Regulation 1907/2006).")
# The same statement with the word "azo" removed - a real supplier phrasing that
# names only the restricted amines. The rule requires the substance class to be
# named, so this is reported as a gap rather than accepted.
COLOUR_NO_AZO = ("Colour paste analysis. Cadmium < 1 ppm. Lead < 1 ppm. Chromium < 1 ppm. "
                 "The colourant does not release any of the aromatic amines restricted "
                 "under REACH Restriction Entry 43.")
CLEAN_ISO = "Certificate of analysis. Total chlorobenzenes 11 ppm. Assay 99.6%."

def status(grade, company, s, section):
    out = ca.assess(s, grade, company=company)
    for it in out["items"]:
        if it["criterion"].section == section:
            return it["status"], it["rationale"], out
    raise KeyError(section)

print("=" * 78)
print("A. THE FIVE STATUSES")
print("=" * 78)

print("\nA1. Meets requirement - every declared criterion, full evidence + declaration")
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True, biocide=True, clp=CLP_CLEAN)
out = ca.assess(s, g, company=co)
for it in out["items"]:
    if it["criterion"].determination == cc.DETERMINATION_DECLARED:
        check(f'{it["criterion"].section} {it["criterion"].title[:34]}', "Meets requirement", it["status"], it["rationale"][:150])

print("\nA2. Potential issue - prohibited hazard classification (3.4)")
s = fresh(); g, co = build(s, sds_hazards={2: "H315,H350,H319"}, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True, clp=CLP_CLEAN)
st, why, _ = status(g, co, s, "3.4"); check("3.4 with H350 on the isocyanate SDS", "Potential issue", st, why[:200])

print("\nA3. Potential issue - prohibited substance in a composition (3.6)")
s = fresh(); g, co = build(s, blowing_cas="75-09-2", colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True)
st, why, _ = status(g, co, s, "3.6"); check("3.6 with methylene chloride CAS 75-09-2", "Potential issue", st, why[:200])

print("\nA4. Potential issue - a figure over the limit (3.7)")
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc="Certificate of analysis. Total chlorobenzenes 34 ppm.", declaration=True)
st, why, _ = status(g, co, s, "3.7"); check("3.7 with 34 ppm against a 20 ppm limit", "Potential issue", st, why[:200])

print("\nA5. Evidence missing - a document that does not address the question")
s = fresh(); g, co = build(s, colour_doc="This product is RoHS compliant and contains no restricted substances.", iso_doc=CLEAN_ISO, declaration=True)
st, why, _ = status(g, co, s, "3.1"); check("3.1 with an unrelated colourant document", "Evidence missing", st, why[:200])

print("\nA5b. Evidence missing - a statement that never names the substance class")
s = fresh(); g, co = build(s, colour_doc=COLOUR_NO_AZO, iso_doc=CLEAN_ISO, declaration=True)
st, why, _ = status(g, co, s, "3.2"); check("3.2 with a statement that omits the word azo", "Evidence missing", st, why[:190])
st, why, _ = status(g, co, s, "3.1"); check("3.1 unaffected - the same document still carries the metals", "Meets requirement", st, why[:150])

print("\nA6. Evidence missing - a clean screen with no applicant declaration")
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=False)
st, why, _ = status(g, co, s, "3.2"); check("3.2 clean, declaration absent", "Evidence missing", st, why[:200])
st, why, _ = status(g, co, s, "3.7"); check("3.7 clean, declaration absent (not declaration-backed)", "Meets requirement", st, why[:150])

print("\nA7. Testing required - always the four measured criteria")
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True, biocide=True, clp=CLP_CLEAN)
out = ca.assess(s, g, company=co)
meas = [it["status"] for it in out["items"] if it["criterion"].determination == cc.DETERMINATION_MEASURED]
check("2.1-2.4 with complete declared evidence", ["Testing required"] * 4, meas)

print("\nA8. Not applicable - the formulation excludes the criterion")
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True, biocide=False)
st, why, _ = status(g, co, s, "3.5"); check("3.5 with no biocide in the recipe", "Not applicable", st, why[:150])

print("\nA9. Section 3.4 - the supplier self-classification, and nothing else")
# Charlie's scope instruction of 21 Aug 2026 reversed the harmonised limb built
# at v2.25.0. CertiPUR reads the classification the SUPPLIER states. Annex VI to
# CLP is a REACH Readiness matter, so CertiPUR stays usable without a REACH
# subscription and never reports a regulatory dataset as its own evidence gap.
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True)
st, why, _ = status(g, co, s, "3.4")
check("clean sheets, no regulatory reference loaded anywhere", "Meets requirement", st, why[:230])
check("the rationale credits the supplier, not a regulatory list", True,
      "supplier" in why.lower() and "harmonised" not in why.lower(), why[:230])

s = fresh(); g, co = build(s, sds_hazards={2: "H350"}, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO,
                           declaration=True)
st, why, _ = status(g, co, s, "3.4")
check("an SDS carrying H350 is still a finding", "Potential issue", st, why[:200])

# The reversal's real test: a loaded harmonised reference that WOULD have raised
# a finding must now have no effect at all on the CertiPUR result.
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True,
                           clp=[("9082-00-2", "Polyoxyalkylene triol", "H350", "Annex VI 601-001-00-0")])
st, why, _ = status(g, co, s, "3.4")
check("a loaded Annex VI hit does NOT change the CertiPUR result", "Meets requirement", st, why[:230])
check("and Annex VI is not named in the rationale", False, "Annex VI" in why, why[:230])

print("\nA10. Section 3.2 - supplier evidence and the declaration")
# The Entry 43 dataset step built at v2.25.0 is gone. Entry 43 restricts the
# amines a colourant may RELEASE, which only the supplier can attest.
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True,
                           azo=[("9082-00-2", "A restricted azo colourant", "", "Entry 43 Appendix 9")])
st, why, _ = status(g, co, s, "3.2")
check("a loaded Entry 43 hit does NOT change the CertiPUR result", "Meets requirement", st, why[:200])
# Naming Entry 43 is correct and expected here - Charlie's own instruction says
# the supplier confirms the colourant "does not release the aromatic amines
# restricted under REACH Restriction Entry 43". What must not happen is CertiPUR
# claiming it looked the colourant up in the dataset, or reporting the dataset
# as missing.
check("the conclusion is credited to supplier evidence", True,
      "Supplier evidence" in why, why[:200])
check("no dataset lookup is claimed", True,
      ("screened against" not in why) and ("not loaded" not in why), why[:200])

s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True)
st, why, _ = status(g, co, s, "3.2")
check("supplier statement and declaration clear it", "Meets requirement", st, why[:200])

s = fresh(); g, co = build(s, colour_doc="This product is RoHS compliant.", iso_doc=CLEAN_ISO,
                           declaration=True)
st, why, _ = status(g, co, s, "3.2")
check("no supplier statement is still a gap", "Evidence missing", st, why[:200])

print("\nA11. CAS numbers are still validated where they are used")
check("a valid CAS passes the check digit", True, rr.cas_check_digit_ok("584-84-9"))
check("a transposed CAS is rejected", False, rr.cas_check_digit_ok("584-84-8"))
check("free text never normalises to a CAS", None, rr.normalise_cas("see section 3"))
check("leading zeros are stripped", "584-84-9", rr.normalise_cas("0000584-84-9"))

print("\nA12. The controlled identity route - composition only, never classification")
WATER = [("Water", "7732-18-5")]
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True,
                           water=WATER)
st, why, _ = status(g, co, s, "3.3")
check("a composition criterion screens the controlled record", "Meets requirement", st, why[:200])
st, why, _ = status(g, co, s, "3.4")
check("3.4 does NOT accept the controlled identity route", "Evidence missing", st, why[:260])
check("and 3.4 names the material it could not answer", True, "UAT Water" in why, why[:260])
check("the reason given is the missing sheet, not a missing dataset", True,
      "safety data sheet" in why and "Annex VI" not in why, why[:260])

s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True,
                           water=None)
s.add(m.RawMaterial(id=5, company_id=1, name="UAT Water", category="Blowing agent"))
s.add(m.RecipeComponent(recipe_version_id=1, raw_material_id=5, raw_material_name="UAT Water", php=3.6))
s.commit()
st, why, _ = status(g, co, s, "3.3")
check("neither a sheet nor a record is still a gap", "Evidence missing", st, why[:200])
st, why, _ = status(g, co, s, "3.4")
check("3.4 with neither is still a gap", "Evidence missing", st, why[:200])

s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True,
                           water=WATER, unreadable_sds=True)
st, why, _ = status(g, co, s, "3.3")
check("an unreadable sheet does NOT fall through to the controlled record",
      "Evidence missing", st, why[:200])

print("\nA12b. CertiPUR is independent of every REACH dataset")
# The point of the reversal: a company with no REACH Readiness subscription and
# nothing loaded must get exactly the same CertiPUR answer as one with the full
# regulatory library in place.
def _all_statuses(sess, grade, comp):
    return [(it["criterion"].criterion_key, it["status"]) for it in ca.assess(sess, grade, company=comp)["items"]]

s1 = fresh(); g1, c1 = build(s1, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True,
                             water=WATER)
s2 = fresh(); g2, c2 = build(s2, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=True,
                             water=WATER,
                             clp=[("9082-00-2", "Polyoxyalkylene triol", "H350", "Annex VI"),
                                  ("7732-18-5", "Water", "H350", "Annex VI")],
                             azo=[("9082-00-2", "A restricted azo colourant", "", "Entry 43")])
check("every criterion returns the same status with and without the references",
      _all_statuses(s1, g1, c1), _all_statuses(s2, g2, c2))

src_all = open('certipur_assessment.py').read() + open('certipur_criteria.py').read()
check("the CertiPUR engine cannot read the reference tables", 0,
      src_all.count("regulatory_reference") + src_all.count("RegulatoryReference"))
for term in ("Candidate List", "Annex XIV", "Annex XVII"):
    check(f'the engine never cites {term}', 0, src_all.count(term))

print("\n" + "=" * 78)
print("B. HAZARD-CODE FAMILY MATCHING (section 3.4)")
print("=" * 78)
for code, expect in [("H340","hit"),("H350","hit"),("H350i","hit"),("H360","hit"),("H360D","hit"),
                     ("H360FD","hit"),("H360Df","hit"),("H370","hit"),("h350","hit"),
                     ("H341","clear"),("H351","clear"),("H361","clear"),("H371","clear"),
                     ("H319","clear"),("H335","clear"),
                     ("H3501","hit"),("H350-i","hit")]:
    got = "hit" if cc.prohibited_hazard_codes([code]) else "clear"
    check(f'prohibited_hazard_codes(["{code}"])', expect, got)
check('reported as printed, not re-cased', ["H360Df"], cc.prohibited_hazard_codes(["H360Df"]))
check('a malformed suffix still matches - fails safe, does not pass a prohibited material',
      ["H350-i"], cc.prohibited_hazard_codes(["H350-i"]))
check('comma-joined string form', ["H350"], cc.prohibited_hazard_codes("H315,H350,H319"))

print("\n" + "=" * 78)
print("C. SNAPSHOT IMMUTABILITY")
print("=" * 78)

print("\nC1. Evidence added after assessment 1 does not change assessment 1")
s = fresh(); g, co = build(s, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO, declaration=False)
a1 = ca.save_assessment(s, ca.assess(s, g, company=co), co, s.get(m.Plant, 1), user={"display_name": "UAT operator", "username": "uat"}); s.commit()
a1_counts = (a1.count_meets, a1.count_potential_issue, a1.count_evidence_missing, a1.count_testing_required, a1.count_not_applicable)
a1_items = {i.section: i.status for i in s.query(m.CertipurAssessmentItem).filter_by(assessment_id=a1.id)}
s.add(m.CompanyDocument(company_id=1, document_type=m.DOCUMENT_TYPE_APPLICANT_DECLARATION,
      file_name="declaration.pdf", document_date=dt.date(2026, 4, 1), signed_by="A. Director", is_current=True))
s.commit()
a2 = ca.save_assessment(s, ca.assess(s, g, company=co), co, s.get(m.Plant, 1), user={"display_name": "UAT operator", "username": "uat"}); s.commit()
s.expire_all(); a1r = s.get(m.CertipurAssessment, a1.id)
check("assessment 1 counts unchanged after the declaration is filed",
      a1_counts, (a1r.count_meets, a1r.count_potential_issue, a1r.count_evidence_missing, a1r.count_testing_required, a1r.count_not_applicable))
check("assessment 1 item statuses unchanged", a1_items,
      {i.section: i.status for i in s.query(m.CertipurAssessmentItem).filter_by(assessment_id=a1.id)})
check("assessment 2 sees the declaration (3.2)", "Meets requirement",
      s.query(m.CertipurAssessmentItem).filter_by(assessment_id=a2.id, section="3.2").one().status)
check("assessment 1 still reads Evidence missing on 3.2", "Evidence missing", a1_items["3.2"])
check("two assessments of the same foam grade exist", 2,
      s.query(m.CertipurAssessment).filter_by(foam_grade_id=g.id).count())

print("\nC2. A new recipe version does not change an earlier assessment")
s2 = s
old_rv = s2.get(m.RecipeVersion, 1); old_rv.is_active = False
s2.add(m.RecipeVersion(id=2, foam_grade_id=1, version_label="v2", is_active=True))
for c in s2.query(m.RecipeComponent).filter_by(recipe_version_id=1).all():
    s2.add(m.RecipeComponent(recipe_version_id=2, raw_material_id=c.raw_material_id,
                             raw_material_name=c.raw_material_name, php=c.php))
s2.commit()
a3 = ca.save_assessment(s2, ca.assess(s2, g, company=co), co, s2.get(m.Plant, 1), user={"display_name": "UAT operator", "username": "uat"}); s2.commit()
check("assessment 1 still names recipe version v1", "v1", s2.get(m.CertipurAssessment, a1.id).recipe_version_label)
check("assessment 3 names recipe version v2", "v2", s2.get(m.CertipurAssessment, a3.id).recipe_version_label)

print("\nC3. Every assessment names the criteria-set version it used")
sets = {a.criteria_set_id for a in s2.query(m.CertipurAssessment).all()}
check("all three assessments reference a criteria set", 1, len(sets))
cs = s2.get(m.CertipurCriteriaSet, list(sets)[0])
check("criteria-set version recorded", cc.CRITERIA_SET_VERSION, cs.version_label if hasattr(cs,"version_label") else cs.version)
check("criteria-set is not editable in place - 12 criteria seeded once", 12,
      s2.query(m.CertipurCriterion).filter_by(criteria_set_id=cs.id).count())
ca.ensure_criteria_set(s2); s2.commit()
check("re-seeding does not create a second set", 1, s2.query(m.CertipurCriteriaSet).count())

print("\n" + "=" * 78)
print("C4. THE REPORT DOES NOT CLAIM MORE THAN THE MODULE CHECKS")
print("=" * 78)
# Charlie, 21 Aug 2026: the pre-audit must not say the foam "in principle
# complies with the criteria of CertiPUR". That phrase covers the whole of
# CertiPUR, and this module deliberately does not screen against regulatory
# lists. These checks exist so the claim cannot creep back in later.
import reports as _rp
_stmt = _rp.CERTIPUR_ASSESSMENT_STATEMENT
check("the pre-audit does not claim compliance in principle", False,
      "in principle complies" in _stmt, _stmt[:120])
check("it names what the module evaluated", True,
      "evidence checks covered by this module" in _stmt)
check("it says regulatory-list screening is out of scope", True,
      "outside the scope of CertiPUR Readiness" in _stmt)
check("it says the report is readiness, not compliance", True,
      "not a conclusion about compliance with CertiPUR as a whole" in _stmt)

# The same over-claim in the 3.4 action text: a written supplier statement may
# be stored and assessed, but PI3 must not assert CertiPUR accepts it.
s34 = fresh(); g34, co34 = build(s34, colour_doc=CLEAN_COLOUR, iso_doc=CLEAN_ISO,
                                 declaration=True, water=[("Water", "7732-18-5")])
_out34 = ca.assess(s34, g34, company=co34)
_item34 = next(i for i in _out34["items"] if i["criterion"].section == "3.4")
_st, _action = _item34["status"], _item34.get("action") or ""
check("3.4 is Evidence missing for a material with no sheet", "Evidence missing", _st)
check("the action does not call a statement acceptable evidence", False,
      "is acceptable evidence for this criterion" in _action, _action[:200])
check("the action still offers the route", True,
      "supporting evidence" in _action, _action[:200])
check("and defers the question to EUROPUR", True,
      "EUROPUR" in _action, _action[:200])

print("\n" + "=" * 78)
print("D. INTERNAL TEST EVIDENCE IS STRUCTURALLY SEPARATE")
print("=" * 78)
src = open('certipur_assessment.py').read() + open('certipur_criteria.py').read()
for name in ("PhysicalPropertyResult", "physical_property_results", "QualityObservation", "ProductionRun"):
    check(f'the engine cannot read {name}', 0, src.count(name))
check("the four measured criteria are the only Testing required results", 4,
      len([c for c in cc.CRITERIA if c["determination"] == cc.DETERMINATION_MEASURED]))
check("two accredited laboratories are named", 2, len(cc.ACCREDITED_LABORATORIES))

print("\n" + "=" * 78)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:"); [print("  -", f) for f in FAIL]
print("=" * 78)
sys.exit(1 if FAIL else 0)
