"""
PI3 Plant Edition - v0.1 internal prototype
Demo data seed script.

Recreates the demonstration case from
"04_PI3_Plant_Edition_Demonstration_Case.docx": a mattress comfort foam
producer sees recurring hardness drift and block shrinkage in a 28 kg/m3
medium-hardness flexible slabstock grade after a formulation version
change. No real client data is used.

Run standalone: python demo_data.py

No longer wired into the app's UI (the Demo Data Admin page was removed
2026-07-31 - not needed once real customer data exists) - this module is
kept only as a way to seed a throwaway local/dev database by calling
seed_demo_data(session) directly.
"""

import datetime as dt

from db import (
    CustomerTrial,
    FoamGrade,
    Machine,
    OptimizationTrial,
    PhysicalPropertyResult,
    Plant,
    ProductFamily,
    ProductionPhase,
    ProductionRun,
    QualityObservation,
    RawMaterial,
    RecipeComponent,
    RecipeVersion,
    Sample,
    get_session,
    init_db,
)


def already_seeded(session) -> bool:
    return session.query(Plant).filter(Plant.name == "Demo Foam Works").first() is not None


def seed_demo_data(session) -> str:
    if already_seeded(session):
        return "Demo data already present - skipped (delete 'Demo Foam Works' plant to reseed)."

    plant = Plant(
        name="Demo Foam Works",
        plant_code="DFW-01",
        location="Demo location",
        notes="Fictional plant for internal demonstration only - no real client data.",
    )
    session.add(plant)
    session.flush()

    family = ProductFamily(
        plant_id=plant.id,
        name="Mattress Comfort Foam",
        application="Mattress comfort layer",
        customer_segment="Mattress OEM",
        description="Flexible slabstock foam family for mattress comfort layers.",
    )
    session.add(family)
    session.flush()

    machine = Machine(
        plant_id=plant.id,
        name="Line 1",
        machine_code="LINE-1",
        oem="Hennecke",
        model="HK-R 5000 (demo)",
        active=True,
        notes="Fictional machine for internal demonstration only.",
    )
    session.add(machine)
    session.flush()

    raw_materials = {
        m.name: m
        for m in [
            RawMaterial(name="Polyol A", category="Polyol", default_supplier="Supplier 1"),
            RawMaterial(name="Polyol B", category="Polyol", default_supplier="Supplier 4"),
            RawMaterial(name="TDI 80/20", category="Isocyanate", default_supplier="Supplier 2"),
            RawMaterial(name="Water", category="Blowing agent", default_supplier="Internal"),
            RawMaterial(name="Catalyst Blend 1", category="Catalyst", default_supplier="Supplier 3"),
            RawMaterial(name="Surfactant S1", category="Surfactant", default_supplier="Supplier 3"),
        ]
    }
    session.add_all(raw_materials.values())
    session.flush()

    grade_28mh = FoamGrade(
        product_family_id=family.id,
        grade_name="28 kg/m3 Medium Hardness",
        target_density=28.0,
        target_hardness=140.0,
        notes="Primary grade in the demonstration case. No visible shrinkage after cure.",
    )
    grade_32fh = FoamGrade(
        product_family_id=family.id,
        grade_name="32 kg/m3 Firm",
        target_density=32.0,
        target_hardness=180.0,
        notes="Second grade - included to show the family covers more than one grade.",
    )
    session.add_all([grade_28mh, grade_32fh])
    session.flush()

    v04 = RecipeVersion(
        foam_grade_id=grade_28mh.id,
        version_label="28-MH-04",
        effective_date=dt.date.today() - dt.timedelta(days=90),
        change_note="Baseline formulation, stable for over a year prior to the raw material substitution.",
        approval_status="Approved",
        created_by="R&D",
        is_active=False,
        ratio_index=0.90,
    )
    session.add(v04)
    session.flush()
    session.add_all(
        [
            RecipeComponent(recipe_version_id=v04.id, raw_material_id=raw_materials["Polyol A"].id, raw_material_name="Polyol A", supplier="Supplier 1", php=100, role_in_formulation="Base polyol"),
            RecipeComponent(recipe_version_id=v04.id, raw_material_id=raw_materials["TDI 80/20"].id, raw_material_name="TDI 80/20", supplier="Supplier 2", php=45, role_in_formulation="Isocyanate"),
            RecipeComponent(recipe_version_id=v04.id, raw_material_id=raw_materials["Water"].id, raw_material_name="Water", supplier="Internal", php=3.2, role_in_formulation="Blowing agent"),
            RecipeComponent(recipe_version_id=v04.id, raw_material_id=raw_materials["Catalyst Blend 1"].id, raw_material_name="Catalyst Blend 1", supplier="Supplier 3", php=0.3, role_in_formulation="Catalyst"),
            RecipeComponent(recipe_version_id=v04.id, raw_material_id=raw_materials["Surfactant S1"].id, raw_material_name="Surfactant S1", supplier="Supplier 3", php=1.0, role_in_formulation="Surfactant"),
        ]
    )

    v05 = RecipeVersion(
        foam_grade_id=grade_28mh.id,
        version_label="28-MH-05",
        effective_date=dt.date.today() - dt.timedelta(days=42),
        change_note="Raw material substitution (Polyol A -> Polyol B) due to supplier availability. Coincides with onset of hardness drift and shrinkage.",
        approval_status="Approved",
        created_by="R&D",
        is_active=False,
        ratio_index=0.95,
    )
    session.add(v05)
    session.flush()
    session.add_all(
        [
            RecipeComponent(recipe_version_id=v05.id, raw_material_id=raw_materials["Polyol B"].id, raw_material_name="Polyol B", supplier="Supplier 4", php=100, role_in_formulation="Base polyol (substituted)"),
            RecipeComponent(recipe_version_id=v05.id, raw_material_id=raw_materials["TDI 80/20"].id, raw_material_name="TDI 80/20", supplier="Supplier 2", php=45, role_in_formulation="Isocyanate"),
            RecipeComponent(recipe_version_id=v05.id, raw_material_id=raw_materials["Water"].id, raw_material_name="Water", supplier="Internal", php=3.2, role_in_formulation="Blowing agent"),
            RecipeComponent(recipe_version_id=v05.id, raw_material_id=raw_materials["Catalyst Blend 1"].id, raw_material_name="Catalyst Blend 1", supplier="Supplier 3", php=0.3, role_in_formulation="Catalyst"),
            RecipeComponent(recipe_version_id=v05.id, raw_material_id=raw_materials["Surfactant S1"].id, raw_material_name="Surfactant S1", supplier="Supplier 3", php=1.0, role_in_formulation="Surfactant"),
        ]
    )

    v06 = RecipeVersion(
        foam_grade_id=grade_28mh.id,
        version_label="28-MH-06",
        effective_date=dt.date.today() - dt.timedelta(days=7),
        change_note="Catalyst balance and cure/cutting timing adjusted following trial series T1-T5.",
        approval_status="Draft",
        created_by="Technical Manager",
        is_active=True,
        ratio_index=1.05,
    )
    session.add(v06)
    session.flush()

    # ---- Trial series T1 - T5, matching the demo case table -------------
    trial_defs = [
        dict(
            recipe=v05,
            objective="Baseline formulation reviewed; no change made.",
            hypothesis="Confirm whether hardness drift is random or systematic.",
            what_changed="No change.",
            responsible_person="Technical Manager",
            humidity=68.0,
            observation="Low load-bearing / hardness values",
            severity="Medium",
            frequency="Recurring",
            suspected_cause="Unknown - under investigation.",
            confidence="Unconfirmed",
            result_against_target="Hardness below target in 2 of 4 blocks tested.",
            physical_property_outcome="Hardness averaged 121 N vs target 140 N; density within tolerance.",
            hardness_actual=121.0,
            conclusion="Issue is not random; continue structured review.",
            reuse_recommendation="Do not assume one-off variation; treat as a recurring pattern requiring root investigation.",
            confidence_adj="Confirmed",
        ),
        dict(
            recipe=v05,
            objective="Adjust catalyst balance to test effect on rise profile and shrinkage.",
            hypothesis="Catalyst balance affects cure speed and may relate to shrinkage.",
            what_changed="Increased catalyst blend slightly.",
            responsible_person="R&D",
            humidity=70.0,
            observation="Shrinkage",
            severity="Medium",
            frequency="Recurring",
            suspected_cause="Catalyst balance and cure timing.",
            confidence="Likely",
            result_against_target="Rise profile improved; shrinkage still present after cure.",
            physical_property_outcome="Hardness improved slightly to 128 N; shrinkage unchanged.",
            hardness_actual=128.0,
            conclusion="Catalyst alone is unlikely to be the full cause.",
            reuse_recommendation="Do not rely on catalyst adjustment alone for this defect pattern.",
            confidence_adj="Likely",
        ),
        dict(
            recipe=v05,
            objective="Review surfactant and water level under the same process conditions.",
            hypothesis="Cell structure changes from the substitution may be contributing.",
            what_changed="Adjusted surfactant level; water level unchanged.",
            responsible_person="R&D",
            humidity=71.0,
            observation="Low load-bearing / hardness values",
            severity="Medium",
            frequency="Recurring",
            suspected_cause="Cell structure interaction with new polyol.",
            confidence="Likely",
            result_against_target="Cell structure improved; hardness still below target.",
            physical_property_outcome="Hardness 132 N vs target 140 N.",
            hardness_actual=132.0,
            conclusion="Formulation contributes, but process condition remains relevant.",
            reuse_recommendation="Treat formulation and process condition as combined factors, not formulation alone.",
            confidence_adj="Likely",
        ),
        dict(
            recipe=v05,
            objective="Control curing and cutting timing to isolate process effect.",
            hypothesis="Delayed cutting under high humidity contributes to shrinkage.",
            what_changed="Standardized cure duration and cutting timing.",
            responsible_person="Plant Manager",
            humidity=72.0,
            observation="Shrinkage",
            severity="Low",
            frequency="One-off",
            suspected_cause="Delayed cutting combined with high ambient humidity.",
            confidence="Confirmed",
            result_against_target="Shrinkage reduced; hardness closer to target.",
            physical_property_outcome="Hardness 137 N vs target 140 N; shrinkage not observed.",
            hardness_actual=137.0,
            conclusion="Timing and post-rise handling are part of the issue.",
            reuse_recommendation="Standardize cure/cutting timing whenever ambient humidity exceeds ~70%.",
            confidence_adj="Confirmed",
        ),
        dict(
            recipe=v06,
            objective="Recheck raw material substitution against earlier confirmed case, with adjusted catalyst and timing together.",
            hypothesis="Combined effect of substitution, humidity, and cure/cutting timing explains the full pattern.",
            what_changed="Applied catalyst adjustment and standardized cure/cutting timing together.",
            responsible_person="Technical Manager",
            humidity=65.0,
            observation="Low load-bearing / hardness values",
            severity="Low",
            frequency="One-off",
            suspected_cause="Raw material substitution combined with humidity/cure interaction.",
            confidence="Confirmed",
            result_against_target="Hardness within target range; no shrinkage observed.",
            physical_property_outcome="Hardness 141 N vs target 140 N; density 28.3 kg/m3.",
            hardness_actual=141.0,
            conclusion="Combined adjustment (catalyst + cure/cutting timing) resolves the pattern for this substitution.",
            reuse_recommendation="When substituting this polyol, apply the catalyst adjustment and standardized cure/cutting timing together; monitor humidity.",
            confidence_adj="Confirmed",
        ),
    ]

    created_trials = []
    for i, d in enumerate(trial_defs, start=1):
        run = ProductionRun(
            plant_id=plant.id,
            foam_grade_id=grade_28mh.id,
            recipe_version_id=d["recipe"].id,
            run_date=dt.date.today() - dt.timedelta(days=(6 - i) * 7),
            batch_reference=f"BATCH-{i:03d}",
            block_reference=f"BLK-{i:03d}",
            machine_id=machine.id,
            operator_or_team_reference="Demo team",
            notes="Demo data - not a real production run.",
        )
        session.add(run)
        session.flush()

        # Finalized-phase machine settings. rise_time lives here (not on the
        # retired RuntimeDataRecord) as of 2026-08-02 - see db.py's
        # ProductionPhase. curing_notes was removed app-wide as of
        # 2026-08-03 (not a real field per user feedback). ratio_index
        # moved to RecipeVersion as of
        # 2026-08-03 (see v04/v05/v06 above) - it now climbs across the
        # trial series via the recipe change (v05 -> v06 at trial 5)
        # alongside the hardness recovery (121 -> 141 N), rather than via a
        # per-phase value, which is the physically accurate picture (the
        # ratio/index is fixed by the recipe in use, not set per run).
        phase_start = dt.datetime.combine(run.run_date, dt.time(8, 0))
        session.add(
            ProductionPhase(
                production_run_id=run.id,
                phase_name="Finalized",
                phase_start=phase_start,
                phase_end=phase_start + dt.timedelta(hours=8),
                mixer_rpm=58 + i * 0.5,
                conveyor_speed=3.1 + i * 0.025,
                air_injection_rate=12.0 + i * 0.1,
                air_pressure_bar=2.1 + i * 0.03,
                foam_height_mm=195 + i * 2.5,
                sidewall_width_mm=1180,
                ambient_temperature_c=24.0,
                ambient_humidity_pct=d["humidity"],
                rise_time=95.0,
                foaming_mode="LLD",
                top_flat_system_used=True,
                notes="Demo data - not a real production run.",
                source_file_reference="demo seed",
            )
        )

        # Trial-level narrative (objective/hypothesis/what-changed/etc.) used
        # to live on a TrialRecord attached to this production run, with
        # AdjustmentConclusion/ApprovalRecord hanging off it. That workflow
        # was removed 2026-08-04 as redundant with the Production Run /
        # Customer Trial / Optimization Trial model - see db.py. The
        # production run itself, plus its quality test results and quality
        # issue, are the parts of this demo scenario that map onto the
        # current schema; the narrative fields in trial_defs are kept only
        # as source material for run.notes below.
        session.add_all(
            [
                PhysicalPropertyResult(
                    production_run_id=run.id, property_name="Density", target_value=28.0,
                    actual_value=28.0 + (i * 0.05), unit="kg/m3", pass_fail="Pass",
                    test_method="ISO 845", tested_at=run.run_date,
                ),
                PhysicalPropertyResult(
                    production_run_id=run.id, property_name="Hardness",
                    target_value=140.0,
                    actual_value=d["hardness_actual"],
                    unit="N", pass_fail="Pass" if i == 5 else "Fail",
                    test_method="ISO 2439", tested_at=run.run_date,
                ),
            ]
        )

        session.add(
            QualityObservation(
                production_run_id=run.id,
                observation_type=d["observation"],
                severity=d["severity"],
                frequency=d["frequency"],
                location_in_block="General block",
                suspected_cause=d["suspected_cause"],
                confidence_level=d["confidence"],
                product_impact="Comfort feel and cutting yield affected while unresolved.",
                customer_impact="Risk of customer-reported softness/inconsistency if shipped.",
                observed_at=run.run_date,
            )
        )

        created_trials.append(run)

    # ---- Routine production runs (no trial at all) -----------------------
    # These demonstrate the primary path: a normal batch gets a recipe,
    # machine parameters, and quality results without ever touching the
    # trial/experiment apparatus above.
    for j in range(1, 3):
        routine_run = ProductionRun(
            plant_id=plant.id,
            foam_grade_id=grade_28mh.id,
            recipe_version_id=v06.id,
            run_date=dt.date.today() - dt.timedelta(days=j),
            batch_reference=f"BATCH-R{j:03d}",
            block_reference=f"BLK-R{j:03d}",
            machine_id=machine.id,
            operator_or_team_reference="Demo team",
            notes="Demo data - routine batch, not a trial.",
        )
        session.add(routine_run)
        session.flush()

        routine_phase_start = dt.datetime.combine(routine_run.run_date, dt.time(8, 0))
        session.add(
            ProductionPhase(
                production_run_id=routine_run.id,
                phase_name="Finalized",
                phase_start=routine_phase_start,
                phase_end=routine_phase_start + dt.timedelta(hours=8),
                mixer_rpm=60.5,
                conveyor_speed=3.22,
                air_injection_rate=12.6,
                air_pressure_bar=2.28,
                foam_height_mm=207.5,
                sidewall_width_mm=1180,
                ambient_temperature_c=24.0,
                ambient_humidity_pct=60.0,
                rise_time=95.0,
                foaming_mode="Trough",
                top_flat_system_used=False,
                notes="Demo data - routine batch, not a trial.",
                source_file_reference="demo seed",
            )
        )
        session.add_all(
            [
                PhysicalPropertyResult(
                    production_run_id=routine_run.id, property_name="Density", target_value=28.0,
                    actual_value=28.1, unit="kg/m3", pass_fail="Pass",
                    test_method="ISO 845", tested_at=routine_run.run_date,
                ),
                PhysicalPropertyResult(
                    production_run_id=routine_run.id, property_name="Hardness", target_value=140.0,
                    actual_value=139.0, unit="N", pass_fail="Pass",
                    test_method="ISO 2439", tested_at=routine_run.run_date,
                ),
            ]
        )
        session.add(
            QualityObservation(
                production_run_id=routine_run.id,
                observation_type="No issue found - routine check",
                severity="Low",
                frequency="One-off",
                location_in_block="General block",
                confidence_level="Confirmed",
                observed_at=routine_run.run_date,
            )
        )

    # ---- Customer Trial demonstration (independent lab-trial flow, added
    # 2026-08-03 - see db.py's CustomerTrial). A small lab-scale box made
    # for a specific sales opportunity, with no machine/process settings
    # behind it at all: no ProductionRun, no ProductionPhase. Samples,
    # quality test results, and quality issues all key off
    # customer_trial_id instead of production_run_id, exercising the same
    # "3 sample source types" pipeline the Intelligence pages' "include
    # trials" toggle (Trend Analysis, Recipe Optimization) reads from.
    # -----------------------------------------------------------------------
    customer_trial = CustomerTrial(
        plant_id=plant.id,
        foam_grade_id=grade_28mh.id,
        recipe_version_id=v06.id,
        customer_name="Rest Well Bedding Co.",
        sales_opportunity_reference="OPP-2026-0143",
        requested_by="Rest Well Bedding Co. - Purchasing",
        trial_objective="Evaluate the current 28-MH-06 formulation for a new mattress SKU before committing to a purchase order.",
        responsible_person="Technical Manager",
        trial_date=dt.date.today() - dt.timedelta(days=3),
        batch_reference="CT-BOX-001",
        status="Closed",
        outcome="Density and hardness both met the customer's target range; sample approved for the next stage of qualification.",
        customer_feedback="Comfort feel matched their reference sample; no shrinkage observed after 48h.",
        follow_up_action="Send formal quote and lead time for a full production trial run.",
        reviewed_by="Technical Manager",
        date_closed=dt.date.today() - dt.timedelta(days=1),
        notes="Demo data - lab-scale customer trial, not a production run.",
    )
    session.add(customer_trial)
    session.flush()

    customer_sample = Sample(
        customer_trial_id=customer_trial.id,
        sample_ts=dt.datetime.combine(customer_trial.trial_date, dt.time(10, 0)),
        zone_label="Whole sample",
        notes="Demo data - lab box sample for customer trial.",
    )
    session.add(customer_sample)
    session.flush()

    session.add_all(
        [
            PhysicalPropertyResult(
                customer_trial_id=customer_trial.id, sample_id=customer_sample.id, property_name="Density",
                target_value=28.0, actual_value=28.2, unit="kg/m3", pass_fail="Pass",
                test_method="ISO 845", tested_at=customer_trial.trial_date,
            ),
            PhysicalPropertyResult(
                customer_trial_id=customer_trial.id, sample_id=customer_sample.id, property_name="Hardness",
                target_value=140.0, actual_value=142.0, unit="N", pass_fail="Pass",
                test_method="ISO 2439", tested_at=customer_trial.trial_date,
            ),
        ]
    )
    session.add(
        QualityObservation(
            customer_trial_id=customer_trial.id,
            observation_type="No issue found - routine check",
            severity="Low",
            frequency="One-off",
            location_in_block="Whole sample",
            confidence_level="Confirmed",
            product_impact="None - sample met target on first trial.",
            observed_at=customer_trial.trial_date,
        )
    )

    # ---- Optimization Trial demonstration (the second independent
    # lab-trial flow - stems from a Performance Improvement initiative
    # related to, but independent of, the Intelligence section's own
    # analysis; same "no machine/process settings behind it" shape as
    # CustomerTrial above, just triggered internally rather than by a
    # customer). -------------------------------------------------------
    optimization_trial = OptimizationTrial(
        plant_id=plant.id,
        foam_grade_id=grade_28mh.id,
        recipe_version_id=v06.id,
        improvement_initiative_reference="PI-2026-0027",
        hypothesis="A small further reduction in catalyst level can trim cost without moving hardness/density outside tolerance.",
        what_changed="Catalyst blend reduced by 5% relative to 28-MH-06.",
        responsible_person="R&D",
        trial_date=dt.date.today() - dt.timedelta(days=2),
        batch_reference="OT-BOX-001",
        status="Closed",
        result_against_target="Hardness and density both remained within tolerance at the reduced catalyst level.",
        conclusion="The catalyst reduction is viable for this grade without a measurable quality impact.",
        reuse_recommendation="Carry the 5% catalyst reduction into the next recipe version review for this grade.",
        reviewed_by="Technical Manager",
        approved_by="Plant Manager",
        date_closed=dt.date.today(),
        notes="Demo data - lab-scale optimization trial, not a production run.",
    )
    session.add(optimization_trial)
    session.flush()

    optimization_sample = Sample(
        optimization_trial_id=optimization_trial.id,
        sample_ts=dt.datetime.combine(optimization_trial.trial_date, dt.time(10, 0)),
        zone_label="Whole sample",
        notes="Demo data - lab box sample for optimization trial.",
    )
    session.add(optimization_sample)
    session.flush()

    session.add_all(
        [
            PhysicalPropertyResult(
                optimization_trial_id=optimization_trial.id, sample_id=optimization_sample.id, property_name="Density",
                target_value=28.0, actual_value=27.9, unit="kg/m3", pass_fail="Pass",
                test_method="ISO 845", tested_at=optimization_trial.trial_date,
            ),
            PhysicalPropertyResult(
                optimization_trial_id=optimization_trial.id, sample_id=optimization_sample.id, property_name="Hardness",
                target_value=140.0, actual_value=138.0, unit="N", pass_fail="Pass",
                test_method="ISO 2439", tested_at=optimization_trial.trial_date,
            ),
        ]
    )
    session.add(
        QualityObservation(
            optimization_trial_id=optimization_trial.id,
            observation_type="No issue found - routine check",
            severity="Low",
            frequency="One-off",
            location_in_block="Whole sample",
            confidence_level="Confirmed",
            product_impact="None - reduced-catalyst sample met target.",
            observed_at=optimization_trial.trial_date,
        )
    )

    session.flush()

    session.commit()
    return (
        "Demo data created: 1 plant, 1 product family, 2 foam grades, 3 recipe versions, "
        "5 production runs with quality test results and a quality issue each, "
        "2 routine production runs with quality results and no issue at all, plus 1 closed "
        "Customer Trial and 1 closed Optimization Trial (each with its own sample and quality "
        "results, independent of any production run)."
    )


if __name__ == "__main__":
    init_db()
    s = get_session()
    print(seed_demo_data(s))
