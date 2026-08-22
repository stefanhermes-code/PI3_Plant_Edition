"""Rendering a page headlessly, and enough data for it to have something to draw.

Why this exists
---------------
No test in this application can import a view. All thirty files under
``views/`` execute ``st.`` calls at module level, so importing one outside a
Streamlit runtime fails immediately. That is not a small gap: on 21 August 2026
the CertiPUR Readiness page raised ``KeyError`` on a customer's screen while
321 checks were green, because the engine had renamed a resolved key at
v2.22.1 and the view was never updated with it. Nothing in the suite could see
the inside of a page.

``streamlit.testing.v1.AppTest`` runs a page script headlessly, with a real
session state and a real script run, and reports whatever it raised. That is
what closes the gap, and it needs no change to the application.

Two things make it work here:

* ``tests/isolation.rebind_to_shared_memory`` gives the application one
  in-memory database that the AppTest thread can also see. Without it the page
  would open its own empty one.
* ``seed_application`` puts a company, a plant, a grade, a recipe and its
  components in that database, so a page reaches its data-dependent code
  instead of stopping at "nothing to show". A smoke test against an empty
  database exercises the top of a page and none of the parts that break.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import db as m

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VIEWS_DIR = os.path.join(PROJECT_ROOT, "views")


def view_files() -> list[str]:
    """Every page file, in navigation order rather than alphabetical."""
    names = [f for f in os.listdir(VIEWS_DIR) if f.endswith(".py")]

    def order(name):
        head = name.split("_", 1)[0]
        return (int(head) if head.isdigit() else 999, name)

    return sorted(names, key=order)


@dataclass
class SeededApplication:
    company_id: int
    plant_id: int
    family_id: int
    grade_id: int
    recipe_version_id: int
    raw_material_id: int
    machine_id: int
    role_id: int


def seed_application(session) -> SeededApplication:
    """One company with a complete, if small, set of live data.

    Deliberately one company, not two: these are render checks, and company
    isolation is proved properly in ``tests/unit/test_company_isolation.py``
    against a two-company world. Mixing the two here would make a render
    failure look like an isolation failure.
    """
    role = m.Role(name="Plant Manager")
    session.add(role)
    session.flush()

    company = m.Company(name="Render Test Co")
    if hasattr(m.Company, "certipur_enabled"):
        company.certipur_enabled = True
    session.add(company)
    session.flush()

    plant = m.Plant(name="Render Test Plant", company_id=company.id)
    session.add(plant)
    session.flush()

    machine = m.Machine(plant_id=plant.id, name="Line 1")
    family = m.ProductFamily(plant_id=plant.id, name="Conventional")
    session.add_all([machine, family])
    session.flush()

    grade = m.FoamGrade(product_family_id=family.id, grade_name="STD25230")
    session.add(grade)
    session.flush()

    version = m.RecipeVersion(
        foam_grade_id=grade.id, version_label="v1", is_active=True
    )
    session.add(version)
    session.flush()

    material = m.RawMaterial(name="Polyol A")
    if hasattr(m.RawMaterial, "company_id"):
        material.company_id = company.id
    if hasattr(m.RawMaterial, "category"):
        material.category = "Polyol"
    session.add(material)
    session.flush()

    session.add(
        m.RecipeComponent(
            recipe_version_id=version.id,
            raw_material_name=material.name,
            raw_material_id=material.id,
            php=100.0,
        )
    )
    session.commit()

    return SeededApplication(
        company_id=company.id,
        plant_id=plant.id,
        family_id=family.id,
        grade_id=grade.id,
        recipe_version_id=version.id,
        raw_material_id=material.id,
        machine_id=machine.id,
        role_id=role.id,
    )


def signed_in_state(seeded: SeededApplication, **overrides) -> dict:
    """The session state a page finds after a real login.

    Note what is NOT here: this is not the AUTH_DISABLED shape. That shape sets
    is_super_admin and leaves company_id None, which tenant_scope reads as
    unfiltered, so a page rendered under it would be showing every company's
    data and a render test would never notice. A real company_id is set for the
    same reason the isolation tests insist on one.
    """
    state = {
        "authenticated": True,
        "auth_source": "password",
        "username": "render.tester",
        "company_id": seeded.company_id,
        "role_id": seeded.role_id,
        "is_platform_owner": False,
        "is_super_admin": True,
    }
    state.update(overrides)
    return state


def render(view_file: str, state: dict, timeout: int = 120):
    """Run one page and hand back the finished AppTest.

    ``is_super_admin`` is set in the default state so that a page is not
    skipped over by an access-control denial - a page that renders nothing
    because the role could not see it would pass a render check while proving
    nothing about the page.
    """
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(os.path.join(VIEWS_DIR, view_file), default_timeout=timeout)
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    return app


def first_exception(app) -> str | None:
    """The first exception a page raised, as text, or None."""
    if not app.exception:
        return None
    raised = app.exception[0]
    return str(getattr(raised, "value", raised))
