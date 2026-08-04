"""Shared cascade-delete helpers.

Deleting anything above a production run in the master-data hierarchy
(Plant -> Product Family -> Foam Grade -> Recipe Version) ultimately has
to delete every production run underneath it too, since
ProductionRun.plant_id, foam_grade_id, and recipe_version_id are all
NOT NULL foreign keys - there is no way to delete a Foam Grade, say,
while a run still points at it. This module centralizes that "delete a
run and everything under it" logic (and the master-data levels built on
top of it) so pages 1, 2, 3, and 4 all share one correct implementation
instead of four slightly-different copies.

None of these functions call session.commit() - callers commit once after
calling them (possibly several times, e.g. once per run under a foam
grade) so a whole master-data delete is one all-or-nothing transaction.
"""

from db import (
    ComponentStreamReading,
    ConditioningSegment,
    CustomerTrial,
    FallplateSectionPosition,
    FoamGrade,
    FoamGradeTargetProperty,
    Machine,
    OptimizationTrial,
    PhysicalPropertyResult,
    PI3AIConnectionSetting,
    Plant,
    ProductFamily,
    ProductionEvent,
    ProductionPhase,
    ProductionRun,
    QualityObservation,
    RawMaterialLotUse,
    RecipeComponent,
    RecipeVersion,
    RuntimeDataRecord,
    Sample,
)


# ---------------------------------------------------------------------------
# Production run (the base case everything else builds on)
# ---------------------------------------------------------------------------

def production_run_dependency_counts(session, run_id):
    phase_ids = [
        p.id for p in session.query(ProductionPhase.id)
        .filter(ProductionPhase.production_run_id == run_id).all()
    ]
    sample_ids = [
        s.id for s in session.query(Sample.id)
        .filter(Sample.production_run_id == run_id).all()
    ]
    return {
        "process phase(s)": len(phase_ids),
        "component stream reading(s)": (
            session.query(ComponentStreamReading)
            .filter(ComponentStreamReading.production_phase_id.in_(phase_ids)).count()
            if phase_ids else 0
        ),
        "fall-plate section position(s)": (
            session.query(FallplateSectionPosition)
            .filter(FallplateSectionPosition.production_phase_id.in_(phase_ids)).count()
            if phase_ids else 0
        ),
        "production event(s)": session.query(ProductionEvent)
        .filter(ProductionEvent.production_run_id == run_id).count(),
        "raw material lot use(s)": session.query(RawMaterialLotUse)
        .filter(RawMaterialLotUse.production_run_id == run_id).count(),
        "runtime data record(s)": session.query(RuntimeDataRecord)
        .filter(RuntimeDataRecord.production_run_id == run_id).count(),
        "quality test result(s)": session.query(PhysicalPropertyResult)
        .filter(PhysicalPropertyResult.production_run_id == run_id).count(),
        "quality issue(s)": session.query(QualityObservation)
        .filter(QualityObservation.production_run_id == run_id).count(),
        "sample(s)": len(sample_ids),
        "conditioning segment(s)": (
            session.query(ConditioningSegment)
            .filter(ConditioningSegment.sample_id.in_(sample_ids)).count()
            if sample_ids else 0
        ),
    }


def delete_production_run_cascade(session, run_id):
    """Delete one production run and everything that depends on it."""
    phase_ids = [
        p.id for p in session.query(ProductionPhase.id)
        .filter(ProductionPhase.production_run_id == run_id).all()
    ]
    sample_ids = [
        s.id for s in session.query(Sample.id)
        .filter(Sample.production_run_id == run_id).all()
    ]

    if phase_ids:
        session.query(ComponentStreamReading).filter(
            ComponentStreamReading.production_phase_id.in_(phase_ids)
        ).delete(synchronize_session=False)
        session.query(FallplateSectionPosition).filter(
            FallplateSectionPosition.production_phase_id.in_(phase_ids)
        ).delete(synchronize_session=False)
    if sample_ids:
        session.query(ConditioningSegment).filter(
            ConditioningSegment.sample_id.in_(sample_ids)
        ).delete(synchronize_session=False)

    session.query(ProductionEvent).filter(
        ProductionEvent.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(ProductionPhase).filter(
        ProductionPhase.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(RawMaterialLotUse).filter(
        RawMaterialLotUse.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(RuntimeDataRecord).filter(
        RuntimeDataRecord.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(PhysicalPropertyResult).filter(
        PhysicalPropertyResult.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(QualityObservation).filter(
        QualityObservation.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(Sample).filter(
        Sample.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(ProductionRun).filter(ProductionRun.id == run_id).delete(synchronize_session=False)


def _merge_counts(total, addition):
    for k, v in addition.items():
        total[k] = total.get(k, 0) + v
    return total


# ---------------------------------------------------------------------------
# Customer trial / Optimization trial - the two independent lab-trial flows
# added 2026-08-03 (see db.py's SAMPLE_SOURCE_TYPES / CustomerTrial /
# OptimizationTrial). Unlike a production run, these have no
# ProductionPhase underneath (no machine/process settings), so their
# dependency tree is much shallower: just samples (+ their conditioning
# history) and quality results/issues keyed directly to the trial.
# ---------------------------------------------------------------------------

def _sample_ids_for_customer_trial(session, customer_trial_id):
    return [
        s.id for s in session.query(Sample.id)
        .filter(Sample.customer_trial_id == customer_trial_id).all()
    ]


def customer_trial_dependency_counts(session, customer_trial_id):
    sample_ids = _sample_ids_for_customer_trial(session, customer_trial_id)
    return {
        "sample(s)": len(sample_ids),
        "conditioning segment(s)": (
            session.query(ConditioningSegment)
            .filter(ConditioningSegment.sample_id.in_(sample_ids)).count()
            if sample_ids else 0
        ),
        "quality test result(s)": session.query(PhysicalPropertyResult)
        .filter(PhysicalPropertyResult.customer_trial_id == customer_trial_id).count(),
        "quality issue(s)": session.query(QualityObservation)
        .filter(QualityObservation.customer_trial_id == customer_trial_id).count(),
    }


def delete_customer_trial_cascade(session, customer_trial_id):
    sample_ids = _sample_ids_for_customer_trial(session, customer_trial_id)
    if sample_ids:
        session.query(ConditioningSegment).filter(
            ConditioningSegment.sample_id.in_(sample_ids)
        ).delete(synchronize_session=False)
    session.query(PhysicalPropertyResult).filter(
        PhysicalPropertyResult.customer_trial_id == customer_trial_id
    ).delete(synchronize_session=False)
    session.query(QualityObservation).filter(
        QualityObservation.customer_trial_id == customer_trial_id
    ).delete(synchronize_session=False)
    session.query(Sample).filter(
        Sample.customer_trial_id == customer_trial_id
    ).delete(synchronize_session=False)
    session.query(CustomerTrial).filter(CustomerTrial.id == customer_trial_id).delete(synchronize_session=False)


def _sample_ids_for_optimization_trial(session, optimization_trial_id):
    return [
        s.id for s in session.query(Sample.id)
        .filter(Sample.optimization_trial_id == optimization_trial_id).all()
    ]


def optimization_trial_dependency_counts(session, optimization_trial_id):
    sample_ids = _sample_ids_for_optimization_trial(session, optimization_trial_id)
    return {
        "sample(s)": len(sample_ids),
        "conditioning segment(s)": (
            session.query(ConditioningSegment)
            .filter(ConditioningSegment.sample_id.in_(sample_ids)).count()
            if sample_ids else 0
        ),
        "quality test result(s)": session.query(PhysicalPropertyResult)
        .filter(PhysicalPropertyResult.optimization_trial_id == optimization_trial_id).count(),
        "quality issue(s)": session.query(QualityObservation)
        .filter(QualityObservation.optimization_trial_id == optimization_trial_id).count(),
    }


def delete_optimization_trial_cascade(session, optimization_trial_id):
    sample_ids = _sample_ids_for_optimization_trial(session, optimization_trial_id)
    if sample_ids:
        session.query(ConditioningSegment).filter(
            ConditioningSegment.sample_id.in_(sample_ids)
        ).delete(synchronize_session=False)
    session.query(PhysicalPropertyResult).filter(
        PhysicalPropertyResult.optimization_trial_id == optimization_trial_id
    ).delete(synchronize_session=False)
    session.query(QualityObservation).filter(
        QualityObservation.optimization_trial_id == optimization_trial_id
    ).delete(synchronize_session=False)
    session.query(Sample).filter(
        Sample.optimization_trial_id == optimization_trial_id
    ).delete(synchronize_session=False)
    session.query(OptimizationTrial).filter(
        OptimizationTrial.id == optimization_trial_id
    ).delete(synchronize_session=False)


# ---------------------------------------------------------------------------
# Recipe version
# ---------------------------------------------------------------------------

def _run_ids_for_recipe_version(session, recipe_version_id):
    return [
        r.id for r in session.query(ProductionRun.id)
        .filter(ProductionRun.recipe_version_id == recipe_version_id).all()
    ]


def recipe_version_dependency_counts(session, recipe_version_id):
    run_ids = _run_ids_for_recipe_version(session, recipe_version_id)
    counts = {
        "recipe component(s)": session.query(RecipeComponent)
        .filter(RecipeComponent.recipe_version_id == recipe_version_id).count(),
        "production run(s)": len(run_ids),
    }
    for run_id in run_ids:
        _merge_counts(counts, production_run_dependency_counts(session, run_id))
    # recipe_version_id is a NULLABLE, optional reference on both lab-trial
    # tables (unlike ProductionRun's NOT NULL one above) - these trials get
    # their recipe reference cleared, not deleted, so count them separately.
    ct_count = session.query(CustomerTrial).filter(CustomerTrial.recipe_version_id == recipe_version_id).count()
    ot_count = session.query(OptimizationTrial).filter(OptimizationTrial.recipe_version_id == recipe_version_id).count()
    if ct_count:
        counts["customer trial(s) (recipe reference will be cleared, not deleted)"] = ct_count
    if ot_count:
        counts["optimization trial(s) (recipe reference will be cleared, not deleted)"] = ot_count
    return counts


def delete_recipe_version_cascade(session, recipe_version_id):
    for run_id in _run_ids_for_recipe_version(session, recipe_version_id):
        delete_production_run_cascade(session, run_id)
    session.query(CustomerTrial).filter(CustomerTrial.recipe_version_id == recipe_version_id).update(
        {"recipe_version_id": None}, synchronize_session="fetch"
    )
    session.query(OptimizationTrial).filter(OptimizationTrial.recipe_version_id == recipe_version_id).update(
        {"recipe_version_id": None}, synchronize_session="fetch"
    )
    session.query(RecipeComponent).filter(
        RecipeComponent.recipe_version_id == recipe_version_id
    ).delete(synchronize_session=False)
    session.query(RecipeVersion).filter(RecipeVersion.id == recipe_version_id).delete(synchronize_session=False)


# ---------------------------------------------------------------------------
# Foam grade
# ---------------------------------------------------------------------------

def _version_ids_for_foam_grade(session, foam_grade_id):
    return [
        v.id for v in session.query(RecipeVersion.id)
        .filter(RecipeVersion.foam_grade_id == foam_grade_id).all()
    ]


def _run_ids_for_foam_grade(session, foam_grade_id):
    run_ids = set(
        r.id for r in session.query(ProductionRun.id)
        .filter(ProductionRun.foam_grade_id == foam_grade_id).all()
    )
    for version_id in _version_ids_for_foam_grade(session, foam_grade_id):
        run_ids.update(_run_ids_for_recipe_version(session, version_id))
    return run_ids


def _customer_trial_ids_for_foam_grade(session, foam_grade_id):
    return [
        t.id for t in session.query(CustomerTrial.id)
        .filter(CustomerTrial.foam_grade_id == foam_grade_id).all()
    ]


def _optimization_trial_ids_for_foam_grade(session, foam_grade_id):
    return [
        t.id for t in session.query(OptimizationTrial.id)
        .filter(OptimizationTrial.foam_grade_id == foam_grade_id).all()
    ]


def foam_grade_dependency_counts(session, foam_grade_id):
    version_ids = _version_ids_for_foam_grade(session, foam_grade_id)
    counts = {"recipe version(s)": len(version_ids)}
    for version_id in version_ids:
        counts["recipe component(s)"] = counts.get("recipe component(s)", 0) + (
            session.query(RecipeComponent).filter(RecipeComponent.recipe_version_id == version_id).count()
        )
    counts["other target physical propert(y/ies)"] = (
        session.query(FoamGradeTargetProperty)
        .filter(FoamGradeTargetProperty.foam_grade_id == foam_grade_id)
        .count()
    )
    run_ids = _run_ids_for_foam_grade(session, foam_grade_id)
    counts["production run(s)"] = len(run_ids)
    for run_id in run_ids:
        _merge_counts(counts, production_run_dependency_counts(session, run_id))
    # foam_grade_id is a NOT NULL, required FK on both lab-trial tables
    # (unlike recipe_version_id above) - these trials get fully deleted,
    # same as production runs.
    ct_ids = _customer_trial_ids_for_foam_grade(session, foam_grade_id)
    counts["customer trial(s)"] = len(ct_ids)
    for ct_id in ct_ids:
        _merge_counts(counts, customer_trial_dependency_counts(session, ct_id))
    ot_ids = _optimization_trial_ids_for_foam_grade(session, foam_grade_id)
    counts["optimization trial(s)"] = len(ot_ids)
    for ot_id in ot_ids:
        _merge_counts(counts, optimization_trial_dependency_counts(session, ot_id))
    return counts


def delete_foam_grade_cascade(session, foam_grade_id):
    version_ids = _version_ids_for_foam_grade(session, foam_grade_id)
    for run_id in _run_ids_for_foam_grade(session, foam_grade_id):
        delete_production_run_cascade(session, run_id)
    for ct_id in _customer_trial_ids_for_foam_grade(session, foam_grade_id):
        delete_customer_trial_cascade(session, ct_id)
    for ot_id in _optimization_trial_ids_for_foam_grade(session, foam_grade_id):
        delete_optimization_trial_cascade(session, ot_id)
    for version_id in version_ids:
        session.query(RecipeComponent).filter(
            RecipeComponent.recipe_version_id == version_id
        ).delete(synchronize_session=False)
    session.query(RecipeVersion).filter(RecipeVersion.foam_grade_id == foam_grade_id).delete(synchronize_session=False)
    session.query(FoamGradeTargetProperty).filter(
        FoamGradeTargetProperty.foam_grade_id == foam_grade_id
    ).delete(synchronize_session=False)
    session.query(FoamGrade).filter(FoamGrade.id == foam_grade_id).delete(synchronize_session=False)


# ---------------------------------------------------------------------------
# Product family
# ---------------------------------------------------------------------------

def _grade_ids_for_family(session, product_family_id):
    return [
        g.id for g in session.query(FoamGrade.id)
        .filter(FoamGrade.product_family_id == product_family_id).all()
    ]


def product_family_dependency_counts(session, product_family_id):
    grade_ids = _grade_ids_for_family(session, product_family_id)
    counts = {"foam grade(s)": len(grade_ids)}
    for grade_id in grade_ids:
        _merge_counts(counts, foam_grade_dependency_counts(session, grade_id))
    return counts


def delete_product_family_cascade(session, product_family_id):
    for grade_id in _grade_ids_for_family(session, product_family_id):
        delete_foam_grade_cascade(session, grade_id)
    session.query(ProductFamily).filter(ProductFamily.id == product_family_id).delete(synchronize_session=False)


# ---------------------------------------------------------------------------
# Plant (the deepest one - every level above collapses into this)
# ---------------------------------------------------------------------------

def plant_dependency_counts(session, plant_id):
    family_ids = [
        f.id for f in session.query(ProductFamily.id).filter(ProductFamily.plant_id == plant_id).all()
    ]
    counts = {"product family(ies)": len(family_ids)}
    already_counted_run_ids = set()
    already_counted_ct_ids = set()
    already_counted_ot_ids = set()
    for family_id in family_ids:
        _merge_counts(counts, product_family_dependency_counts(session, family_id))
        for grade_id in _grade_ids_for_family(session, family_id):
            already_counted_run_ids.update(_run_ids_for_foam_grade(session, grade_id))
            already_counted_ct_ids.update(_customer_trial_ids_for_foam_grade(session, grade_id))
            already_counted_ot_ids.update(_optimization_trial_ids_for_foam_grade(session, grade_id))

    # Runs keyed directly to this plant that weren't already reached via a
    # product family/foam grade above (shouldn't normally happen given how
    # runs are created, but a direct plant_id FK exists, so check for it).
    direct_run_ids = set(
        r.id for r in session.query(ProductionRun.id).filter(ProductionRun.plant_id == plant_id).all()
    )
    extra_run_ids = direct_run_ids - already_counted_run_ids
    counts["production run(s) not otherwise linked"] = len(extra_run_ids)
    for run_id in extra_run_ids:
        _merge_counts(counts, production_run_dependency_counts(session, run_id))

    # Same "not otherwise linked" check for the two lab-trial tables - both
    # carry their own direct plant_id (NOT NULL) alongside foam_grade_id.
    direct_ct_ids = set(
        t.id for t in session.query(CustomerTrial.id).filter(CustomerTrial.plant_id == plant_id).all()
    )
    extra_ct_ids = direct_ct_ids - already_counted_ct_ids
    counts["customer trial(s) not otherwise linked"] = len(extra_ct_ids)
    for ct_id in extra_ct_ids:
        _merge_counts(counts, customer_trial_dependency_counts(session, ct_id))

    direct_ot_ids = set(
        t.id for t in session.query(OptimizationTrial.id).filter(OptimizationTrial.plant_id == plant_id).all()
    )
    extra_ot_ids = direct_ot_ids - already_counted_ot_ids
    counts["optimization trial(s) not otherwise linked"] = len(extra_ot_ids)
    for ot_id in extra_ot_ids:
        _merge_counts(counts, optimization_trial_dependency_counts(session, ot_id))

    counts["machine(s)"] = session.query(Machine).filter(Machine.plant_id == plant_id).count()
    counts["pi3/ai connectivity setting(s)"] = (
        session.query(PI3AIConnectionSetting).filter(PI3AIConnectionSetting.plant_id == plant_id).count()
    )
    return counts


def delete_plant_cascade(session, plant_id):
    family_ids = [
        f.id for f in session.query(ProductFamily.id).filter(ProductFamily.plant_id == plant_id).all()
    ]
    already_deleted_run_ids = set()
    already_deleted_ct_ids = set()
    already_deleted_ot_ids = set()
    for family_id in family_ids:
        for grade_id in _grade_ids_for_family(session, family_id):
            already_deleted_run_ids.update(_run_ids_for_foam_grade(session, grade_id))
            already_deleted_ct_ids.update(_customer_trial_ids_for_foam_grade(session, grade_id))
            already_deleted_ot_ids.update(_optimization_trial_ids_for_foam_grade(session, grade_id))
        delete_product_family_cascade(session, family_id)

    remaining_run_ids = set(
        r.id for r in session.query(ProductionRun.id).filter(ProductionRun.plant_id == plant_id).all()
    ) - already_deleted_run_ids
    for run_id in remaining_run_ids:
        delete_production_run_cascade(session, run_id)

    remaining_ct_ids = set(
        t.id for t in session.query(CustomerTrial.id).filter(CustomerTrial.plant_id == plant_id).all()
    ) - already_deleted_ct_ids
    for ct_id in remaining_ct_ids:
        delete_customer_trial_cascade(session, ct_id)

    remaining_ot_ids = set(
        t.id for t in session.query(OptimizationTrial.id).filter(OptimizationTrial.plant_id == plant_id).all()
    ) - already_deleted_ot_ids
    for ot_id in remaining_ot_ids:
        delete_optimization_trial_cascade(session, ot_id)

    session.query(Machine).filter(Machine.plant_id == plant_id).delete(synchronize_session=False)
    session.query(PI3AIConnectionSetting).filter(PI3AIConnectionSetting.plant_id == plant_id).delete(synchronize_session=False)
    session.query(Plant).filter(Plant.id == plant_id).delete(synchronize_session=False)
