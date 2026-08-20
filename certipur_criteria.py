# -*- coding: utf-8 -*-
"""The CertiPUR(TM) requirement set, transcribed from the source document.

SOURCE
------
"CertiPUR(TM) Label for Flexible Polyurethane Foams - Application Form and
Technical Requirements", EUROPUR aisbl, edition 2026. Filed as
CertiPUR_Technical_Paper_2026.pdf in PI3 Flexible Foam Development Docs.
Section numbers below are that document's own numbering, so any statement here
can be checked against it directly.

WHY THIS IS A MODULE AND NOT A TABLE
------------------------------------
It is both. This module is the SEED. The controlled, versioned copy lives in
the database (see the CR of 19 Aug 2026, section 4: every saved assessment
must reference the exact criteria-set version used, and a historical
assessment must keep referencing its original version even after the criteria
change). Loading this constant creates one criteria-set row and its criteria;
a later edition of the technical paper becomes a NEW set, never an edit of the
old one.

Held as a constant for the same reason db.CUSTOMER_TYPES and
quality_standards.py are: this is stable published industry vocabulary owned
by a third party, not tenant data. No customer edits it.

THE ONE DISTINCTION THE WHOLE PRE-AUDIT RESTS ON
------------------------------------------------
The source document sets its criteria in two categories, and says so itself
(page 2, "How CertiPUR works"):

  MEASURED     Measurable upper limits on FINISHED FOAM. Determined only by
               an accredited laboratory, from foam samples, using the test
               method the document specifies. PI3 cannot assess these from
               plant data - not partially, not indicatively. They are always
               "Testing required". What PI3 can usefully do is state the
               limit, the method and which laboratory, so the customer knows
               what they are buying before they buy it.

  DECLARED     Prohibited substances, for which the applicant signs a
               declaration (application form, section 6) that they are not
               intentionally added. No laboratory is involved. These are
               answerable from the formulation, the raw materials and their
               safety data sheets - which is exactly the evidence PI3 holds.

That split is the reason a pre-audit is worth anything at all: the declared
half is the half a foam producer can get wrong on paper, before spending money
on a test that only measures the other half.

Section 3.4 deserves particular attention because it is written by reference
to the SDS itself: a raw material whose supplier self-classifies it as CMR
1a/1b (H340, H350, H360) or STOT SE 1 (H370) may not be intentionally used
"from the moment this appear on the SDS". The criterion IS an SDS check. That
makes it deterministic rather than a matter of interpretation, and it is the
single highest-value check in the set.

WHAT IS DELIBERATELY NOT ENCODED HERE
-------------------------------------
The sampling procedure (section III.1), the laboratory test methods in detail,
the label terms of use (chapter IV) and the commercial terms (chapter II).
Those govern the formal application, not a readiness assessment, and copying
them here would create a second source of truth for something PI3 does not
decide. The test-method text below is the short identification only, so a
report can name the method the customer's laboratory will use.
"""

# --- vocabulary --------------------------------------------------------------

DETERMINATION_MEASURED = "measured"      # accredited laboratory only
DETERMINATION_DECLARED = "declared"      # applicant declaration, pre-auditable

# How PI3 reaches a conclusion on a DECLARED criterion. Recorded on each
# criterion so the assessment can say WHY it concluded what it concluded, which
# the CR requires for every supported result (section 7).
METHOD_LAB_TEST = "Independent laboratory test"
METHOD_SDS_HAZARD = "SDS hazard classification review"
METHOD_SUBSTANCE_SCREEN = "Formulation and SDS substance screen"
METHOD_SUPPLIER_DECLARATION = "Supplier declaration review"
METHOD_APPLICANT_DECLARATION = "Applicant declaration"

CRITERIA_SET_NAME = "CertiPUR flexible polyurethane foam requirements"
CRITERIA_SET_VERSION = "2026"
CRITERIA_SET_SOURCE = (
    "CertiPUR(TM) Label for Flexible Polyurethane Foams - Application Form and "
    "Technical Requirements, EUROPUR aisbl, edition 2026"
)

# Foam families exactly as the application form lists them (form section 4).
# An applicant ticks the ones they are applying for, and every family applied
# for has to be tested. Held here so the readiness page can ask which family a
# grade belongs to rather than inventing its own vocabulary.
FOAM_FAMILIES = (
    "Standard Ether foams (SDE)",
    "High Resilience foams (HR)",
    "Combustion Modified High Resilience foams (CMHR)",
    "Combustion Modified Ether foams (CME)",
    "Visco-Elastic foams (VE)",
    "Combustion Modified Visco-Elastic foams (CMVE)",
    "Flame Retardant Foams containing Brominated Flame Retardants",
)

# The only two laboratories whose results CertiPUR accepts (chapter II,
# "Accredited laboratories"). Results from anywhere else are not accepted, so
# the report names them rather than leaving the customer to find out later.
ACCREDITED_LABORATORIES = (
    "Eurofins Product Testing A/S - Smedeskovvej 38, DK-8464 Galten, Denmark",
    "TUV Rheinland LGA Products GmbH - Tillystrasse 2, D-90431 Nurnberg, Germany",
)

LABEL_VALIDITY_YEARS = 3
CONTROL_TEST_FREQUENCY = "At least once a year, on samples selected by EUROPUR."


# --- the criteria ------------------------------------------------------------
# Each entry:
#   id             stable identifier - never renumbered, even if a later edition
#                  moves the section. Historical assessments reference this.
#   section        the source document's own section number
#   title          short label for a table row
#   requirement    the controlled requirement wording
#   determination  DETERMINATION_MEASURED / DETERMINATION_DECLARED
#   method         how PI3 assesses it (or METHOD_LAB_TEST when it cannot)
#   limit          the limit as the document states it, verbatim in substance
#   test_method    short identification of the laboratory method, where one applies
#   substances     ((name, cas_or_None, individual_limit_or_None), ...)
#   note           anything the assessor needs that the requirement text omits

CRITERIA = (
    # ---- Section 2: measurable limits. Laboratory only. --------------------
    {
        "id": "CP-2.1-ORGANOTIN",
        "section": "2.1",
        "title": "Organotin compounds",
        "requirement": (
            "Finished foam must not exceed the individual limits for MBT, DBT and TBT, "
            "nor the sum limit across all eight organotin species."
        ),
        "determination": DETERMINATION_MEASURED,
        "method": METHOD_LAB_TEST,
        "limit": "MBT <25 ppm, DBT <15 ppm, TBT <5 ppm, sum of all eight <50 ppm",
        "test_method": (
            "Extraction in an ultrasonic bath, derivatisation with sodium "
            "tetraethylborate, GC-MS in SIM mode"
        ),
        "substances": (
            ("n-butyltin (Monobutyltin; MBT)", None, "<25 ppm"),
            ("di-n-butyltin (Dibutyltin; DBT)", None, "<15 ppm"),
            ("tri-n-butyltin (Tributyltin; TBT)", None, "<5 ppm"),
            ("tetra-n-butyltin (Tetrabutyltin; TeBT)", None, None),
            ("n-octyltin (Monooctyltin; MOT)", None, None),
            ("di-n-octyltin (Dioctyltin; DOT)", None, None),
            ("tri-cyclohexyltin (TCyT)", None, None),
            ("tri-phenyltin (TPhT)", None, None),
        ),
        "note": (
            "Five of the eight carry no individual limit and are still counted in the "
            "sum, so a formulation can pass every individual limit and fail the sum."
        ),
    },
    {
        "id": "CP-2.2-PHTHALATE-LIMIT",
        "section": "2.2",
        "title": "Ortho-phthalate plasticisers (measured)",
        "requirement": "Finished foam must not exceed a sum total of 100 ppm across the listed ortho-phthalates.",
        "determination": DETERMINATION_MEASURED,
        "method": METHOD_LAB_TEST,
        "limit": "Sum total 100 ppm (limit of quantification 50 ppm)",
        "test_method": "Soxhlet extraction with dichloromethane, then GC/MS or HPLC/UV",
        "substances": (
            ("Bis(2-ethylhexyl) phthalate (DEHP)", "117-81-7", None),
            ("Dibutyl phthalate (DBP)", "84-74-2", None),
            ("Benzyl butyl phthalate (BBP)", "85-68-7", None),
            ("Diisobutyl phthalate (DIBP)", "84-69-5", None),
            ("Di-n-pentyl phthalate (DNPP)", "131-18-0", None),
            ("Di-n-hexyl phthalate (DNHP)", "84-75-3", None),
            ("Dicyclohexyl phthalate (DCHP)", "84-61-7", None),
            ("Di-n-octyl phthalate (DNOP)", "117-84-0", None),
            ("Diisononyl phthalate (DINP)", "28553-12-0", None),
            ("Diisodecyl phthalate (DIDP)", "26761-40-0", None),
            ("Diisopentyl phthalate (DIPP)", "605-50-5", None),
            ("Bis(methylcyclohexyl) phthalate (MDCHP)", "27987-25-3", None),
            ("Bis(2-propylheptyl) phthalate (DPHP)", "53306-54-0", None),
        ),
        "note": (
            "The CertiPUR sum limit is more stringent than REACH Restriction entry 51, "
            "both in the number of substances counted and in the total."
        ),
    },
    {
        "id": "CP-2.3-TDA-MDA",
        "section": "2.3",
        "title": "TDA and MDA",
        "requirement": "Finished foam must not exceed the limits for 2,4-TDA, 4,4'-MDA and the MDA sum.",
        "determination": DETERMINATION_MEASURED,
        "method": METHOD_LAB_TEST,
        "limit": "2,4-TDA <= 5.0 ppm; 4,4'-MDA <= 5.0 ppm; sum of 2,2'-, 2,4'- and 4,4'-MDA <= 15.0 ppm",
        "test_method": "Four repeat extractions with 1% aqueous acetic acid, then HPLC-MS",
        "substances": (
            ("2,4 Toluenediamine (2,4-TDA)", "95-80-7", "<= 5.0 ppm"),
            ("4,4' Diaminodiphenylmethane (4,4' MDA)", "101-77-9", "<= 5.0 ppm"),
            ("2,2'-MDA", "6582-52-1", None),
            ("2,4'-MDA", "1208-52-2", None),
        ),
        "note": "These are isocyanate hydrolysis products, so they are a process outcome rather than an ingredient choice.",
    },
    {
        "id": "CP-2.4-VOC",
        "section": "2.4",
        "title": "Volatile organic compound emissions",
        "requirement": "Emissions from the finished foam must not exceed the stated chamber limits.",
        "determination": DETERMINATION_MEASURED,
        "method": METHOD_LAB_TEST,
        "limit": (
            "Formaldehyde 15; Toluene 100; Styrene 5; each other CMR class 1a or 1b 5; "
            "sum of all CMR class 1a and 1b (including formaldehyde) 40; aromatic "
            "hydrocarbons 500; total organic volatiles 500 - all in ug/m3"
        ),
        "test_method": (
            "Emission chamber to ISO 16000-9 and ISO 16000-11, conditioned 3 days at "
            "23 C / 50% RH; sampling at 72 +/- 2 h on Tenax TA and DNPH; TD-GC-MS to "
            "ISO 16000-6; formaldehyde and acetaldehyde by HPLC/UV to ISO 16000-3"
        ),
        "substances": (
            ("Formaldehyde", "50-00-0", "15 ug/m3"),
            ("Toluene", "108-88-3", "100 ug/m3"),
            ("Styrene", "100-42-5", "5 ug/m3"),
        ),
        "note": (
            "The CMR classes referred to are substances with a harmonised and "
            "self-classification under CLP Regulation 1272/2008. This is the criterion "
            "most sensitive to the sampling and sealing procedure, so a failure here is "
            "not always a formulation failure."
        ),
    },

    # ---- Section 3: prohibited substances. Declaration - pre-auditable. ----
    {
        "id": "CP-3.1-HEAVY-METALS",
        "section": "3.1",
        "title": "Heavy metals",
        "requirement": (
            "The applicant declares that no substance is intentionally added that may, to "
            "its knowledge, result in the foam containing heavy metals above the stated "
            "concentrations."
        ),
        "determination": DETERMINATION_DECLARED,
        "method": METHOD_SUBSTANCE_SCREEN,
        "limit": "Sb 100, As 25, Cd 75, Cr 60, Pb 90, Hg 60, Se 500 - all ppm of foam",
        "test_method": None,
        "substances": (
            ("Antimony (Sb)", "7440-36-0", "100 ppm"),
            ("Arsenic (As)", "7440-38-2", "25 ppm"),
            ("Cadmium (Cd)", "7440-43-9", "75 ppm"),
            ("Chromium (Cr)", "7440-47-3", "60 ppm"),
            ("Lead (Pb)", "7439-92-1", "90 ppm"),
            ("Mercury (Hg)", "7439-97-6", "60 ppm"),
            ("Selenium (Se)", "7782-49-2", "500 ppm"),
        ),
        "note": (
            "The pure metals are unlikely formulation components. Cadmium, chromium and "
            "lead can be components of PIGMENTS, so the source document directs the "
            "applicant to ask colour paste suppliers for the concentrations they deliver. "
            "A pre-audit that ignores colour pastes will pass this criterion wrongly."
        ),
    },
    {
        "id": "CP-3.2-AZO-DYES",
        "section": "3.2",
        "title": "Azocolourants and azodyes",
        "requirement": (
            "Azocolourants and azodyes restricted under REACH (EC 1907/2006) Restriction "
            "Entry 43 must not be used in flexible PU foam."
        ),
        "determination": DETERMINATION_DECLARED,
        "method": METHOD_SUBSTANCE_SCREEN,
        "limit": "Not used",
        "test_method": None,
        "substances": (),
        "note": (
            "Restriction Entry 43 lists the aromatic amines these dyes may release, not "
            "the dyes themselves, so this is answered from the colourant supplier's own "
            "REACH statement rather than by matching a CAS number in the recipe."
        ),
    },
    {
        "id": "CP-3.3-PHTHALATE-PROHIBITION",
        "section": "3.3",
        "title": "Ortho-phthalates not intentionally added",
        "requirement": "The applicant declares that ortho-phthalates are not intentionally added to the foam formulation.",
        "determination": DETERMINATION_DECLARED,
        "method": METHOD_SUBSTANCE_SCREEN,
        "limit": "Not intentionally added",
        "test_method": None,
        "substances": (
            ("1,2-Benzenedicarboxylic acid, di-C6-10-alkyl esters", "68515-51-5", None),
            ("1,2-Benzenedicarboxylic acid, benzyl isononyl alkyl esters", None, None),
            ("1,2-Benzenedicarboxylic acid, di-C8-10-alkyl esters", "71662-46-9", None),
            ("1,2-Benzenedicarboxylic acid, di-C8-10-branched alkyl esters, C9-rich", "68515-48-0", None),
            ("1,2-Benzenedicarboxylic acid, di-C9-11-branched alkyl esters, C10-rich", "68515-49-1", None),
        ),
        "note": (
            "This prohibition is wider than the measured limit in 2.2. The substances "
            "listed here are ADDITIONAL to the thirteen tested for, and the source "
            "document states the list is non-exhaustive - so a clean CAS screen supports "
            "the declaration but does not prove it."
        ),
    },
    {
        "id": "CP-3.4-HAZARD-CLASSIFICATION",
        "section": "3.4",
        "title": "CMR and STOT SE 1 raw materials",
        "requirement": (
            "A raw material for which the supplier applies a self-classification as CMR "
            "class 1a or 1b (H340, H350, H360) or STOT SE class 1 (H370) may not be "
            "intentionally used from the moment that classification appears on the safety "
            "data sheet. Where a substance has a harmonised classification under CLP "
            "Regulation 1272/2008 giving CMR 1a or 1b or STOT SE 1 in the EU, intentional "
            "use is forbidden from the date of entry into application."
        ),
        "determination": DETERMINATION_DECLARED,
        "method": METHOD_SDS_HAZARD,
        "limit": "No raw material carrying H340, H350, H360 or H370",
        "test_method": None,
        "substances": (
            ("H340 - May cause genetic defects (Mutagen 1A/1B)", None, "Prohibited"),
            ("H350 - May cause cancer (Carcinogen 1A/1B)", None, "Prohibited"),
            ("H360 - May damage fertility or the unborn child (Reprotoxic 1A/1B)", None, "Prohibited"),
            ("H370 - Causes damage to organs (STOT SE 1)", None, "Prohibited"),
        ),
        "note": (
            "The criterion is written by reference to the SDS itself - 'from the moment "
            "this appear on the SDS' - so it is answered by reading section 2 of each raw "
            "material's safety data sheet. This is the one criterion PI3 can settle "
            "deterministically rather than indicatively. Section 3.5 exempts biocides. "
            "The source document also notes that a label holder who receives an SDS with "
            "such a classification and has no drop-in substitute may request a transition "
            "period from the EUROPUR secretariat."
        ),
    },
    {
        "id": "CP-3.5-BIOCIDES",
        "section": "3.5",
        "title": "Biocides",
        "requirement": (
            "Only biocides allowed under the Biocidal Products Regulation 528/2012 and its "
            "amendments for use in product type 9 may be used in flexible PU foam under "
            "CertiPUR."
        ),
        "determination": DETERMINATION_DECLARED,
        "method": METHOD_SUPPLIER_DECLARATION,
        "limit": "PT9-authorised biocides only",
        "test_method": None,
        "substances": (),
        "note": (
            "Explicitly carved out of section 3.4: a biocide is not disqualified by "
            "carrying a CMR or STOT SE 1 classification, only by not being PT9-authorised. "
            "Applying the 3.4 rule to a biocide would produce a false failure."
        ),
    },
    {
        "id": "CP-3.6-BLOWING-AGENTS",
        "section": "3.6",
        "title": "Blowing agents",
        "requirement": "CFC, HCFC and methylene chloride may not be used to produce foam under CertiPUR.",
        "determination": DETERMINATION_DECLARED,
        "method": METHOD_SUBSTANCE_SCREEN,
        "limit": "Not used",
        "test_method": None,
        "substances": (
            ("Chlorofluorocarbons (CFC)", None, "Prohibited"),
            ("Hydrochlorofluorocarbons (HCFC)", None, "Prohibited"),
            ("Methylene chloride (dichloromethane)", "75-09-2", "Prohibited"),
        ),
        "note": (
            "CFC and HCFC are already banned by Regulation 1005/2009 implementing the "
            "Montreal Protocol. The methylene chloride prohibition entered into force on "
            "1 September 2024 and is therefore in force now."
        ),
    },
    {
        "id": "CP-3.7-CHLOROBENZENES",
        "section": "3.7",
        "title": "Chlorobenzene content of diisocyanates",
        "requirement": (
            "The diisocyanates used to produce the foam must meet a limit of 20 ppm total "
            "chlorobenzenes."
        ),
        "determination": DETERMINATION_DECLARED,
        "method": METHOD_SUPPLIER_DECLARATION,
        "limit": "Maximum 20 ppm total chlorobenzenes",
        "test_method": "Quantitative GC-MS or an equivalent validated method",
        "substances": (
            ("Monochlorobenzene", "108-90-7", None),
            ("Dichlorobenzene (sum of all isomers)", None, None),
            ("Chlorobenzenes with 3 or more chlorine atoms (sum of all isomers)", None, None),
        ),
        "note": (
            "The source document states the evidence may be obtained from the raw material "
            "supplier, so this is a supplier document rather than a foam test. It applies "
            "only to the isocyanate components of the recipe."
        ),
    },
    {
        "id": "CP-3.8-BROMINATED-DIPHENYL-ETHERS",
        "section": "3.8",
        "title": "Brominated diphenyl ether flame retardants",
        "requirement": (
            "PentaBDE, OctaBDE and DecaBDE may not be intentionally used in or for the "
            "production of flexible polyurethane foam under CertiPUR."
        ),
        "determination": DETERMINATION_DECLARED,
        "method": METHOD_SUBSTANCE_SCREEN,
        "limit": "Not used",
        "test_method": None,
        "substances": (
            ("Pentabromodiphenyl Ether (PentaBDE)", "32534-81-9", "Prohibited"),
            ("Octabromodiphenyl Ether (OctaBDE)", "32536-52-0", "Prohibited"),
            ("Decabromodiphenyl Ether (DecaBDE)", "1163-19-5", "Prohibited"),
        ),
        "note": (
            "Relevant to the combustion-modified and brominated flame retardant foam "
            "families in particular, and answerable from the flame retardant's own SDS."
        ),
    },
)


def by_determination(determination):
    return tuple(c for c in CRITERIA if c["determination"] == determination)


def measured_criteria():
    """The four that only an accredited laboratory can answer."""
    return by_determination(DETERMINATION_MEASURED)


def declared_criteria():
    """The eight a pre-audit can actually assess."""
    return by_determination(DETERMINATION_DECLARED)


def prohibited_cas_numbers():
    """Every CAS number that is prohibited outright, for the formulation screen.

    Deliberately excludes the measured-limit sections: a substance there is not
    prohibited, it is limited in the finished foam, and matching one against a
    recipe would produce a failure the criteria do not support."""
    out = {}
    for c in declared_criteria():
        for name, cas, limit in c["substances"]:
            if cas and limit == "Prohibited":
                out[cas] = (name, c["id"])
    return out


PROHIBITED_H_CODES = ("H340", "H350", "H360", "H370")


def prohibited_hazard_codes(codes):
    """Which of `codes` are prohibited by section 3.4, matched on the FAMILY.

    A hazard statement carries an optional letter suffix that narrows what the
    hazard is without changing its class: H350i is Carcinogen 1A/1B by
    inhalation, H360D and H360FD are Reprotoxic 1A/1B. Section 3.4 prohibits
    the class, so all of those are prohibited and an exact string match against
    "H350" or "H360" would let every one of them through.

    Matched on the first four characters, which is precise in both directions:
    H341 (Mutagen 2) and H361 (Reprotoxic 2) and H371 (STOT SE 2) are NOT
    prohibited by 3.4, and their four-character prefixes are not in the list,
    so they correctly do not match.

    `codes` may be a list or a comma-joined string, since that is how the
    column stores them."""
    if not codes:
        return []
    if isinstance(codes, str):
        codes = [c.strip() for c in codes.split(",")]
    hits = []
    for code in codes:
        c = (code or "").strip()
        # Compared case-insensitively, reported as the sheet prints it - the
        # suffix casing is meaningful to a reader (H360Df is not H360DF) and a
        # report that silently re-cases the evidence is harder to check against
        # the document it came from.
        if len(c) >= 4 and c[:4].upper() in PROHIBITED_H_CODES:
            hits.append(c)
    return sorted(set(hits))
