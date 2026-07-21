"""
PI3 Plant Edition - v0.1 internal prototype
Demo data seed script.

Recreates the demonstration case from
"04_PI3_Plant_Edition_Demonstration_Case.docx": a mattress comfort foam
producer sees recurring hardness drift and block shrinkage in a 28 kg/m3
medium-hardness flexible slabstock grade after a formulation version
change. No real client data is used.

Run standalone:   python demo_data.py
Or trigger from the app: Maintenance & License Admin > Demo Data tab
(admin role only).
"""

import datetime as dt

from db import (
    AdjustmentConclusion,
    ApprovalRecord,
    FoamGrade,
    Machine,
    MaintenanceLicenseRecord,
    PhysicalPropertyResult,
    Plant,
    ProductFamily,
    ProductionPhase,
    ProductionRun,
    QualityObservation,
    RawMaterial,
    RecipeComponent,
    RecipeVersion,
    RuntimeDataRecord,
    SimilarCaseLink,
    TrialRecord,
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
        quality_specification="Density 28 +/-1.5 kg/m3, hardness 140 +/-15 N, no visible shrinkage after cure.",
        notes="Primary grade in the demonstration case.",
    )
    grade_32fh = FoamGrade(
        product_family_id=family.id,
        grade_name="32 kg/m3 Firm",
        target_density=32.0,
        target_hardness=180.0,
        quality_specification="Density 32 +/-1.5 kg/m3, hardness 180 +/-15 N.",
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
            observation="Hardness drift",
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
            observation="Block shrinkage",
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
            observation="Hardness drift",
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
            observation="Block shrinkage",
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
            observation="Hardness drift",
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

        session.add(
            RuntimeDataRecord(
                production_run_id=run.id,
                line_speed=3.2,
                pump_speed_or_flow_data="Nominal",
                temperature_data="Nominal",
                pressure_data="Nominal",
                ambient_temperature=24.0,
                ambient_humidity=d["humidity"],
                rise_time=95.0,
                curing_notes="Standard curing unless noted in trial." ,
                source_file_reference="demo seed",
            )
        )

        # Finalized-phase machine settings. ratio_index climbs steadily
        # across T1->T5 alongside the hardness recovery (121 -> 141 N),
        # illustrating exactly the process-vs-quality correlation the
        # Industrial Intelligence pages are built to surface.
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
                ratio_index=0.92 + (i - 1) * 0.0325,
                foam_height_mm=195 + i * 2.5,
                sidewall_width_mm=1180,
                notes="Demo data - not a real production run.",
                source_file_reference="demo seed",
            )
        )

        trial = TrialRecord(
            production_run_id=run.id,
            trial_or_change_objective=d["objective"],
            hypothesis=d["hypothesis"],
            what_changed=d["what_changed"],
            responsible_person=d["responsible_person"],
            status="Closed",
            result_against_target=d["result_against_target"],
            physical_property_outcome=d["physical_property_outcome"],
            conclusion=d["conclusion"],
            reuse_recommendation=d["reuse_recommendation"],
            reviewed_by="Technical Manager",
            approved_by="Plant Manager",
            date_closed=run.run_date,
        )
        session.add(trial)
        session.flush()

        session.add_all(
            [
                PhysicalPropertyResult(
                    production_run_id=run.id, trial_record_id=trial.id, property_name="Density", target_value=28.0,
                    actual_value=28.0 + (i * 0.05), unit="kg/m3", pass_fail="Pass",
                    test_method="ISO 845", tested_at=run.run_date,
                ),
                PhysicalPropertyResult(
                    production_run_id=run.id, trial_record_id=trial.id, property_name="Hardness",
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
                trial_record_id=trial.id,
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

        session.add(
            AdjustmentConclusion(
                production_run_id=run.id,
                trial_record_id=trial.id,
                parameter_changed=d["what_changed"],
                formulation_changed="catalyst" in d["what_changed"].lower() or "surfactant" in d["what_changed"].lower(),
                material_changed="Polyol B" if i == 1 else "",
                result=d["result_against_target"],
                reuse_recommendation=d["reuse_recommendation"],
                confidence_level=d["confidence_adj"],
                follow_up_required=(i < 5),
                created_by=d["responsible_person"],
            )
        )

        session.add(
            ApprovalRecord(
                production_run_id=run.id,
                trial_record_id=trial.id,
                reviewed_by="Technical Manager",
                approved_by="Plant Manager",
                approval_status="Approved",
                review_notes=f"Trial T{i} reviewed as part of hardness drift / shrinkage investigation.",
                date_reviewed=run.run_date,
                date_approved=run.run_date,
            )
        )

        created_trials.append(trial)

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

        session.add(
            RuntimeDataRecord(
                production_run_id=routine_run.id,
                line_speed=3.2,
                pump_speed_or_flow_data="Nominal",
                temperature_data="Nominal",
                pressure_data="Nominal",
                ambient_temperature=24.0,
                ambient_humidity=60.0,
                rise_time=95.0,
                curing_notes="Standard curing, routine batch.",
                source_file_reference="demo seed",
            )
        )
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
                ratio_index=1.05,
                foam_height_mm=207.5,
                sidewall_width_mm=1180,
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
                observation_type="Routine check - no issues",
                severity="Low",
                frequency="One-off",
                location_in_block="General block",
                confidence_level="Confirmed",
                observed_at=routine_run.run_date,
            )
        )

    session.flush()
    # Link the final resolving trial (T5) as the similar case for T1
    session.add(
        SimilarCaseLink(
            source_trial_id=created_trials[0].id,
            linked_trial_id=created_trials[-1].id,
            similarity_basis="foam_grade, observation_type, recipe_version",
            notes="T5 provides the confirmed resolution pattern for the hardness drift first observed in T1.",
        )
    )

    session.add(
        MaintenanceLicenseRecord(
            plant_id=plant.id,
            plant_count=1,
            installation_type="Single Plant",
            deployment_type="Private / restricted company environment",
            license_value=15000.0,
            annual_maintenance_percentage=18.0,
            annual_maintenance_value=15000.0 * 0.18,
            maintenance_start_date=dt.date.today(),
            renewal_date=dt.date.today().replace(year=dt.date.today().year + 1),
        )
    )

    session.commit()
    return (
        "Demo data created: 1 plant, 1 product family, 2 foam grades, 3 recipe versions, "
        "5 closed trials (with full closeout, quality observations, adjustments, approvals, and "
        "1 similar-case link), plus 2 routine production runs with quality results and no trial at all."
    )


if __name__ == "__main__":
    init_db()
    s = get_session()
    print(seed_demo_data(s))
