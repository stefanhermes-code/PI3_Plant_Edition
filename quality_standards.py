"""Industry accepted tolerances for physical property Pass/Fail.

Added 2026-08-01, replacing a flat +/-10%-of-target band that was applied
uniformly to every property regardless of what it actually measures.
INDUSTRY_TOLERANCES below holds a published/accepted foam-testing tolerance
per property (aligned to ASTM D-3574 test methods) rather than an
assumption.

Each property is tagged with HOW its tolerance number is interpreted,
because that genuinely differs by property rather than being one uniform
rule - conflating the two is exactly what went wrong here across three
rounds of correction on 2026-08-01/02:
- "relative": the number is a PERCENTAGE OF THE TARGET VALUE. Example: Ball
  rebound resilience target 48%, tolerance 5 -> allowed band is target +/-
  (5% of 48) = 48 +/- 2.4, i.e. 45.6-50.4.
- "absolute": the number is a FIXED AMOUNT in the property's own unit,
  independent of the target's magnitude. Example: Density target 25 kg/m3,
  tolerance 2 -> allowed band is 25 +/- 2.0 kg/m3, i.e. 23.0-27.0. Density
  is the one property confirmed to work this way (a plant doesn't get a
  wider allowance just because it's targeting a higher-density grade), not
  as a percentage of target.

unit_label is retained purely to describe what unit the property's
target/actual values are themselves measured in for display (e.g. in the
Recipe Optimization "meet target" table via tolerance_label()) - for an
"absolute" property it is also the unit the tolerance number itself is
in; for a "relative" property the tolerance number is dimensionless (a
percentage), regardless of unit_label.

Single source of truth: every Pass/Fail decision in the app should call
compute_pass_fail() below rather than re-deriving a band inline, so this
policy only ever lives in one place. Just as importantly, nothing should
persist a Pass/Fail verdict and trust it forever - a stored value only
ever reflects the tolerance rule in effect at the moment it was written,
so every screen that reports a pass rate or an Achieved/Not-achieved
verdict must call compute_pass_fail() live from target_value/actual_value
at read time (see analytics.property_results_dataframe, app.py's
dashboard KPI, and reports.py's report builders). That is what makes a
tolerance correction here take effect everywhere immediately, without a
separate recompute step every time this table changes.
"""

# property_name -> (mode, tolerance_value, unit_label).
# mode "relative": tolerance_value is a percentage of the target value,
# applied as target +/- (target * tolerance_value / 100).
# mode "absolute": tolerance_value is a fixed amount in unit_label, applied
# as target +/- tolerance_value regardless of the target's magnitude.
INDUSTRY_TOLERANCES = {
    "Density": ("absolute", 2.0, "kg/m3"),
    "40% IFD / hardness": ("relative", 20.0, "N"),
    "Tensile strength": ("relative", 10.0, "kPa"),
    "Elongation at break": ("relative", 10.0, "%"),
    "Ball rebound resilience": ("relative", 5.0, "%"),
    "Compression set": ("relative", 1.0, "%"),
    "Airflow / air permeability": ("relative", 10.0, "cfm"),
}

_FALLBACK_RELATIVE_TOLERANCE = 0.10  # +/-10% of target, for any property not listed above


def industry_tolerance_for(property_name):
    """Returns the (mode, tolerance_value) published for this property_name,
    or None if there's no published industry accepted tolerance for it
    (caller should fall back to a relative band - see compute_pass_fail).
    Not yet converted to an absolute +/- amount for a given target - use
    compute_pass_fail() or _tolerance_band() for that."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    return (entry[0], entry[1]) if entry else None


def _tolerance_band(property_name, target_value):
    """Returns (lower, upper) for property_name given target_value, or None
    if there's no published tolerance (caller falls back to the relative
    band)."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    if not entry:
        return None
    mode, tol_value, _unit = entry
    allowed = abs(tol_value) if mode == "absolute" else abs(target_value) * (tol_value / 100.0)
    return target_value - allowed, target_value + allowed


def tolerance_label(property_name):
    """Human-readable tolerance string for display: '± X <unit>' for an
    absolute tolerance, '± X% of target' for a relative one, or
    '± 10% of target (no industry tolerance published)' for anything
    falling back to the relative band."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    if entry:
        mode, tol_value, unit = entry
        tol_text = f"{tol_value:g}"
        if mode == "absolute":
            return f"± {tol_text} {unit}"
        return f"± {tol_text}% of target"
    return "± 10% of target (no industry tolerance published)"


def compute_pass_fail(property_name, target_value, actual_value):
    """Pass/Fail against the industry accepted tolerance for property_name,
    or the +/-10%-of-target fallback band if none is published. Returns
    None if target or actual is missing/zero (nothing to compare).

    Call this at read time, not just at write time - see the module
    docstring. A Pass/Fail value computed once and stored goes stale the
    moment this tolerance table changes; every display of a pass rate or
    an Achieved/Not-achieved verdict should recompute from target_value/
    actual_value directly rather than trust a previously-stored verdict."""
    if not target_value or actual_value is None:
        return None
    band = _tolerance_band(property_name, target_value)
    if band is not None:
        lower, upper = band
    else:
        lower, upper = target_value * (1 - _FALLBACK_RELATIVE_TOLERANCE), target_value * (1 + _FALLBACK_RELATIVE_TOLERANCE)
    return "Pass" if lower <= actual_value <= upper else "Fail"
