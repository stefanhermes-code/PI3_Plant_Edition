"""Deleting a production run, and everything that must and must not go with it.

Work package 7 of the Permanent Automated Regression Test Suite CR.

A cascade delete has two ways to be wrong and only one of them is visible. If
it deletes too little, the next screen shows an orphan and somebody reports it.
If it deletes too much, the rows are gone and nobody knows they were ever
there - there is no error, no gap, and no way back short of a restore.

So every case here builds **two** runs and asserts that the second one is
untouched. Deleting the only run in the database would pass whatever the code
did.
"""

from __future__ import annotations

import datetime as dt

import pytest

import cascades
import db as m


@pytest.fixture
def two_runs(sqlite_session):
    """Two production runs on the same plant, each with a full set of children."""
    session = sqlite_session
    company = m.Company(name="Cascade Co")
    session.add(company)
    session.flush()
    plant = m.Plant(name="Cascade Plant", company_id=company.id)
    session.add(plant)
    session.flush()
    family = m.ProductFamily(plant_id=plant.id, name="Fam")
    session.add(family)
    session.flush()
    grade = m.FoamGrade(product_family_id=family.id, grade_name="G1")
    session.add(grade)
    session.flush()
    version = m.RecipeVersion(foam_grade_id=grade.id, version_label="v1")
    session.add(version)
    session.flush()

    made = []
    for label in ("first", "second"):
        run = m.ProductionRun(
            plant_id=plant.id, foam_grade_id=grade.id, recipe_version_id=version.id
        )
        session.add(run)
        session.flush()

        phase = m.ProductionPhase(production_run_id=run.id, phase_name=f"{label} phase")
        sample = m.Sample(production_run_id=run.id)
        session.add_all([phase, sample])
        session.flush()

        session.add_all([
            m.ProductionEvent(
                production_run_id=run.id,
                event_ts=dt.datetime(2026, 1, 1, 12, 0),
                event_type="start",
            ),
            m.RawMaterialLotUse(
                production_run_id=run.id,
                component_stream_name="Polyol",
                supplier_lot_no=f"LOT-{label}",
            ),
            m.RuntimeDataRecord(production_run_id=run.id),
            m.PhysicalPropertyResult(
                sample_id=sample.id,
                production_run_id=run.id,
                property_name="Density",
            ),
            m.QualityObservation(
                production_run_id=run.id, observation_type="Split"
            ),
        ])
        session.flush()
        made.append(run.id)

    session.commit()
    return session, made[0], made[1]


def test_the_counts_report_what_is_actually_there(two_runs):
    """The number the confirmation dialog shows a person before they agree."""
    session, first, _ = two_runs
    counts = cascades.production_run_dependency_counts(session, first)

    assert counts["process phase(s)"] == 1
    assert counts["sample(s)"] == 1
    assert counts["production event(s)"] == 1
    assert counts["raw material lot use(s)"] == 1
    assert counts["runtime data record(s)"] == 1
    assert counts["quality test result(s)"] == 1
    assert counts["quality issue(s)"] == 1


def test_the_counts_are_for_this_run_only(two_runs):
    """Two identical runs. A count that ignored the run id would read 2."""
    session, first, _ = two_runs
    counts = cascades.production_run_dependency_counts(session, first)
    assert all(value <= 1 for value in counts.values()), counts


def test_a_run_with_nothing_hanging_off_it_counts_zero_not_none(two_runs):
    session, _, _ = two_runs
    empty = m.ProductionRun(
        plant_id=session.query(m.Plant).first().id,
        foam_grade_id=session.query(m.FoamGrade).first().id,
        recipe_version_id=session.query(m.RecipeVersion).first().id,
    )
    session.add(empty)
    session.commit()

    counts = cascades.production_run_dependency_counts(session, empty.id)
    assert set(counts.values()) == {0}


DEPENDENTS = [
    (m.ProductionPhase, "production_run_id"),
    (m.Sample, "production_run_id"),
    (m.ProductionEvent, "production_run_id"),
    (m.RawMaterialLotUse, "production_run_id"),
    (m.RuntimeDataRecord, "production_run_id"),
    (m.PhysicalPropertyResult, "production_run_id"),
    (m.QualityObservation, "production_run_id"),
]


@pytest.mark.parametrize(
    "model,column", DEPENDENTS, ids=[d[0].__name__ for d in DEPENDENTS]
)
def test_the_delete_takes_the_runs_own_dependents(two_runs, model, column):
    session, first, _ = two_runs
    cascades.delete_production_run_cascade(session, first)
    session.commit()

    remaining = session.query(model).filter(getattr(model, column) == first).count()
    assert remaining == 0


@pytest.mark.parametrize(
    "model,column", DEPENDENTS, ids=[d[0].__name__ for d in DEPENDENTS]
)
def test_the_delete_leaves_the_other_runs_dependents_alone(two_runs, model, column):
    """The half that is invisible when it goes wrong."""
    session, first, second = two_runs
    cascades.delete_production_run_cascade(session, first)
    session.commit()

    survived = session.query(model).filter(getattr(model, column) == second).count()
    assert survived == 1


def test_the_run_itself_goes_and_the_other_one_stays(two_runs):
    session, first, second = two_runs
    cascades.delete_production_run_cascade(session, first)
    session.commit()

    assert session.get(m.ProductionRun, first) is None
    assert session.get(m.ProductionRun, second) is not None


def test_the_delete_does_not_reach_up_into_the_master_data(two_runs):
    """A run belongs to a plant, a grade and a recipe version. Deleting the run
    must not take any of them - they are shared with every other run."""
    session, first, _ = two_runs
    cascades.delete_production_run_cascade(session, first)
    session.commit()

    assert session.query(m.Plant).count() == 1
    assert session.query(m.FoamGrade).count() == 1
    assert session.query(m.RecipeVersion).count() == 1
    assert session.query(m.ProductFamily).count() == 1
    assert session.query(m.Company).count() == 1


def test_deleting_a_run_that_does_not_exist_changes_nothing(two_runs):
    session, first, second = two_runs
    before = {
        model.__name__: session.query(model).count() for model, _ in DEPENDENTS
    }

    cascades.delete_production_run_cascade(session, 999_999)
    session.commit()

    after = {model.__name__: session.query(model).count() for model, _ in DEPENDENTS}
    assert after == before
    assert session.get(m.ProductionRun, first) is not None
    assert session.get(m.ProductionRun, second) is not None
