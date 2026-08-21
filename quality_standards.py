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
  tolerance 2 -> allowed band is 25 +/- 2.0 kg/m3, i.e. 23.0-27.0 (a plant
  doesn't get a wider allowance just because it's targeting a
  higher-density grade). Compression set works the same way even though
  the property itself is measured in %: target 8%, tolerance 1 -> allowed
  band is 8 +/- 1 percentage point, i.e. 7%-9% - NOT 8% of the target
  value (which would be a far tighter +/- 0.08 band). Elongation at break
  and Ball rebound resilience work the same way, confirmed 2026-08-02:
  target 48% ball rebound, tolerance 5 -> allowed band is 48 +/- 5
  percentage points, i.e. 43%-53% - NOT 5% of 48 (the previous, incorrect
  "relative" reading, which gave 45.6-50.4, a much tighter and wrong
  band). The published tolerance numbers themselves (5 and 10) are
  unchanged from before this fix - only the mode moved from "relative" to
  "absolute", exactly as already done for Density and Compression set.

DIRECTION - ADDED 2026-08-21
============================
Until now every tolerance was applied as a two-sided band: target +/- allowed,
failing a value outside it in EITHER direction. Stefan's correction: "It is
correct when target is 8% and the result is 5.3% then of course it is a pass.
The tolerance is not a symmetric bandwidth for compression set. However for
Density it is. So there are different interpretations per physical property."

He is right, and the old model failed good foam. A compression set of 5.3%
against a target of 8% is a BETTER foam and was reported as a failure. So was
a tensile strength of 126 kPa against a target of 110.

Each property now carries a direction as well as a width:

  "two-sided"   a value outside the band in either direction fails. Density and
                40% IFD: too light and too heavy are both the wrong product,
                too soft and too hard are both the wrong grade. Airflow, ruled
                by Stefan 21 Aug: too tight risks shrinkage and closed cell,
                too open risks weak foam.
  "minimum"     only a value BELOW the band fails. Tensile strength, elongation
                at break and ball rebound resilience - all three are specified
                in practice as a minimum, and exceeding it is not a defect.
                Ball rebound ruled by Stefan 21 Aug against the earlier
                suggestion here that it be two-sided.
  "maximum"     only a value ABOVE the band fails. Compression set - lower is
                better foam.

THESE ARE HTC'S DEFAULTS, NOT A LAW
===================================
Stefan, 21 Aug 2026: "In general tolerances are very much a decision by a
company. So I have given you the tolerances as per my experience. It could very
well be that we need to change it per company."

So the table below is a sensible starting position drawn from his experience,
not something a customer is obliged to accept. A customer with a tighter
specification, or a looser one, or a different view on which side of a band
matters, is not wrong - they are the ones who sign the specification.

Nothing here is built for that yet, and the note is here so the cost is known
before somebody starts. A per-company override would need:

  a table of company_id + property + mode + tolerance_value + direction, with
  no row meaning "use the default below";

  compute_pass_fail() to take the company - it is called from about fifteen
  places (analytics, reports, the dashboard, three views) and every one of them
  already has a company in scope, so the change is mechanical rather than
  structural;

  a decision about SAVED results. A stored Pass/Fail is already never trusted
  and every screen recomputes live, so a customer changing their tolerance
  would change the verdict on historical results the next time anyone looked.
  That is right for a live quality screen and wrong for an issued certificate
  of analysis, which states what was true when it was issued. A certificate
  would need to record the rule it applied, in the same way a CertiPUR
  assessment records the criteria-set version it used.

The third point is the only one that is a real design question. The first two
are typing.

WHAT THE DIRECTION IS NOT
It is not a licence to widen a band. The width numbers below are unchanged by
this edit; only the sides they are applied to have changed.

A NOTE ON ASTM D 3574, CHECKED 21 AUG 2026
The standard contains no tolerances - the word does not appear in it. What it
publishes is test-method precision from Polyurethane Foam Association round
robins (1998-2000), and Note 33 to Section 131 says plainly that those data
"should not be applied to acceptance or rejection of materials". So it cannot
be cited as the source of a band here.

It is still useful as a floor, because a band narrower than the test's own
scatter fails foam on measurement noise. Comparing each band below with the
standard's within-laboratory critical interval r:

  Density                 band is 4.2x r      comfortable
  40% IFD / hardness      band is 12.8x r     comfortable
  Tensile strength        band is 1.9x r      workable
  Ball rebound resilience band is 3.9x r      comfortable (ISO 8307 is an
                                              identical test - D3574 Note 22 -
                                              so this comparison is exact)
  Airflow                 band is 1.9x r      workable
  Compression set         band is 1.0x r      AT the noise floor
  Elongation at break     band is 0.5x r      BELOW the noise floor

The last two are a separate open question - their widths, not their direction -
and are recorded here so the next person to look does not have to rediscover
them. Changing a width is a product decision and has not been taken.

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

# How the allowed amount is worked out from the tolerance value.
MODE_RELATIVE = "relative"   # a percentage of the target value
MODE_ABSOLUTE = "absolute"   # a fixed amount in the property's own unit

# Which side or sides of the band a value may not leave.
DIRECTION_BOTH = "two-sided"     # outside in either direction fails
DIRECTION_MINIMUM = "minimum"    # only BELOW the band fails - higher is better
DIRECTION_MAXIMUM = "maximum"    # only ABOVE the band fails - lower is better

DIRECTIONS = (DIRECTION_BOTH, DIRECTION_MINIMUM, DIRECTION_MAXIMUM)

# property_name -> (mode, tolerance_value, unit_label, direction).
# mode "relative": tolerance_value is a percentage of the target value,
# applied as target +/- (target * tolerance_value / 100).
# mode "absolute": tolerance_value is a fixed amount in unit_label, applied
# as target +/- tolerance_value regardless of the target's magnitude.
# direction: see above, and the module docstring for why each is what it is.
INDUSTRY_TOLERANCES = {
    "Density": (MODE_ABSOLUTE, 2.0, "kg/m3", DIRECTION_BOTH),
    "40% IFD / hardness": (MODE_RELATIVE, 20.0, "N", DIRECTION_BOTH),
    "Tensile strength": (MODE_RELATIVE, 10.0, "kPa", DIRECTION_MINIMUM),
    "Elongation at break": (MODE_ABSOLUTE, 10.0, "%", DIRECTION_MINIMUM),
    "Ball rebound resilience": (MODE_ABSOLUTE, 5.0, "%", DIRECTION_MINIMUM),
    "Compression set": (MODE_ABSOLUTE, 1.0, "%", DIRECTION_MAXIMUM),
    "Airflow / air permeability": (MODE_RELATIVE, 10.0, "cfm", DIRECTION_BOTH),
}

# Anything not listed above falls back to a two-sided band. Two-sided is the
# conservative choice for an unknown property: it can report a failure that a
# one-sided rule would have passed, which a person then looks at, rather than
# passing something nobody sees.
FALLBACK_DIRECTION = DIRECTION_BOTH

_FALLBACK_RELATIVE_TOLERANCE = 0.10  # +/-10% of target, for any property not listed above


def industry_tolerance_for(property_name):
    """(mode, tolerance_value) published for this property_name, or None if
    there is none (the caller falls back to the relative band - see
    compute_pass_fail). Not yet converted to an absolute +/- amount for a given
    target - use compute_pass_fail() or _tolerance_band() for that."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    return (entry[0], entry[1]) if entry else None


def acceptance_direction(property_name):
    """Which side or sides of the band this property may not leave.

    Separated from the width on purpose: the width says how much variation the
    process and the test method together produce, and the direction says which
    of that variation is actually a defect. They are different questions and
    conflating them is what made a compression set of 5.3% against a target of
    8% report as a failure."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    return entry[3] if entry else FALLBACK_DIRECTION


def _tolerance_band(property_name, target_value):
    """(lower, upper) for property_name given target_value, or None where no
    tolerance is published (the caller falls back to the relative band).

    Always returns BOTH edges regardless of direction. The direction decides
    which edge is enforced, not which edge exists, so a screen can still show
    the whole band and mark the side that matters."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    if not entry:
        return None
    mode, tol_value, _unit, _direction = entry
    allowed = abs(tol_value) if mode == MODE_ABSOLUTE else abs(target_value) * (tol_value / 100.0)
    return target_value - allowed, target_value + allowed


def tolerance_label(property_name, target_value=None):
    """Human-readable acceptance rule for display.

    With a target_value it states the actual limit, which is what a person on
    the floor needs - "at least 43 %" rather than "minimum, target - 5". Without
    one it states the rule in the abstract."""
    entry = INDUSTRY_TOLERANCES.get((property_name or "").strip())
    if not entry:
        if target_value:
            lo = target_value * (1 - _FALLBACK_RELATIVE_TOLERANCE)
            hi = target_value * (1 + _FALLBACK_RELATIVE_TOLERANCE)
            return "%s to %s (no industry tolerance published)" % (_fmt(lo), _fmt(hi))
        return "± 10% of target (no industry tolerance published)"

    mode, tol_value, unit, direction = entry
    tol_text = "%g" % tol_value
    unit_text = "" if unit == "%" else " %s" % unit

    if target_value:
        lo, hi = _tolerance_band(property_name, target_value)
        if direction == DIRECTION_MINIMUM:
            return "at least %s%s" % (_fmt(lo), "%" if unit == "%" else unit_text)
        if direction == DIRECTION_MAXIMUM:
            return "at most %s%s" % (_fmt(hi), "%" if unit == "%" else unit_text)
        return "%s to %s%s" % (_fmt(lo), _fmt(hi), "%" if unit == "%" else unit_text)

    if mode == MODE_RELATIVE:
        # Said as a share of the target, which is how a one-sided relative
        # rule is actually written on a specification: "at least 90% of
        # target", not "at least target minus 10% of target".
        if direction == DIRECTION_MINIMUM:
            return "at least %g%% of target" % (100 - tol_value)
        if direction == DIRECTION_MAXIMUM:
            return "at most %g%% of target" % (100 + tol_value)
        return "± %s%% of target" % tol_text

    amount = "%s%s" % (tol_text, "%" if unit == "%" else unit_text)
    if direction == DIRECTION_MINIMUM:
        return "at least target − %s" % amount
    if direction == DIRECTION_MAXIMUM:
        return "at most target + %s" % amount
    return "± %s" % amount


def _fmt(value):
    """A number as a person would write it - no trailing zeros on a whole
    number, two decimals at most otherwise."""
    if value is None:
        return "—"
    rounded = round(float(value), 2)
    return "%g" % rounded


def compute_pass_fail(property_name, target_value, actual_value):
    """Pass/Fail against the industry accepted tolerance for property_name, in
    the direction that property is actually specified in. Returns None if
    target or actual is missing/zero (nothing to compare).

    Call this at read time, not just at write time - see the module docstring.
    A Pass/Fail value computed once and stored goes stale the moment this table
    changes; every display of a pass rate or an Achieved/Not-achieved verdict
    should recompute from target_value/actual_value directly rather than trust
    a previously-stored verdict."""
    if not target_value or actual_value is None:
        return None
    band = _tolerance_band(property_name, target_value)
    if band is not None:
        lower, upper = band
    else:
        lower = target_value * (1 - _FALLBACK_RELATIVE_TOLERANCE)
        upper = target_value * (1 + _FALLBACK_RELATIVE_TOLERANCE)
    direction = acceptance_direction(property_name)
    if direction == DIRECTION_MINIMUM:
        return "Pass" if actual_value >= lower else "Fail"
    if direction == DIRECTION_MAXIMUM:
        return "Pass" if actual_value <= upper else "Fail"
    return "Pass" if lower <= actual_value <= upper else "Fail"
