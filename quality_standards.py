"""Industry accepted tolerances for physical property Pass/Fail.

Added 2026-08-01, replacing a flat +/-10%-of-target band that was applied
uniformly to every property regardless of what it actually measures. That
placeholder always sat somewhere between too tight and too loose depending
on the property - a percentage of target isn't how tolerance is actually
expressed in the foam industry: it's a fixed amount in the property's own
unit (a few Newtons, a couple of kg/m3), independent of what the target
happens to be. INDUSTRY_TOLERANCES below is exactly that, per property, in
the same unit the property is measured in, sourced from published/accepted
foam-testing tolerances (ASTM D-3574 methods) rather than an assumption.

A property with no entry here (anything outside the 6 commonly tracked
properties, e.g. an exotic technical property nobody has published a
standard tolerance for) falls back to the old +/-10%-of-target band via
_FALLBACK_RELATIVE_TOLERANCE - never left with no tolerance at all, since
target/actual are still comparable even without a documented number.

Single source of truth: every Pass/Fail decision in the app (data entry,
CSV import, edit, and any future recompute) should call compute_pass_fail()
below rather than re-deriving a band inline, so this policy only ever
lives in one place.
"""

# property_name -> (tolerance, unit_label) - tolerance is a +/- amount in
# the property's own unit, applied as target +/- tolerance. unit_label is
# for display only (e.g. in the Recipe Optimization "meet target" table);
# it is NOT used to convert or validate the unit actually recorded on a
# result (that's a separate free-text field - see db.py's
# PhysicalPropertyResult.unit).
INDUSTRY_TOLERANCES = {
    "Density": (2.0, "kg/m3"),
    "40% IFD / hardness": (20.0, "N"),
    "Tensile strength": (10.0, "kPa"),
    "Elongation at break": (10.0, "%"),
    "Ball rebound resilience": (5.0, "%"),
    "Compression set": (1.0, "%"),
    "Airflow / air permeability": (10.0, "cfm"),
}

_FALLBACK_RELATIVE_TOLERANCE = 0.10  # +/-10% of target, for any property not listed above


def industry_tolerance_for(property_name):
    """Returns the absolute +/- tolerance for this property_name, or None
    if there's no published industry accepted tolerance for it (caller
    should fall back to a relative band - see compute_pass_fail)."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    return entry[0] if entry else None


def tolerance_label(property_name):
    """Human-readable '+/- X unit' string for display, or '+/- 10% (no "
    "published tolerance)' for anything falling back to the relative band."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    if entry:
        tol, unit = entry
        tol_text = f"{tol:g}"
        return f"± {tol_text} {unit}"
    return "± 10% (no industry tolerance published)"


def compute_pass_fail(property_name, target_value, actual_value):
    """Pass/Fail against the industry accepted tolerance for property_name,
    or the +/-10%-of-target fallback band if none is published. Returns
    None if target or actual is missing/zero (nothing to compare)."""
    if not target_value or actual_value is None:
        return None
    tol = industry_tolerance_for(property_name)
    if tol is not None:
        lower, upper = target_value - tol, target_value + tol
    else:
        lower, upper = target_value * (1 - _FALLBACK_RELATIVE_TOLERANCE), target_value * (1 + _FALLBACK_RELATIVE_TOLERANCE)
    return "Pass" if lower <= actual_value <= upper else "Fail"
