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
    AdjustmentConclusion,
    ApprovalRecord,
    ComponentStreamReading,
    ConditioningSegment,
    FallplateSectionPosition,
    FoamGrade,
    Machine,
    MaintenanceLicenseRecord,
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
    TrialRecord,
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
        "adjustment & conclusion record(s)": session.query(AdjustmentConclusion)
        .filter(AdjustmentConclusion.production_run_id == run_id).count(),
        "approval record(s)": session.query(ApprovalRecord)
        .filter(ApprovalRecord.production_run_id == run_id).count(),
        "trial / experiment record(s)": session.query(TrialRecord)
        .filter(TrialRecord.production_run_id == run_id).count(),
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
    session.query(AdjustmentConclusion).filter(
        AdjustmentConclusion.production_run_id == run_id
    ).delete(synchronize_session=False)
    session.query(ApprovalRecord).filter(
        ApprovalRecord.production_run_id == run_id
    ).delete(synchronize_session=False)
    # TrialRecord.production_run_id is NOT NULL - must go before the run
    # itself. Order relative to the four tables above doesn't matter for
    # THEIR deletion, but it does matter that TrialRecord is deleted only
    # after (or regardless of) their trial_record_id references, since
    # those rows are being deleted outright, not repointed.
    session.query(TrialRecord).filter(
        TrialRecord.production_run_id == run_id
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
    return counts


def delete_recipe_version_cascade(session, recipe_version_id):
    for run_id in _run_ids_for_recipe_version(session, recipe_version_id):
        delete_production_run_cascade(session, run_id)
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


def foam_grade_dependency_counts(session, foam_grade_id):
    version_ids = _version_ids_for_foam_grade(session, foam_grade_id)
    counts = {"recipe version(s)": len(version_ids)}
    for version_id in version_ids:
        counts["recipe component(s)"] = counts.get("recipe component(s)", 0) + (
            session.query(RecipeComponent).filter(RecipeComponent.recipe_version_id == version_id).count()
        )
    run_ids = _run_ids_for_foam_grade(session, foam_grade_id)
    counts["production run(s)"] = len(run_ids)
    for run_id in run_ids:
        _merge_counts(counts, production_run_dependency_counts(session, run_id))
    return counts


def delete_foam_grade_cascade(session, foam_grade_id):
    version_ids = _version_ids_for_foam_grade(session, foam_grade_id)
    for run_id in _run_ids_for_foam_grade(session, foam_grade_id):
        delete_production_run_cascade(session, run_id)
    for version_id in version_ids:
        session.query(RecipeComponent).filter(
            RecipeComponent.recipe_version_id == version_id
        ).delete(synchronize_session=False)
    session.query(RecipeVersion).filter(RecipeVersion.foam_grade_id == foam_grade_id).delete(synchronize_session=False)
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
    for family_id in family_ids:
        _merge_counts(counts, product_family_dependency_counts(session, family_id))
        for grade_id in _grade_ids_for_family(session, family_id):
            already_counted_run_ids.update(_run_ids_for_foam_grade(session, grade_id))

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

    counts["machine(s)"] = session.query(Machine).filter(Machine.plant_id == plant_id).count()
    counts["maintenance/license record(s)"] = (
        session.query(MaintenanceLicenseRecord).filter(MaintenanceLicenseRecord.plant_id == plant_id).count()
    )
    counts["pi3/ai connectivity setting(s)"] = (
        session.query(PI3AIConnectionSetting).filter(PI3AIConnectionSetting.plant_id == plant_id).count()
    )
    return counts


def delete_plant_cascade(session, plant_id):
    family_ids = [
        f.id for f in session.query(ProductFamily.id).filter(ProductFamily.plant_id == plant_id).all()
    ]
    already_deleted_run_ids = set()
    for family_id in family_ids:
        for grade_id in _grade_ids_for_family(session, family_id):
            already_deleted_run_ids.update(_run_ids_for_foam_grade(session, grade_id))
        delete_product_family_cascade(session, family_id)

    remaining_run_ids = set(
        r.id for r in session.query(ProductionRun.id).filter(ProductionRun.plant_id == plant_id).all()
    ) - already_deleted_run_ids
    for run_id in remaining_run_ids:
        delete_production_run_cascade(session, run_id)

    session.query(Machine).filter(Machine.plant_id == plant_id).delete(synchronize_session=False)
    session.query(MaintenanceLicenseRecord).filter(MaintenanceLicenseRecord.plant_id == plant_id).delete(synchronize_session=False)
    session.query(PI3AIConnectionSetting).filter(PI3AIConnectionSetting.plant_id == plant_id).delete(synchronize_session=False)
    session.query(Plant).filter(Plant.id == plant_id).delete(synchronize_session=False)
