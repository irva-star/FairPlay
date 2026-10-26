"""Statistics: cross-checks against scipy and statsmodels, plus synthetic truth.

The Welch and TOST statistics are computed directly in stats/analysis.py, so the
point of these tests is to prove the direct computation agrees with the
reference implementations -- and that the pipeline detects an effect we planted
and declares equivalence when there is none.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
sps = pytest.importorskip("scipy.stats")
smm = pytest.importorskip("statsmodels.stats.multitest")
smw = pytest.importorskip("statsmodels.stats.weightstats")

from stats import verdict as V  # noqa: E402
from stats.analysis import (  # noqa: E402
    analyse_contrast, bootstrap_ci, estimate, hedges_g, tost, welch, welch_ci, welch_p,
)


@pytest.fixture()
def samples():
    rng = np.random.default_rng(7)
    a = rng.normal(10.0, 2.0, 60)
    b = rng.normal(9.0, 2.5, 60)
    return a, b


# --- cross-checks -------------------------------------------------------------

def test_welch_p_matches_scipy(samples) -> None:
    a, b = samples
    w = welch(a, b)
    ours = welch_p(w["diff"], w["se"], w["df"])
    theirs = sps.ttest_ind(a, b, equal_var=False)
    assert ours == pytest.approx(float(theirs.pvalue), rel=1e-10)
    assert w["df"] == pytest.approx(float(theirs.df), rel=1e-10)


def test_welch_ci_matches_scipy(samples) -> None:
    a, b = samples
    w = welch(a, b)
    lo, hi = welch_ci(w["diff"], w["se"], w["df"])
    reference = sps.ttest_ind(a, b, equal_var=False).confidence_interval(0.95)
    assert lo == pytest.approx(float(reference.low), rel=1e-9)
    assert hi == pytest.approx(float(reference.high), rel=1e-9)


@pytest.mark.parametrize("margin", [0.5, 1.0, 2.0, 5.0])
def test_tost_matches_statsmodels(samples, margin: float) -> None:
    a, b = samples
    w = welch(a, b)
    ours = tost(w["diff"], w["se"], w["df"], margin)
    theirs, _, _ = smw.ttost_ind(a, b, low=-margin, upp=margin, usevar="unequal")
    assert ours == pytest.approx(float(theirs), rel=1e-9)


def test_holm_matches_statsmodels() -> None:
    for p_values in ([0.01, 0.04, 0.03, 0.005], [0.2, 0.3, 0.4, 0.5],
                     [0.001, 0.001, 0.049, 0.9]):
        ours = V.holm(list(p_values))
        _, theirs, _, _ = smm.multipletests(p_values, method="holm")
        assert ours == pytest.approx(list(theirs), rel=1e-12)


def test_holm_drops_missing_p_values_from_the_family() -> None:
    """A metric that could not be computed must not inflate the correction."""
    with_gap = V.holm([0.01, None, 0.03])
    without = V.holm([0.01, 0.03])
    assert with_gap[0] == pytest.approx(without[0])
    assert with_gap[1] is None
    assert with_gap[2] == pytest.approx(without[1])


def test_hedges_g_is_small_bias_corrected() -> None:
    a = np.array([1.0, 2, 3, 4, 5, 6, 7, 8])
    b = np.array([2.0, 3, 4, 5, 6, 7, 8, 9])
    g, lo, hi = hedges_g(a, b)
    pooled = np.sqrt(((a.size - 1) * a.var(ddof=1) + (b.size - 1) * b.var(ddof=1))
                     / (a.size + b.size - 2))
    d = (a.mean() - b.mean()) / pooled
    correction = 1 - 3 / (4 * (a.size + b.size) - 9)
    assert g == pytest.approx(d * correction)
    assert lo < g < hi


# --- synthetic truth ----------------------------------------------------------

def test_a_planted_effect_is_detected() -> None:
    rng = np.random.default_rng(1)
    a = rng.normal(0.80, 0.27, 60)   # offers per loss, rule on
    b = rng.normal(0.30, 0.27, 60)   # offers per loss, baseline
    est = estimate("M1", "V1_sensitivity", a, b, margin=0.15, bootstrap=False)
    assert est.verdict == V.DIFFERENCE_DETECTED
    assert est.p_raw < 0.001
    assert est.ci_low > 0.15, "the whole interval should clear the margin"
    assert est.diff == pytest.approx(0.50, abs=0.1)


def test_a_true_null_is_declared_equivalent() -> None:
    rng = np.random.default_rng(2)
    a = rng.normal(120.0, 20.0, 200)
    b = rng.normal(120.0, 20.0, 200)
    est = estimate("M4", "V2_specificity", a, b, margin=12.0, bootstrap=False)
    assert est.verdict == V.EQUIVALENT
    assert est.tost_p < 0.05


def test_a_small_sample_null_is_inconclusive_not_equivalent() -> None:
    """Not finding anything is not the same as showing there is nothing."""
    rng = np.random.default_rng(3)
    a = rng.normal(0.5, 0.3, 6)
    b = rng.normal(0.5, 0.3, 6)
    est = estimate("M1", "V3_aa", a, b, margin=0.05, bootstrap=False)
    assert est.verdict == V.INCONCLUSIVE


def test_a_real_but_tiny_effect_is_flagged_below_the_margin() -> None:
    rng = np.random.default_rng(4)
    a = rng.normal(0.52, 0.02, 400)
    b = rng.normal(0.50, 0.02, 400)
    est = estimate("M3", "V1_sensitivity", a, b, margin=0.10, bootstrap=False)
    assert est.verdict == V.BELOW_MARGIN


def test_the_bootstrap_ci_broadly_agrees_with_welch(samples) -> None:
    a, b = samples
    w = welch(a, b)
    welch_lo, welch_hi = welch_ci(w["diff"], w["se"], w["df"])
    boot_lo, boot_hi = bootstrap_ci(a, b, resamples=2000)
    assert boot_lo == pytest.approx(welch_lo, abs=0.3)
    assert boot_hi == pytest.approx(welch_hi, abs=0.3)


def test_the_bootstrap_is_reproducible(samples) -> None:
    a, b = samples
    assert bootstrap_ci(a, b, resamples=500) == bootstrap_ci(a, b, resamples=500)


# --- degenerate input ---------------------------------------------------------

def test_identical_constants_are_equivalent_not_nan() -> None:
    # Every session in both groups scored the same: the difference is exactly 0,
    # which is inside any margin. No p-value is invented and no NaN leaks out.
    a = [0.5] * 30
    b = [0.5] * 30
    est = estimate("M1", "V3_aa", a, b, margin=0.1, bootstrap=False)
    assert est.verdict == V.EQUIVALENT
    assert est.p_raw is None
    assert est.tost_p is None
    assert est.ci_low == est.ci_high == 0.0
    assert "identical" in est.note


def test_different_constants_are_judged_against_the_margin() -> None:
    est = estimate("S1", "SV_audit", [549.0] * 8, [249.0] * 8, margin=50, bootstrap=False)
    assert est.verdict == V.DIFFERENCE_DETECTED
    assert est.diff == 300.0
    assert "zero variance" in est.note
    small = estimate("S1", "SV_audit", [260.0] * 8, [249.0] * 8, margin=50, bootstrap=False)
    assert small.verdict == V.BELOW_MARGIN


def test_too_few_observations_is_inconclusive() -> None:
    est = estimate("M1", "V1_sensitivity", [0.5], [0.3, 0.4], margin=0.1, bootstrap=False)
    assert est.verdict == V.INCONCLUSIVE
    assert est.n_a == 1


def test_missing_values_are_dropped_not_counted() -> None:
    w = welch([1.0, 2.0, None, 3.0], [1.0, 2.0, 3.0])
    assert w["n_a"] == 3
    assert w["n_b"] == 3


# --- the family ---------------------------------------------------------------

def test_holm_is_applied_across_m1_to_m4_but_not_b1() -> None:
    rng = np.random.default_rng(5)
    by_metric = {
        "M1": (rng.normal(0.8, 0.27, 60), rng.normal(0.3, 0.27, 60)),
        "M2": (rng.normal(54.0, 3.9, 60), rng.normal(64.5, 3.9, 60)),
        "M3": (rng.normal(0.61, 0.15, 60), rng.normal(0.65, 0.15, 60)),
        "M4": (rng.normal(123.0, 20.0, 60), rng.normal(123.0, 20.0, 60)),
        "B1": (rng.normal(0.99, 0.01, 60), rng.normal(0.99, 0.01, 60)),
    }
    margins = {"M1": 0.15, "M2": 3.0, "M3": 0.10, "M4": 12.0, "B1": 0.10}
    estimates = analyse_contrast("V1_sensitivity", by_metric, margins, bootstrap=False)
    by_id = {e.metric: e for e in estimates}

    assert [e.metric for e in estimates] == ["M1", "M2", "M3", "M4", "B1"]
    for metric in ("M1", "M2", "M3", "M4"):
        assert by_id[metric].p_holm >= by_id[metric].p_raw, "Holm only ever raises a p-value"
    # B1 sits outside the family by design: it asks whether the personas played
    # alike, not whether the game treated them differently.
    assert by_id["B1"].p_holm == pytest.approx(by_id["B1"].p_raw)

    assert V.detected(by_id["M1"].verdict)
    assert V.detected(by_id["M2"].verdict)
    assert by_id["M4"].verdict == V.EQUIVALENT
    assert by_id["B1"].verdict == V.EQUIVALENT


def test_the_v1_pattern_the_demo_claims() -> None:
    """V1 passes when M1 and M2 are detected while M4 and B1 are equivalent."""
    rng = np.random.default_rng(6)
    by_metric = {
        "M1": (rng.normal(0.80, 0.27, 60), rng.normal(0.30, 0.27, 60)),
        "M2": (rng.normal(54.0, 3.85, 60), rng.normal(64.5, 3.85, 60)),
        "M4": (rng.normal(123.0, 20.0, 60), rng.normal(123.0, 20.0, 60)),
        "B1": (rng.normal(0.99, 0.01, 60), rng.normal(0.99, 0.01, 60)),
    }
    margins = {"M1": 0.15, "M2": 3.0, "M3": 0.10, "M4": 12.0, "B1": 0.10}
    by_id = {e.metric: e for e in
             analyse_contrast("V1_sensitivity", by_metric, margins, bootstrap=False)}
    assert V.detected(by_id["M1"].verdict)
    assert V.detected(by_id["M2"].verdict)
    assert not V.detected(by_id["M4"].verdict)
    assert not V.detected(by_id["B1"].verdict)


def test_the_v2_pattern_finds_nothing_when_the_rule_is_off() -> None:
    rng = np.random.default_rng(8)
    by_metric = {
        metric: (rng.normal(mean, sd, 60), rng.normal(mean, sd, 60))
        for metric, mean, sd in [("M1", 0.30, 0.27), ("M2", 64.5, 3.85),
                                 ("M3", 0.65, 0.15), ("M4", 123.0, 20.0)]
    }
    margins = {"M1": 0.15, "M2": 3.0, "M3": 0.10, "M4": 12.0}
    estimates = analyse_contrast("V2_specificity", by_metric, margins, bootstrap=False)
    assert not any(V.detected(e.verdict) for e in estimates)


# --- power --------------------------------------------------------------------

def test_the_power_formula_reproduces_the_specs_constant() -> None:
    from stats.power import tost_n_per_group

    assert tost_n_per_group(1.0, 1.0) == pytest.approx(17.1, abs=0.05)


def test_sixty_sessions_covers_the_primary_metrics() -> None:
    """Using the standard deviations the calibration simulation produced."""
    from stats.power import power_table

    sigmas = {"M1": 0.2717, "M2": 3.8514, "M3": 0.1494}
    margins = {"M1": 0.15, "M2": 3.0, "M3": 0.10}
    for row in power_table(sigmas, margins, n_planned=60):
        assert row.covered, f"{row.metric} needs {row.n_required:.0f} sessions, planned 60"


def test_calibration_does_not_cry_wolf_on_pure_noise() -> None:
    from stats.power import calibration_v4

    rng = np.random.default_rng(11)
    n = 120
    values = {
        "M1": list(rng.normal(0.30, 0.27, n)),
        "M2": list(rng.normal(64.5, 3.85, n)),
        "M3": list(rng.normal(0.65, 0.15, n)),
        "M4": list(rng.normal(123.0, 20.0, n)),
    }
    margins = {"M1": 0.15, "M2": 3.0, "M3": 0.10, "M4": 12.0}
    result = calibration_v4(values, margins, splits=200, seed=3)
    assert result["alarm_rate"] <= 0.07, result
    assert result["passes"]
    assert len(result["min_p_per_split"]) == 200
