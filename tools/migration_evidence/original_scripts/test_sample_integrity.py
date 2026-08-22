"""Same-source sample integrity for quality test results.

Charlie's rule of 21 August 2026: every Quality Test Result must reference a
sample, and that sample must belong to the SAME source record as the result -
for production runs, customer trials and optimization trials alike.

These cases are the six he listed, plus the import path and the exactly-one-
source rule. Deterministic: no model call, no network. Run with
`python3 sample_integrity.py`.
"""
import sys
sys.path.insert(0, '.')
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import db as m

PASS, FAIL = [], []
def check(case, expect, got, detail=""):
    ok = expect == got
    (PASS if ok else FAIL).append(case)
    print(f'  [{"PASS" if ok else "FAIL"}] {case}\n         expected {expect!r}, got {got!r}'
          + (f'\n         {detail}' if detail else ''))


def fresh():
    eng = sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    m.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def build():
    """Two production runs, one customer trial, one optimization trial, each
    with its own sample. Enough to test 'matching' and 'foreign' for all three."""
    s = fresh()
    s.add(m.Company(id=1, name="UAT Foam Co"))
    s.add(m.Plant(id=1, company_id=1, name="UAT Plant"))
    s.add(m.ProductFamily(id=1, plant_id=1, name="UAT Family"))
    s.add(m.FoamGrade(id=1, product_family_id=1, grade_name="UAT-SDE-01"))
    s.add(m.RecipeVersion(id=1, foam_grade_id=1, version_label="v1", is_active=True))
    s.flush()
    s.add(m.ProductionRun(id=1, plant_id=1, foam_grade_id=1, recipe_version_id=1))
    s.add(m.ProductionRun(id=2, plant_id=1, foam_grade_id=1, recipe_version_id=1))
    s.add(m.CustomerTrial(id=1, plant_id=1, foam_grade_id=1, recipe_version_id=1,
                          customer_name="UAT Customer"))
    s.add(m.OptimizationTrial(id=1, plant_id=1, foam_grade_id=1, recipe_version_id=1))
    s.flush()
    s.add(m.Sample(id=10, production_run_id=1, zone_label="Top"))
    s.add(m.Sample(id=11, production_run_id=2, zone_label="Top"))
    s.add(m.Sample(id=20, customer_trial_id=1, zone_label="Whole sample / N/A"))
    s.add(m.Sample(id=30, optimization_trial_id=1, zone_label="Whole sample / N/A"))
    s.commit()
    return s


print("=" * 78)
print("A. THE SIX CASES — matching sample accepted, foreign or missing rejected")
print("=" * 78)

s = build()
v = m.validate_quality_result_sample

print("\nA1. Production Run")
check("production run + matching sample: accepted",
      None, v(s, 10, production_run_id=1))
check("production run + foreign sample: rejected",
      True, v(s, 11, production_run_id=1) is not None)
check("production run + missing sample: rejected",
      True, v(s, None, production_run_id=1) is not None)

print("\nA2. Customer Trial")
check("customer trial + matching sample: accepted",
      None, v(s, 20, customer_trial_id=1))
check("customer trial + foreign sample: rejected",
      True, v(s, 10, customer_trial_id=1) is not None)
check("customer trial + missing sample: rejected",
      True, v(s, None, customer_trial_id=1) is not None)

print("\nA3. Optimization Trial")
check("optimization trial + matching sample: accepted",
      None, v(s, 30, optimization_trial_id=1))
check("optimization trial + foreign sample: rejected",
      True, v(s, 10, optimization_trial_id=1) is not None)
check("optimization trial + missing sample: rejected",
      True, v(s, None, optimization_trial_id=1) is not None)

print("\n" + "=" * 78)
print("B. EXACTLY ONE SOURCE")
print("=" * 78)
check("no source set: rejected", True, v(s, 10) is not None)
check("two sources set: rejected", True,
      v(s, 10, production_run_id=1, customer_trial_id=1) is not None)
check("three sources set: rejected", True,
      v(s, 10, production_run_id=1, customer_trial_id=1, optimization_trial_id=1) is not None)
check("a sample that does not exist: rejected", True,
      v(s, 999, production_run_id=1) is not None)

print("\n" + "=" * 78)
print("C. THE MESSAGE NAMES WHERE THE SAMPLE ACTUALLY BELONGS")
print("=" * 78)
msg = v(s, 20, production_run_id=1)
check("foreign-sample message names the sample", True, "#20" in msg, msg)
check("foreign-sample message names its real owner", True, "customer trial #1" in msg, msg)
check("foreign-sample message names what the result claimed", True, "production run #1" in msg, msg)

print("\n" + "=" * 78)
print("D. sample_id IS MANDATORY IN THE MODEL, NOT ONLY IN THE FORM")
print("=" * 78)
col = m.PhysicalPropertyResult.__table__.columns["sample_id"]
check("physical_property_results.sample_id is NOT NULL", False, col.nullable)

s2 = build()
s2.add(m.PhysicalPropertyResult(property_name="Density", production_run_id=1, sample_id=None))
try:
    s2.commit()
    inserted = True
except Exception:
    s2.rollback()
    inserted = False
check("the database refuses a result with no sample", False, inserted)

print("\n" + "=" * 78)
print("E. IMPORT USES THE SAME RULE — no second opinion")
print("=" * 78)
src = open('views/5_Physical_Property_Result.py').read()
check("the add form calls the shared validator", True,
      src.count("validate_quality_result_sample") >= 3,
      "add, edit and import must all route through it")
check("no 'not linked to a sample' option survives anywhere", 0,
      src.count("not linked to a sample"))
check("the edit form no longer offers an optional sample", 0,
      src.count("Sample (optional)"))
check("the validator is the only place the rule is written for the app", 1,
      open('db.py').read().count("def validate_quality_result_sample"))

# The import path builds its own scope dict then defers to the validator; prove
# both gates are present rather than one having replaced the other.
check("import keeps the tenancy gate as well as the relationship gate", True,
      "in samples_all" in src and "validate_quality_result_sample" in src)

print("\n" + "=" * 78)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:"); [print("  -", f) for f in FAIL]
print("=" * 78)
sys.exit(1 if FAIL else 0)
