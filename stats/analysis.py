"""Estimation (SPEC section 8).

The Welch statistics are computed directly from both sample variances, and TOST
reuses exactly those statistics, so the significance test and the equivalence
test can never disagree about the data underneath them. The unit tests
cross-check against scipy.stats.ttest_ind(equal_var=False) and
statsmodels.stats.weightstats.ttost_ind(usevar="unequal").

Degenerate input is reported as "could not be computed" (None) rather than NaN:
a session set where every value is identical gives a zero standard error, and
a NaN leaking onto the dashboard reads as a bug rather than as a result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np
from scipy import stats as sps

from stats import verdict as V

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 2026


@dataclass
class Estimate:
    metric: str
    contrast: str
    n_a: int
    n_b: int
    mean_a: float | None = None
    mean_b: float | None = None
    diff: float | None = None
    se: float | None = None
    df: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    boot_ci_low: float | None = None
    boot_ci_high: float | None = None
    hedges_g: float | None = None
    g_ci_low: float | None = None
    g_ci_high: float | None = None
    p_raw: float | None = None
    p_holm: float | None = None
    tost_p: float | None = None
    margin: float | None = None
    verdict: str = V.INCONCLUSIVE
    note: str = ""

    def as_row(self) -> dict:
        return asdict(self)


def _clean(values) -> np.ndarray:
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    return arr[np.isfinite(arr)]


def welch(a, b) -> dict[str, float | None]:
    """Difference in means with Welch standard error and Satterthwaite df."""
    a, b = _clean(a), _clean(b)
    na, nb = a.size, b.size
    if na < 2 or nb < 2:
        return {"n_a": na, "n_b": nb, "diff": None, "se": None, "df": None,
                "mean_a": float(a.mean()) if na else None,
                "mean_b": float(b.mean()) if nb else None,
                "note": "fewer than 2 observations in a group"}

    va, vb = a.var(ddof=1), b.var(ddof=1)
    se2 = va / na + vb / nb
    diff = float(a.mean() - b.mean())

    if se2 <= 0:
        return {"n_a": na, "n_b": nb, "diff": diff, "se": 0.0, "df": None,
                "mean_a": float(a.mean()), "mean_b": float(b.mean()),
                "note": "zero variance in both groups"}

    se = math.sqrt(se2)
    df = se2 ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    return {"n_a": na, "n_b": nb, "diff": diff, "se": se, "df": float(df),
            "mean_a": float(a.mean()), "mean_b": float(b.mean()), "note": ""}


def welch_p(diff: float, se: float, df: float) -> float:
    t = diff / se
    return float(2.0 * sps.t.sf(abs(t), df))


def welch_ci(diff: float, se: float, df: float, *, alpha: float = V.ALPHA) -> tuple[float, float]:
    crit = float(sps.t.ppf(1.0 - alpha / 2.0, df))
    return diff - crit * se, diff + crit * se


def tost(diff: float, se: float, df: float, margin: float) -> float:
    """Two one-sided tests against +/-margin, from the same Welch statistics."""
    p_lower = float(sps.t.sf((diff + margin) / se, df))   # H0: diff <= -margin
    p_upper = float(sps.t.cdf((diff - margin) / se, df))  # H0: diff >= +margin
    return max(p_lower, p_upper)


def hedges_g(a, b) -> tuple[float | None, float | None, float | None]:
    a, b = _clean(a), _clean(b)
    na, nb = a.size, b.size
    if na < 2 or nb < 2:
        return None, None, None
    va, vb = a.var(ddof=1), b.var(ddof=1)
    pooled2 = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    if pooled2 <= 0:
        return None, None, None
    d = (a.mean() - b.mean()) / math.sqrt(pooled2)
    correction = 1.0 - 3.0 / (4.0 * (na + nb) - 9.0)
    g = float(d * correction)
    se_g = math.sqrt((na + nb) / (na * nb) + g ** 2 / (2.0 * (na + nb - 2)))
    return g, g - 1.96 * se_g, g + 1.96 * se_g


def bootstrap_ci(a, b, *, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
                 alpha: float = V.ALPHA) -> tuple[float | None, float | None]:
    """Percentile CI as a robustness check on the Welch interval."""
    a, b = _clean(a), _clean(b)
    if a.size < 2 or b.size < 2:
        return None, None
    rng = np.random.default_rng(seed)
    diffs = (
        rng.choice(a, size=(resamples, a.size), replace=True).mean(axis=1)
        - rng.choice(b, size=(resamples, b.size), replace=True).mean(axis=1)
    )
    lo, hi = np.quantile(diffs, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(lo), float(hi)


def estimate(metric: str, contrast: str, a, b, margin: float, *,
             alpha: float = V.ALPHA, bootstrap: bool = True) -> Estimate:
    """One metric, one contrast. Contrasts are always A minus B, with B = free."""
    w = welch(a, b)
    est = Estimate(
        metric=metric, contrast=contrast, margin=margin,
        n_a=int(w["n_a"]), n_b=int(w["n_b"]),
        mean_a=w["mean_a"], mean_b=w["mean_b"], diff=w["diff"],
        se=w["se"], df=w["df"], note=w["note"] or "",
    )
    if w["df"] is None or w["se"] in (None, 0.0):
        # Nothing testable. verdict.decide turns the missing p-values into
        # "Inconclusive" rather than letting NaN reach the dashboard.
        est.verdict = V.decide(None, None, alpha=alpha)
        return est

    diff, se, df = float(w["diff"]), float(w["se"]), float(w["df"])
    est.p_raw = welch_p(diff, se, df)
    est.ci_low, est.ci_high = welch_ci(diff, se, df, alpha=alpha)
    est.tost_p = tost(diff, se, df, margin)
    est.hedges_g, est.g_ci_low, est.g_ci_high = hedges_g(a, b)
    if bootstrap:
        est.boot_ci_low, est.boot_ci_high = bootstrap_ci(a, b, alpha=alpha)
    # p_holm and the final verdict are filled in by analyse_contrast, which sees
    # the whole M1-M4 family.
    est.verdict = V.decide(est.p_raw, est.tost_p, alpha=alpha)
    return est


HOLM_FAMILY = ("M1", "M2", "M3", "M4")


def analyse_contrast(contrast: str, by_metric: dict[str, tuple], margins: dict[str, float],
                     *, alpha: float = V.ALPHA, bootstrap: bool = True) -> list[Estimate]:
    """Estimate every metric for one contrast, then Holm-correct M1 to M4.

    B1 is a balance check and deliberately sits outside the Holm family: it asks
    whether the personas played alike, which is a different question from
    whether the game treated them differently.
    """
    estimates: dict[str, Estimate] = {}
    for metric, (a, b) in by_metric.items():
        estimates[metric] = estimate(metric, contrast, a, b, margins[metric],
                                     alpha=alpha, bootstrap=bootstrap)

    family = [m for m in HOLM_FAMILY if m in estimates]
    adjusted = V.holm([estimates[m].p_raw for m in family], alpha=alpha)
    for metric, p_adj in zip(family, adjusted):
        est = estimates[metric]
        est.p_holm = p_adj
        est.verdict = V.decide(p_adj, est.tost_p, alpha=alpha)

    for metric, est in estimates.items():
        if metric not in family:
            est.p_holm = est.p_raw  # outside the family: unadjusted, by design
            est.verdict = V.decide(est.p_raw, est.tost_p, alpha=alpha)

    order = list(HOLM_FAMILY) + [m for m in estimates if m not in HOLM_FAMILY]
    return [estimates[m] for m in order if m in estimates]
