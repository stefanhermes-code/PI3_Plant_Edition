"""Where-used traceability for a raw material.

Work package 7 of the Permanent Automated Regression Test Suite CR.

This is the report a person reaches for when a supplier lot is in question and
the answer to "which of our grades has this material in it" has to be complete.
A where-used report that quietly misses a grade is worse than no report,
because it is believed.

So every case here puts a second, unrelated raw material in the database as
well. A report that ignored the material id and returned everything would pass
a single-material test.
"""

from __future__ import annotations

import datetime as dt

import pytest

import db as m
import reports


@pytest.fixture
def materials_in_use(sqlite_session):
    """One material used in two grades - one active version, one retired -
    and a second material used nowhere, plus a third used only by the other
    grade."""
    session = sqlite_session
    company = m.Company(name="Trace Co")
    session.add(company)
    session.flush()
    plant = m.Plant(name="Trace Plant", company_id=company.id)
    session.add(plant)
    session.flush()
    family = m.ProductFamily(plant_id=plant.id, name="Conventional")
    session.add(family)
    session.flush()

    grade_a = m.FoamGrade(
        product_family_id=family.id, grade_name="A-GRADE", target_density=30.0
    )
    grade_b = m.FoamGrade(product_family_id=family.id, grade_name="B-GRADE")
    session.add_all([grade_a, grade_b])
    session.flush()

    active = m.RecipeVersion(
        foam_grade_id=grade_a.id, version_label="A-v2", is_active=True
    )
    retired = m.RecipeVersion(
        foam_grade_id=grade_b.id, version_label="B-v1", is_active=False
    )
    session.add_all([active, retired])
    session.flush()

    shared = m.RawMaterial(name="Polyol Shared", category="Polyol")
    other = m.RawMaterial(name="Polyol Other", category="Polyol")
    unused = m.RawMaterial(name="Polyol Unused", category="Polyol")
    session.add_all([shared, other, unused])
    session.flush()

    session.add_all([
        m.RecipeComponent(
            recipe_version_id=active.id,
            raw_material_name=shared.name,
            raw_material_id=shared.id,
            php=100.0,
        ),
        m.RecipeComponent(
            recipe_version_id=retired.id,
            raw_material_name=shared.name,
            raw_material_id=shared.id,
            php=95.0,
        ),
        m.RecipeComponent(
            recipe_version_id=active.id,
            raw_material_name=other.name,
            raw_material_id=other.id,
            php=3.5,
        ),
    ])

    session.add(
        m.CustomerTrial(
            plant_id=plant.id,
            foam_grade_id=grade_a.id,
            recipe_version_id=active.id,
            customer_name="A Customer",
            trial_date=dt.date(2026, 5, 1),
            status="Closed",
        )
    )
    session.commit()

    return {
        "session": session,
        "shared": shared.id,
        "other": other.id,
        "unused": unused.id,
        "grade_a": "A-GRADE",
        "grade_b": "B-GRADE",
    }


def test_a_material_used_in_two_grades_reports_both(materials_in_use):
    data = reports.build_where_used_report_data(
        materials_in_use["session"], materials_in_use["shared"]
    )
    grades = {row["Foam grade"] for row in data["usage_rows"]}
    assert grades == {materials_in_use["grade_a"], materials_in_use["grade_b"]}
    assert data["foam_grade_count"] == 2
    assert data["recipe_version_count"] == 2


def test_the_report_is_for_the_material_asked_for(materials_in_use):
    """The other material is in the same recipe version. It must not appear."""
    data = reports.build_where_used_report_data(
        materials_in_use["session"], materials_in_use["other"]
    )
    grades = {row["Foam grade"] for row in data["usage_rows"]}
    assert grades == {materials_in_use["grade_a"]}
    assert data["raw_material_name"] == "Polyol Other"
    assert len(data["usage_rows"]) == 1


def test_a_retired_version_is_reported_and_marked_retired(materials_in_use):
    """It still has to appear. Foam made under it is in the world."""
    data = reports.build_where_used_report_data(
        materials_in_use["session"], materials_in_use["shared"]
    )
    statuses = {row["Foam grade"]: row["Status"] for row in data["usage_rows"]}
    assert statuses[materials_in_use["grade_a"]] == "Active"
    assert statuses[materials_in_use["grade_b"]] == "Retired"


def test_the_php_reported_is_the_one_recorded_on_that_version(materials_in_use):
    data = reports.build_where_used_report_data(
        materials_in_use["session"], materials_in_use["shared"]
    )
    php = {row["Foam grade"]: row["PHP"] for row in data["usage_rows"]}
    assert php[materials_in_use["grade_a"]] == 100.0
    assert php[materials_in_use["grade_b"]] == 95.0


def test_a_material_used_nowhere_reports_nothing_rather_than_everything(materials_in_use):
    """An empty result and a missing filter look the same until you check."""
    data = reports.build_where_used_report_data(
        materials_in_use["session"], materials_in_use["unused"]
    )
    assert data is not None
    assert data["usage_rows"] == []
    assert data["recipe_version_count"] == 0
    assert data["foam_grade_count"] == 0
    assert data["trial_rows"] == []


def test_a_material_that_does_not_exist_reports_nothing_at_all(materials_in_use):
    assert (
        reports.build_where_used_report_data(materials_in_use["session"], 999_999)
        is None
    )


def test_a_trial_run_on_an_affected_version_is_reported(materials_in_use):
    """The reason this report exists in a recall: which trials used it."""
    data = reports.build_where_used_report_data(
        materials_in_use["session"], materials_in_use["shared"]
    )
    assert len(data["trial_rows"]) == 1
    assert data["trial_rows"][0]["Trial type"] == "Customer Trial"
    assert data["trial_rows"][0]["Foam grade"] == materials_in_use["grade_a"]


def test_a_trial_on_a_version_this_material_is_not_in_is_not_reported(materials_in_use):
    data = reports.build_where_used_report_data(
        materials_in_use["session"], materials_in_use["unused"]
    )
    assert data["trial_rows"] == []


def test_the_grade_targets_come_through_for_the_affected_grades(materials_in_use):
    data = reports.build_where_used_report_data(
        materials_in_use["session"], materials_in_use["shared"]
    )
    density = [row for row in data["target_rows"] if row["Property"] == "Density"]
    assert len(density) == 1
    assert density[0]["Foam grade"] == materials_in_use["grade_a"]
    assert density[0]["Target"] == 30.0


# --- the cell parsers every import goes through -----------------------------
#
# Small, and worth pinning: these decide what a spreadsheet cell becomes on the
# way into the database. A date read wrong or a flag read wrong produces a row
# that looks perfectly ordinary.

import helpers  # noqa: E402 - grouped with the tests that use it


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("2026-08-22", dt.datetime(2026, 8, 22)),
        ("2026-08-22 14:30", dt.datetime(2026, 8, 22, 14, 30)),
        (dt.date(2026, 8, 22), dt.datetime(2026, 8, 22)),
    ],
    ids=["iso date", "iso datetime", "a real date object"],
)
def test_a_date_cell_parses_to_that_date(cell, expected):
    assert helpers.parse_dt(cell) == expected


@pytest.mark.parametrize(
    "cell",
    [None, "", "   ", "not a date", "n/a"],
    ids=["none", "empty", "whitespace", "text", "n/a"],
)
def test_a_cell_that_is_not_a_date_becomes_nothing_rather_than_today(cell):
    """Coercing an unreadable cell to now() would date a record to the import."""
    assert helpers.parse_dt(cell) is None


@pytest.mark.parametrize(
    "cell", [True, "true", "TRUE", " True ", "1", "yes", "Y"],
    ids=["bool", "true", "upper", "padded", "one", "yes", "y"],
)
def test_the_ways_a_spreadsheet_says_yes(cell):
    assert helpers.parse_bool(cell) is True


@pytest.mark.parametrize(
    "cell", [False, "false", "no", "0", "", None, "maybe", "2"],
    ids=["bool", "false", "no", "zero", "empty", "none", "maybe", "two"],
)
def test_everything_else_is_no(cell):
    """Fail closed. An unreadable flag must not switch something on."""
    assert helpers.parse_bool(cell) is False
