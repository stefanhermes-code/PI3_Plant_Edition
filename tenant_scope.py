"""Shared company-scoping helpers for the operational pages that sit below
Plant in the schema hierarchy.

Plant, RawMaterial, and Supplier already carry their own `company_id`
(see pages/1_Plant_Installation_Overview.py and pages/14_Raw_Materials.py).
Everything else - product families, foam grades, recipes, production runs,
and all the quality/trial/maintenance data keyed to a production run or a
plant - has no `company_id` column of its own. It scopes through the
plant(s) it ultimately hangs off:

    Plant --- ProductFamily --- FoamGrade --- RecipeVersion --- RecipeComponent
      |
      +--- Machine
      |
      +--- ProductionRun --- TrialRecord / QualityObservation /
                              PhysicalPropertyResult / AdjustmentConclusion /
                              ApprovalRecord / ExpertNote / ...
      |
      +--- MaintenanceLicenseRecord / PI3AIConnectionSetting

`None` is used throughout as the "unfiltered" sentinel (the platform owner
viewing "All companies"), matching the convention already used in
access_control.py and on the Plant/Raw Materials pages. An empty list
(`[]`) is a real, different value: it means the company has zero plants
(or zero families, etc.) yet, so anything scoped to it should show nothing
- not silently fall through to "everything."
"""

from db import Company, FoamGrade, Plant, ProductFamily, ProductionRun


def company_picker(st_module, session, is_platform_owner, own_company_id, key):
    """Same 'Company' selectbox (platform owner) / lock (everyone else)
    pattern already used on the Plant and Raw Materials pages. Returns
    (selected_company_or_None, all_companies)."""
    all_companies = session.query(Company).order_by(Company.name).all()
    if is_platform_owner:
        company = st_module.selectbox(
            "Company", [None] + all_companies,
            format_func=lambda c: "All companies" if c is None else c.name,
            key=key,
        )
    else:
        company = next((c for c in all_companies if c.id == own_company_id), None)
    return company, all_companies


def plant_ids_for_company(session, company_id):
    """None (company_id is None) = unfiltered. Otherwise the list of
    Plant.id belonging to that company (possibly empty)."""
    if company_id is None:
        return None
    return [pid for (pid,) in session.query(Plant.id).filter(Plant.company_id == company_id).all()]


def family_ids_for_plants(session, plant_ids):
    if plant_ids is None:
        return None
    if not plant_ids:
        return []
    return [
        fid for (fid,) in session.query(ProductFamily.id).filter(ProductFamily.plant_id.in_(plant_ids)).all()
    ]


def grade_ids_for_families(session, family_ids):
    if family_ids is None:
        return None
    if not family_ids:
        return []
    return [
        gid for (gid,) in session.query(FoamGrade.id).filter(FoamGrade.product_family_id.in_(family_ids)).all()
    ]


def grade_ids_for_company(session, company_id):
    """Convenience: foam grade ids reachable from a company's plants,
    walking Plant -> ProductFamily -> FoamGrade in one call."""
    plant_ids = plant_ids_for_company(session, company_id)
    family_ids = family_ids_for_plants(session, plant_ids)
    return grade_ids_for_families(session, family_ids)


def run_ids_for_plants(session, plant_ids):
    if plant_ids is None:
        return None
    if not plant_ids:
        return []
    return [
        rid for (rid,) in session.query(ProductionRun.id).filter(ProductionRun.plant_id.in_(plant_ids)).all()
    ]


def run_ids_for_company(session, company_id):
    """Convenience: production run ids reachable from a company's plants."""
    plant_ids = plant_ids_for_company(session, company_id)
    return run_ids_for_plants(session, plant_ids)


def apply_scope(query, column, ids):
    """ids=None -> no filter (unfiltered). ids=[] -> filters to zero rows
    (correct when the company has none of that entity yet, rather than
    silently showing everything). Otherwise filters to column.in_(ids)."""
    if ids is None:
        return query
    return query.filter(column.in_(ids))
