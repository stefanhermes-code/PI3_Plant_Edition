"""The quality calculations, where a wrong answer is silent.

Work package 5 of the Permanent Automated Regression Test Suite CR, section 6.

This was the only area of the CR with no coverage at all, and it is the one
where an error does not announce itself. A page that crashes gets reported. A
failure rate computed against the wrong denominator produces a plausible
number, on a chart, that nobody questions - and the smaller it is, the more
comfortable it looks.

So the expected values here are worked out by hand in the test, from the
arithmetic, rather than by calling the function and writing down what came
back. A test that records the current output only tells you the output has not
changed; it does not tell you it was ever right.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

import analytics as an
import quality_standards as qs


# --- pass/fail against the published tolerance ------------------------------
#
# The widths and directions were ruled by Stefan on 21 August 2026 and are not
# reopened here. What is tested is that the rule is applied as stated: the
# width says how much variation the process and the test method produce, the
# direction says which side of it is actually a defect.

def test_density_is_two_sided_at_two_absolute_units():
    """Density: absolute +/- 2.0 kg/m3, two-sided."""
    assert qs.compute_pass_fail("Density", 30.0, 30.0) == "Pass"
    assert qs.compute_pass_fail("Density", 30.0, 32.0) == "Pass"   # on the edge
    assert qs.compute_pass_fail("Density", 30.0, 28.0) == "Pass"   # on the edge
    assert qs.compute_pass_fail("Density", 30.0, 32.01) == "Fail"
    assert qs.compute_pass_fail("Density", 30.0, 27.99) == "Fail"


def test_the_band_edge_itself_passes():
    """A value exactly on the limit is inside it. Stated once, deliberately."""
    lower, upper = qs._tolerance_band("Density", 30.0)
    assert (lower, upper) == (28.0, 32.0)
    assert qs.compute_pass_fail("Density", 30.0, lower) == "Pass"
    assert qs.compute_pass_fail("Density", 30.0, upper) == "Pass"


def test_compression_set_only_fails_above():
    """Maximum: lower is better. This is the case that prompted the ruling -
    5.3% against a target of 8% is a good result, not a failure."""
    assert qs.compute_pass_fail("Compression set", 8.0, 5.3) == "Pass"
    assert qs.compute_pass_fail("Compression set", 8.0, 9.0) == "Pass"   # on the edge
    assert qs.compute_pass_fail("Compression set", 8.0, 9.01) == "Fail"


def test_ball_rebound_only_fails_below():
    """Minimum: higher is better."""
    assert qs.compute_pass_fail("Ball rebound resilience", 45.0, 60.0) == "Pass"
    assert qs.compute_pass_fail("Ball rebound resilience", 45.0, 40.0) == "Pass"  # edge
    assert qs.compute_pass_fail("Ball rebound resilience", 45.0, 39.99) == "Fail"


def test_a_relative_tolerance_scales_with_the_target():
    """40% IFD: relative +/- 20%, two-sided. 20% of 100 is not 20% of 200."""
    assert qs._tolerance_band("40% IFD / hardness", 100.0) == (80.0, 120.0)
    assert qs._tolerance_band("40% IFD / hardness", 200.0) == (160.0, 240.0)
    assert qs.compute_pass_fail("40% IFD / hardness", 200.0, 170.0) == "Pass"
    assert qs.compute_pass_fail("40% IFD / hardness", 100.0, 170.0) == "Fail"


def test_a_one_sided_relative_property_is_not_silently_two_sided():
    """Tensile strength: relative 10%, minimum. Well above target must pass."""
    assert qs.acceptance_direction("Tensile strength") == qs.DIRECTION_MINIMUM
    assert qs.compute_pass_fail("Tensile strength", 100.0, 500.0) == "Pass"
    assert qs.compute_pass_fail("Tensile strength", 100.0, 90.0) == "Pass"   # edge
    assert qs.compute_pass_fail("Tensile strength", 100.0, 89.9) == "Fail"


def test_an_unlisted_property_falls_back_to_two_sided_ten_percent():
    """The conservative choice: it can report a failure a person then looks at."""
    assert qs.acceptance_direction("Something nobody has published") == qs.DIRECTION_BOTH
    assert qs.compute_pass_fail("Something nobody has published", 100.0, 110.0) == "Pass"
    assert qs.compute_pass_fail("Something nobody has published", 100.0, 110.1) == "Fail"
    assert qs.compute_pass_fail("Something nobody has published", 100.0, 89.9) == "Fail"


def test_the_property_name_is_matched_after_trimming():
    assert qs.compute_pass_fail("  Density  ", 30.0, 32.0) == "Pass"
    assert qs.compute_pass_fail("  Density  ", 30.0, 33.0) == "Fail"


@pytest.mark.parametrize(
    "target,actual",
    [(None, 30.0), (0, 30.0), (30.0, None)],
    ids=["no target", "zero target", "no result"],
)
def test_nothing_to_compare_returns_no_verdict_rather_than_a_guess(target, actual):
    assert qs.compute_pass_fail("Density", target, actual) is None


def test_a_zero_result_is_a_result_and_gets_a_verdict():
    """0 is falsy in Python and is a real measurement. It must not be dropped."""
    assert qs.compute_pass_fail("Density", 30.0, 0.0) == "Fail"


# --- the denominator --------------------------------------------------------
#
# Charlie's rule of 19 August 2026: a property-specific failure rate must use
# that property's own result count, never the all-properties total.

def test_pass_rate_is_the_share_of_the_series_it_was_given():
    series = pd.Series(["Pass"] * 7 + ["Fail"] * 3)
    assert an.pass_rate(series) == 0.7


def test_pass_rate_ignores_rows_with_no_verdict_rather_than_counting_them_as_failures():
    """A result with no target has no verdict. Counting it as a failure would
    invent failures; counting it in the denominator would dilute the rate."""
    series = pd.Series(["Pass", "Pass", "Fail", None, np.nan])
    assert an.pass_rate(series) == round(2 / 3, 3)


def test_pass_rate_of_nothing_is_not_a_hundred_percent():
    """An empty series has no rate. Returning 1.0 would read as perfect."""
    assert an.pass_rate(pd.Series([], dtype=object)) is None
    assert an.pass_rate(pd.Series([None, np.nan])) is None


def test_the_denominator_is_the_filtered_series_and_the_difference_is_large():
    """The arithmetic behind the rule, so the rule is not just an assertion.

    Compression set alone: 7 failures in 242 results. Divided by the
    all-properties total of 1,455, the same 7 failures read as a sixth of the
    real rate. Both numbers are computed here from the same failure count.
    """
    compression_set = pd.Series(["Fail"] * 7 + ["Pass"] * 235)
    all_properties = pd.Series(["Fail"] * 56 + ["Pass"] * (1455 - 56))

    property_failure_rate = 1 - an.pass_rate(compression_set)
    overall_failure_rate = 1 - an.pass_rate(all_properties)
    wrong_denominator = 7 / len(all_properties)

    assert round(property_failure_rate, 3) == round(7 / 242, 3)
    assert round(overall_failure_rate, 3) == round(56 / 1455, 3)
    assert wrong_denominator < property_failure_rate / 5
    assert len(compression_set) != len(all_properties)


# --- normalising to percent of target ---------------------------------------

def _results(actuals, targets):
    return pd.DataFrame({"actual_value": actuals, "target_value": targets})


def test_normalising_puts_every_grade_on_the_same_scale():
    """Two grades of the same property with different targets, pooled.

    Raw, grade B's values sit far above grade A's for no reason other than its
    target. Normalised, both read as a percentage of their own target, so a
    pooled analysis is measuring the process rather than which grade it was.
    """
    df = _results([30.0, 33.0, 200.0, 220.0], [30.0, 30.0, 200.0, 200.0])
    out = an.normalize_to_pct_of_target(df)

    assert list(out["target_value"]) == [100.0, 100.0, 100.0, 100.0]
    assert list(out["actual_value"]) == pytest.approx([100.0, 110.0, 100.0, 110.0])
    assert list(out["_raw_actual_value"]) == [30.0, 33.0, 200.0, 220.0]


def test_a_row_with_no_target_is_dropped_rather_than_divided_by_zero():
    df = _results([30.0, 31.0, 32.0], [30.0, None, 0.0])
    out = an.normalize_to_pct_of_target(df)
    assert len(out) == 1
    assert out.iloc[0]["_raw_actual_value"] == 30.0


def test_normalising_nothing_returns_nothing():
    empty = _results([], [])
    assert an.normalize_to_pct_of_target(empty).empty
    assert an.normalize_to_pct_of_target(_results([30.0], [0.0])).empty


# --- short-term sigma -------------------------------------------------------

def test_moving_range_sigma_is_the_mean_moving_range_over_d2():
    """Worked by hand: |diffs| = 2,1,2,1 -> mean 1.5 -> 1.5 / 1.128."""
    values = np.array([10.0, 12.0, 11.0, 13.0, 12.0])
    assert an._moving_range_sigma(values) == pytest.approx(1.5 / 1.128)


def test_moving_range_sigma_is_not_inflated_by_a_shift_the_way_stdev_is():
    """The whole reason the estimator exists.

    Two stable halves with a step between them. The plain sample stdev reads
    the step as spread; the moving range only sees it once, in one of the
    gaps.
    """
    values = np.array([10.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0, 20.0])
    short_term = an._moving_range_sigma(values)
    naive = float(np.std(values, ddof=1))
    assert short_term < naive / 2


def test_a_flat_series_has_no_spread():
    assert an._moving_range_sigma(np.array([5.0, 5.0, 5.0])) == 0.0


def test_a_single_point_has_no_moving_range():
    assert an._moving_range_sigma(np.array([5.0])) == 0.0


# --- control chart ----------------------------------------------------------

def series_df(values, target=None, start=None):
    """A results series shaped the way property_run_series returns one."""
    start = start or dt.date(2026, 1, 1)
    return pd.DataFrame(
        {
            "run_id": list(range(1, len(values) + 1)),
            "tested_at": [start + dt.timedelta(days=i) for i in range(len(values))],
            "actual_value": [float(v) for v in values],
            "target_value": [target] * len(values) if target is not None else [np.nan] * len(values),
            "source": ["Production Run"] * len(values),
        }
    )


def test_a_control_chart_from_a_handful_of_points_is_refused():
    """Noise dressed up as insight. It says so rather than drawing it."""
    result = an.control_chart_analysis(series_df([10, 11, 12]), min_points=5)
    assert result == {"ready": False, "n": 3}


def test_the_control_limits_are_three_short_term_sigma_either_side_of_the_mean():
    values = [10.0, 12.0, 11.0, 13.0, 12.0]
    result = an.control_chart_analysis(series_df(values))

    mean = sum(values) / len(values)
    sigma = 1.5 / 1.128
    assert result["mean"] == pytest.approx(mean)
    assert result["sigma"] == pytest.approx(sigma)
    assert result["ucl"] == pytest.approx(mean + 3 * sigma)
    assert result["lcl"] == pytest.approx(mean - 3 * sigma)


def test_a_stable_process_raises_no_flags():
    result = an.control_chart_analysis(series_df([10, 11, 10, 11, 10, 11, 10]))
    assert result["ready"]
    assert result["in_control"]
    assert result["flags"] == []


def test_a_point_outside_the_control_limits_is_flagged_with_where_it_was():
    result = an.control_chart_analysis(series_df([10, 10, 10, 10, 10, 10, 10, 40]))
    rules = [flag["rule"] for flag in result["flags"]]
    assert "Beyond 3-sigma control limit" in rules
    assert not result["in_control"]

    breach = next(f for f in result["flags"] if f["rule"].startswith("Beyond"))
    assert breach["first_index"] == 7
    assert breach["first_run_id"] == 8


def test_a_sustained_shift_to_one_side_of_the_centre_is_flagged():
    """Eight consecutive points on one side of the centre line."""
    values = [10, 10, 10, 10, 10, 10, 10, 10, 50]
    result = an.control_chart_analysis(series_df(values))
    rules = [flag["rule"] for flag in result["flags"]]
    assert any("Sustained shift" in rule for rule in rules)

    shift = next(f for f in result["flags"] if "Sustained shift" in f["rule"])
    assert shift["first_index"] == 7, "the eighth point is where the rule is met"


def test_two_of_three_points_beyond_the_two_sigma_warning_line_is_flagged():
    values = [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 13, 10, 13]
    result = an.control_chart_analysis(series_df(values))
    rules = [flag["rule"] for flag in result["flags"]]
    assert any("2-of-3" in rule for rule in rules)


def test_a_sustained_drift_is_flagged_even_though_no_point_leaves_the_limits():
    """The reason the run rules exist at all.

    Six consecutive rising points, every one of them inside the control
    limits. A chart that only watched the 3-sigma lines would show a process
    walking steadily off its centre and say nothing.
    """
    values = [10, 11, 12, 13, 14, 15]
    result = an.control_chart_analysis(series_df(values))

    inside = [v for v in values if result["lcl"] <= v <= result["ucl"]]
    assert len(inside) == len(values), "the point of this case is that none escape"

    rules = [flag["rule"] for flag in result["flags"]]
    assert any("drift" in rule for rule in rules)
    assert not any(rule.startswith("Beyond") for rule in rules)


def test_the_drift_rule_fires_on_the_sixth_point_not_the_seventh():
    """Defect found 22 August 2026, fixed at v2.36.0. Test written first.

    The rule is stated in the function's own docstring as "6+ consecutive
    points steadily rising or falling", which is Nelson rule 3 - six points,
    five intervals between them. The counter counted intervals rather than
    points, because it reset to 1 on the very first interval, when no
    direction had been established yet. So it needed seven points, and a
    six-point drift went unreported.

    Nothing announces this. The chart simply does not flag, and a process
    walking off its centre for six runs reads as a process nobody flagged.
    """
    six_points_rising = [10, 11, 12, 13, 14, 15]
    result = an.control_chart_analysis(series_df(six_points_rising))
    drift = [f for f in result["flags"] if "drift" in f["rule"]]
    assert drift, "six consecutive rising points must raise the drift rule"
    assert drift[0]["first_index"] == 5, "flagged at the sixth point"

    five_points_rising = [10, 11, 12, 13, 14]
    assert not [
        f for f in an.control_chart_analysis(series_df(five_points_rising))["flags"]
        if "drift" in f["rule"]
    ], "five is not six - the fix must not overshoot the other way"


def test_a_flat_series_raises_no_flags_rather_than_flagging_every_point():
    """With sigma of zero, every point equals every limit. Nothing is a signal."""
    result = an.control_chart_analysis(series_df([10] * 10))
    assert result["ready"]
    assert result["sigma"] == 0.0
    assert result["flags"] == []


# --- capability -------------------------------------------------------------

def test_cpk_is_computed_from_the_spec_limits_and_the_short_term_sigma():
    """Worked by hand rather than recorded from the function."""
    values = [100.0, 102.0, 101.0, 103.0, 102.0]
    result = an.capability_analysis(series_df(values, target=100.0), tolerance_pct=0.10)

    mean = sum(values) / len(values)
    sigma = 1.5 / 1.128
    assert result["usl"] == pytest.approx(110.0)
    assert result["lsl"] == pytest.approx(90.0)
    assert result["cpu"] == pytest.approx(round((110.0 - mean) / (3 * sigma), 3))
    assert result["cpl"] == pytest.approx(round((mean - 90.0) / (3 * sigma), 3))
    assert result["cpk"] == min(result["cpu"], result["cpl"])


def test_cpk_takes_the_worse_of_the_two_sides_not_the_average():
    """A process hard against one limit is not rescued by room at the other."""
    values = [109.0, 109.5, 109.0, 109.5, 109.0]
    result = an.capability_analysis(series_df(values, target=100.0))
    assert result["cpk"] == result["cpu"]
    assert result["cpu"] < result["cpl"]


def test_a_process_in_control_can_still_be_incapable():
    """The distinction the function exists for."""
    values = [100.0, 106.0, 100.0, 106.0, 100.0, 106.0, 100.0]
    frame = series_df(values, target=100.0)
    chart = an.control_chart_analysis(frame)
    capability = an.capability_analysis(frame)

    assert chart["in_control"]
    assert capability["cpk"] < 1.0


def test_capability_without_a_target_is_not_guessed():
    assert an.capability_analysis(series_df([10, 11, 10, 11, 10])) is None
    assert an.capability_analysis(series_df([10, 11, 10, 11, 10], target=0)) is None


def test_capability_with_no_spread_is_not_reported_as_infinite():
    assert an.capability_analysis(series_df([100] * 6, target=100.0)) is None


def test_capability_from_a_handful_of_points_is_refused():
    assert an.capability_analysis(series_df([100, 101, 102], target=100.0)) is None


# --- CUSUM ------------------------------------------------------------------

def test_cusum_measures_drift_from_the_target_not_from_the_series_mean():
    """Deliberate, and the docstring says why: a series that already contains
    an unaddressed shift has a mean contaminated by that shift."""
    values = [110.0, 110.5, 110.0, 110.5, 110.0, 110.5, 110.0, 110.5]
    result = an.cusum_analysis(series_df(values, target=100.0))
    assert result["reference"] == 100.0


def test_cusum_falls_back_to_the_series_mean_when_no_target_is_recorded():
    values = [10.0, 11.0, 10.0, 11.0, 10.0, 11.0, 10.0, 11.0]
    result = an.cusum_analysis(series_df(values))
    assert result["reference"] == pytest.approx(sum(values) / len(values))


def test_a_sustained_shift_off_target_is_caught_and_dated():
    values = [100.0, 100.5, 100.0, 100.5] + [104.0, 104.5, 104.0, 104.5, 104.0, 104.5]
    result = an.cusum_analysis(series_df(values, target=100.0))
    assert result["breach_index"] is not None
    assert result["breach_direction"] == "upward"
    assert result["breach_run_id"] == result["breach_index"] + 1


def test_a_process_sitting_on_target_does_not_breach():
    values = [100.0, 100.5, 99.5, 100.0, 100.5, 99.5, 100.0, 100.5, 99.5, 100.0]
    result = an.cusum_analysis(series_df(values, target=100.0))
    assert result["breach_index"] is None
    assert result["breach_direction"] is None


def test_cusum_from_too_few_points_is_refused():
    assert an.cusum_analysis(series_df([100] * 7, target=100.0)) is None


# --- trend test -------------------------------------------------------------

def test_a_straight_line_is_reported_as_a_significant_trend():
    result = an.trend_test(series_df([10, 11, 12, 13, 14, 15]))
    assert result["direction"] == "increasing"
    assert result["significant"]
    assert result["slope_per_run"] == pytest.approx(1.0)
    assert result["r_squared"] == 1.0


def test_a_falling_line_is_reported_as_decreasing():
    result = an.trend_test(series_df([15, 14, 13, 12, 11, 10]))
    assert result["direction"] == "decreasing"
    assert result["slope_per_run"] == pytest.approx(-1.0)


def test_noise_is_not_reported_as_a_trend():
    result = an.trend_test(series_df([10, 11, 10, 11, 10, 11, 10, 11]))
    assert not result["significant"]


def test_a_curved_trend_is_caught_by_the_non_parametric_check():
    """The reason there are two tests and not one. A drift that accelerates is
    still monotonic, and Mann-Kendall only assumes the direction is consistent.
    """
    values = [10.0, 10.2, 10.5, 11.0, 12.0, 14.0, 18.0, 26.0, 42.0]
    result = an.trend_test(series_df(values))
    assert result["mk_direction"] == "increasing"
    assert result["mk_significant"]


def test_a_flat_series_has_no_direction_and_no_significance():
    result = an.trend_test(series_df([10.0] * 8))
    assert result["direction"] == "flat"
    assert not result["significant"]
    assert result["mk_tau"] == 0.0


def test_a_trend_from_too_few_points_is_refused():
    assert an.trend_test(series_df([10, 11, 12])) is None
