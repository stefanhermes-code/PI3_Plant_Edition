"""Industry accepted tolerances for physical property Pass/Fail.

Added 2026-08-01, replacing a flat +/-10%-of-target band that was applied
uniformly to every property regardless of what it actually measures.
INDUSTRY_TOLERANCES below holds a published/accepted foam-testing tolerance
per property (aligned to ASTM D-3574 test methods) rather than an
assumption.

Corrected 2026-08-01 (twice, same day, before this was noticed in review):
every tolerance value in INDUSTRY_TOLERANCES is a PERCENTAGE OF THE TARGET
VALUE (relative), not a fixed amount in the property's own unit. The first
correction only applied this to the 3 properties whose own measurement unit
is "%" (Elongation at break, Ball rebound resilience, Compression set);
per explicit direction, the relative-to-target interpretation applies
uniformly to ALL published tolerances, including Density, 40% IFD/hardness,
Tensile strength, and Airflow/air permeability - none of these numbers are
a fixed amount in kg/m3, N, kPa, or cfm. Example: Ball rebound resilience
target 48%, tolerance 5 -> allowed band is target +/- (5% of 48) = 48 +/-
2.4, i.e. 45.6-50.4. Density target 25 kg/m3, tolerance 2 -> allowed band is
25 +/- (2% of 25) = 25 +/- 0.5 kg/m3, i.e. 24.5-25.5 - NOT 25 +/- 2.0 kg/m3,
which is what both the original and first-corrected versions of this module
computed. unit_label is retained purely to describe what unit the
property's target/actual values are themselves measured in for display
(e.g. in the Recipe Optimization "meet target" table via tolerance_label())
- it no longer affects how the tolerance number itself is interpreted.

A property with no entry here (anything outside the 7 commonly tracked
properties, e.g. an exotic technical property nobody has published a
standard tolerance for) falls back to the old +/-10%-of-target band via
_FALLBACK_RELATIVE_TOLERANCE - never left with no tolerance at all, since
target/actual are still comparable even without a documented number. This
fallback was already relative-to-target, so it is unaffected by this fix
and is now consistent with every other property's interpretation.

Single source of truth: every Pass/Fail decision in the app (data entry,
CSV import, edit, and any future recompute) should call compute_pass_fail()
below rather than re-deriving a band inline, so this policy only ever
lives in one place.
"""

# property_name -> (tolerance_pct, unit_label). tolerance_pct is ALWAYS a
# percentage of the target value (relative), applied as
# target +/- (target * tolerance_pct / 100) - see compute_pass_fail().
# unit_label is for display only (what unit the property's target/actual
# values are measured in, e.g. in the Recipe Optimization "meet target"
# table via tolerance_label()); it is NOT used to convert or validate the
# unit actually recorded on a result (that's a separate free-text field -
# see db.py's PhysicalPropertyResult.unit), and no longer affects whether
# the tolerance is relative or absolute - every entry here is relative.
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
    """Returns the published tolerance PERCENTAGE (of target) for this
    property_name, or None if there's no published industry accepted
    tolerance for it (caller should fall back to a relative band - see
    compute_pass_fail). Not yet converted to an absolute +/- amount for a
    given target - use compute_pass_fail() or _tolerance_band() for that."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    return entry[0] if entry else None


def _tolerance_band(property_name, target_value):
    """Returns (lower, upper) for property_name given target_value, or None
    if there's no published tolerance (caller falls back to the relative
    band). Every published tolerance is a percentage of target_value."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    if not entry:
        return None
    tol_pct, _unit = entry
    allowed = abs(target_value) * (tol_pct / 100.0)
    return target_value - allowed, target_value + allowed


def tolerance_label(property_name):
    """Human-readable '± X% of target' string for display, or
    '± 10% of target (no industry tolerance published)' for anything
    falling back to the relative band. Every published tolerance is now
    expressed the same way, regardless of the property's own unit."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    if entry:
        tol_pct, _unit = entry
        tol_text = f"{tol_pct:g}"
        return f"± {tol_text}% of target"
    return "± 10% of target (no industry tolerance published)"


def compute_pass_fail(property_name, target_value, actual_value):
    """Pass/Fail against the industry accepted tolerance for property_name,
    or the +/-10%-of-target fallback band if none is published. Returns
    None if target or actual is missing/zero (nothing to compare)."""
    if not target_value or actual_value is None:
        return None
    band = _tolerance_band(property_name, target_value)
    if band is not None:
        lower, upper = band
    else:
        lower, upper = target_value * (1 - _FALLBACK_RELATIVE_TOLERANCE), target_value * (1 + _FALLBACK_RELATIVE_TOLERANCE)
    return "Pass" if lower <= actual_value <= upper else "Fail"
