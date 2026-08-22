"""A two-company world, for tests about who may see what.

One company is never enough for an isolation test. If there is only company A
in the database, a query that ignores the company filter altogether returns
exactly what a correctly scoped query returns, and the test passes.

So this builds two complete companies with the same shape - plant, product
family, foam grade, recipe version, production run, customer trial,
optimization trial - and a third company with nothing in it at all, which is
its own case: an empty scope must show nothing, not everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import db as m


@dataclass
class CompanyRows:
    """Every id belonging to one company, so a test can name what it expects."""

    company_id: int
    plant_id: int
    family_id: int
    grade_id: int
    recipe_version_id: int
    run_id: int
    customer_trial_id: int
    optimization_trial_id: int


@dataclass
class World:
    a: CompanyRows
    b: CompanyRows
    empty_company_id: int
    session: object = field(repr=False, default=None)


def _build_company(session, label: str) -> CompanyRows:
    company = m.Company(name=f"Company {label}")
    session.add(company)
    session.flush()

    plant = m.Plant(name=f"{label} Plant", company_id=company.id)
    session.add(plant)
    session.flush()

    family = m.ProductFamily(plant_id=plant.id, name=f"{label} Family")
    session.add(family)
    session.flush()

    grade = m.FoamGrade(product_family_id=family.id, grade_name=f"{label}-GRADE")
    session.add(grade)
    session.flush()

    version = m.RecipeVersion(foam_grade_id=grade.id, version_label=f"{label}-v1")
    session.add(version)
    session.flush()

    run = m.ProductionRun(
        plant_id=plant.id, foam_grade_id=grade.id, recipe_version_id=version.id
    )
    customer_trial = m.CustomerTrial(
        plant_id=plant.id, foam_grade_id=grade.id, customer_name=f"{label} Customer"
    )
    optimization_trial = m.OptimizationTrial(plant_id=plant.id, foam_grade_id=grade.id)
    session.add_all([run, customer_trial, optimization_trial])
    session.flush()

    return CompanyRows(
        company_id=company.id,
        plant_id=plant.id,
        family_id=family.id,
        grade_id=grade.id,
        recipe_version_id=version.id,
        run_id=run.id,
        customer_trial_id=customer_trial.id,
        optimization_trial_id=optimization_trial.id,
    )


def two_company_world(session) -> World:
    """Two fully populated companies, plus one with no plants at all."""
    a = _build_company(session, "A")
    b = _build_company(session, "B")
    empty = m.Company(name="Company C (no plants)")
    session.add(empty)
    session.flush()
    session.commit()
    return World(a=a, b=b, empty_company_id=empty.id, session=session)
