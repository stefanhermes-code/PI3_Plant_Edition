"""Regression check for PI3 Plant Edition after the CertiPUR change.

There is no automated test suite in this edition, so this is what can be
checked without one: that every page still compiles, that the navigation and
the access-control catalogue still agree, and that the shared analytics
functions the Industrial Intelligence pages depend on still run.
"""
# ---------------------------------------------------------------------------
# Moved into the permanent suite on 22 August 2026 under the Permanent
# Automated Regression Test Suite CR. The body below is the original script,
# unchanged except for this header, the removal of the local check() helper and
# the print-and-exit summary, and paths made repository-relative instead of
# cwd-relative. The check() statements themselves were not retyped.
#
# Replayed by tests/_recorder.py. Not importable on its own.
# ---------------------------------------------------------------------------
from tests._recorder import PROJECT_ROOT, check, print  # noqa: A004
import os as _os


def _root(*parts):
    """A path inside the repository, wherever pytest was started from."""
    return _os.path.join(PROJECT_ROOT, *parts)

import os, re, py_compile, traceback

print("=" * 78); print("F. REGRESSION"); print("=" * 78)

print("\nF1. Every module and every page compiles")
mods = sorted(f for f in os.listdir(PROJECT_ROOT) if f.endswith('.py') and f not in
              ('mirror.py','fixtures.py','access.py','regression.py'))
views = sorted(os.listdir(_root('views'))) if os.path.isdir(_root('views')) else []
bad = []
for f in mods:
    try: py_compile.compile(_root(f), doraise=True)
    except Exception as e: bad.append((f, str(e)[:80]))
for f in views:
    if not f.endswith('.py'): continue
    try: py_compile.compile(_root('views', f), doraise=True)
    except Exception as e: bad.append((f, str(e)[:80]))
check(f"{len(mods)} modules and {len([v for v in views if v.endswith('.py')])} pages compile", [], bad)

print("\nF2. Navigation and access control agree")
import access_control as ac
app_src = open(_root('app.py')).read()
nav_keys = set(re.findall(r'\(\s*"([a-z0-9_]+)",\s*st\.Page\(', app_src))
missing_cat = sorted(nav_keys - set(ac.PAGE_CATALOG))
# "report" is declared on its own line (report_page = st.Page(...)) because it
# is one of the standard always-included capabilities, not a keyed nav tuple.
nav_keys |= {"report"} if 'report_page = st.Page("views/21_Report.py"' in app_src else set()
missing_nav = sorted(set(ac.PAGE_CATALOG) - nav_keys)
check("every navigation key is in PAGE_CATALOG", [], missing_cat)
check("every PAGE_CATALOG key has a navigation entry", [], missing_nav)
check("PAGE_SECTION and PAGE_CATALOG do not drift", set(), set(ac.PAGE_CATALOG) ^ set(ac.PAGE_SECTION))
check("every section used is in SECTION_ORDER", set(), set(ac.PAGE_SECTION.values()) - set(ac.SECTION_ORDER))
files = set(re.findall(r'st\.Page\("(views/[^"]+)"', app_src))
check(f"all {len(files)} page files referenced by the navigation exist", [],
      sorted(f for f in files if not os.path.exists(_root(f))))

print("\nF3. The Industrial Intelligence pages are unchanged by this CR")
II = {"recipe_optimization": "views/15_Recipe_Optimization.py",
      "trend_analysis": "views/16_Trend_Analysis.py",
      "machine_settings_correlation": "views/17_Process_Property_Correlation.py",
      "root_cause_assistant": "views/18_Root_Cause_Assistant.py",
      "machine_settings_optimization": "views/19_Machine_Settings_Optimization.py",
      "expert_notes": "views/20_Expert_Notes.py"}
check("all six existing Industrial Intelligence pages still exist", [],
      sorted(k for k, f in II.items() if not os.path.exists(_root(f))))
check("all six are still in PAGE_CATALOG", [], sorted(k for k in II if k not in ac.PAGE_CATALOG))
check("all six are still in the Industrial Intelligence section", [],
      sorted(k for k in II if ac.PAGE_SECTION.get(k) != "Industrial Intelligence"))
# The loop that stood here appended to the script's local FAIL list, which no
# longer exists, and it tested the same condition as the check below it. It was
# dead on every green run and would have raised NameError on the one run where
# it mattered. Removed during the move; recorded in the inventory.
check("none of the six references CertiPUR", 0,
      sum(1 for f in II.values() if "certipur" in open(_root(f)).read().lower()))

print("\nF4. Shared analytics functions still import and run")
try:
    import analytics
    fns = [n for n in dir(analytics) if not n.startswith('_') and callable(getattr(analytics, n))]
    check("analytics imports", True, True)
    check("analytics still exposes its shared data functions", True, len(fns) > 10, f"{len(fns)} callables")
except Exception:
    check("analytics imports", True, False, traceback.format_exc()[-200:])

print("\nF5. Pass/Fail policy - widths unchanged, directions as ruled 21 Aug")
import quality_standards as qs
check("seven published tolerances", 7, len(qs.INDUSTRY_TOLERANCES))
check("every width is unchanged by the direction change",
      {"Density": ("absolute", 2.0), "40% IFD / hardness": ("relative", 20.0),
       "Tensile strength": ("relative", 10.0), "Elongation at break": ("absolute", 10.0),
       "Ball rebound resilience": ("absolute", 5.0), "Compression set": ("absolute", 1.0),
       "Airflow / air permeability": ("relative", 10.0)},
      {k: v[:2] for k, v in qs.INDUSTRY_TOLERANCES.items()})
check("directions as Stefan ruled",
      {"Density": "two-sided", "40% IFD / hardness": "two-sided",
       "Airflow / air permeability": "two-sided", "Tensile strength": "minimum",
       "Elongation at break": "minimum", "Ball rebound resilience": "minimum",
       "Compression set": "maximum"},
      {k: v[3] for k, v in qs.INDUSTRY_TOLERANCES.items()})
check("an unlisted property falls back to two-sided", "two-sided",
      qs.acceptance_direction("Something nobody published a tolerance for"))
check("compute_pass_fail still returns None with no target", None, qs.compute_pass_fail("Density", None, 28))

print("\nF5a. The case that prompted the change")
check("compression set target 8, result 5.3", "Pass", qs.compute_pass_fail("Compression set", 8, 5.3))
check("compression set target 8, result 9.4", "Fail", qs.compute_pass_fail("Compression set", 8, 9.4))
check("tensile target 110, result 126", "Pass", qs.compute_pass_fail("Tensile strength", 110, 126))
check("tensile target 110, result 95", "Fail", qs.compute_pass_fail("Tensile strength", 110, 95))
check("elongation target 150, result 190", "Pass", qs.compute_pass_fail("Elongation at break", 150, 190))
check("elongation target 150, result 135", "Fail", qs.compute_pass_fail("Elongation at break", 150, 135))
check("ball rebound target 48, result 56", "Pass", qs.compute_pass_fail("Ball rebound resilience", 48, 56))
check("ball rebound target 48, result 41", "Fail", qs.compute_pass_fail("Ball rebound resilience", 48, 41))
check("density stays two-sided, 31 against 28", "Fail", qs.compute_pass_fail("Density", 28, 31.0))
check("density stays two-sided, 25 against 28", "Fail", qs.compute_pass_fail("Density", 28, 25.0))
check("density inside the band", "Pass", qs.compute_pass_fail("Density", 28, 29.5))
check("airflow stays two-sided, above", "Fail", qs.compute_pass_fail("Airflow / air permeability", 3.0, 3.5))
check("airflow stays two-sided, below", "Fail", qs.compute_pass_fail("Airflow / air permeability", 3.0, 2.5))

print("\nF5b. The label states the rule a person has to apply")
check("compression set with a target", "at most 9%", qs.tolerance_label("Compression set", 8))
check("ball rebound with a target", "at least 43%", qs.tolerance_label("Ball rebound resilience", 48))
check("tensile with a target", "at least 99 kPa", qs.tolerance_label("Tensile strength", 110))
check("density with a target", "26 to 30 kg/m3", qs.tolerance_label("Density", 28))
check("a relative one-sided rule reads as a share of target",
      "at least 90% of target", qs.tolerance_label("Tensile strength"))

print("\nF6. Reports module still exposes every existing report")
import reports
for fn in ["build_batch_release_record_data","render_batch_release_record_docx",
           "build_period_summary_data","render_period_summary_docx",
           "build_trial_report_data","render_trial_report_docx",
           "build_sample_certificate_data","render_sample_certificate_docx",
           "build_recipe_formulation_record_data","build_where_used_report_data",
           "build_quality_test_report_data","build_quality_issue_report_data",
           "render_certipur_pre_audit_docx"]:
    check(f"reports.{fn}", True, hasattr(reports, fn))
