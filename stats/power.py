"""Power check and the V4 calibration (SPEC section 8).

The sample-size formula is for TOST at 80% power with a true difference of zero,
which is the case we actually need to be powered for: showing equivalence on the
negative control and in the A/A comparison is harder than detecting the planted
effect.

    n_per_group ~= 2 (z_{1-alpha} + z_{1-beta/2})^2 sigma^2 / Delta^2
                ~= 17.1 sigma^2 / Delta^2    at alpha=.05, power=.80

V4 asks a different question: left alone, how often does this pipeline cry wolf?
Splitting sessions that were never treated differently and re-running the whole
analysis 1,000 times should raise an alarm about 5% of the time. More than 7%
means the method is not calibrated and the headline findings cannot be trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist

ALPHA = 0.05
POWER = 0.80

CALIBRATION_SPLITS = 1_000
CALIBRATION_SEED = 4242
CALIBRATION_MAX_ALARM_RATE = 0.07


def tost_n_per_group(sigma: float, delta: float, *, alpha: float = ALPHA,
                     power: float = POWER) -> float:
    """Sessions needed per group to show equivalence within +/-delta."""
    if delta <= 0:
        raise ValueError("delta must be positive")
    if sigma <= 0:
        return 2.0  # no variance: any two sessions settle it
    nd = NormalDist()
    z_alpha = nd.inv_cdf(1.0 - alpha)
    z_beta = nd.inv_cdf(1.0 - (1.0 - power) / 2.0)
    return 2.0 * (z_alpha + z_beta) ** 2 * sigma ** 2 / delta ** 2


@dataclass
class PowerRow:
    metric: str
    sigma: float
    margin: float
    n_required: float
    n_planned: int

    @property
    def covered(self) -> bool:
        return self.n_planned >= self.n_required


def power_table(sigmas: dict[str, float], margins: dict[str, float],
                *, n_planned: int = 60) -> list[PowerRow]:
    rows = []
    for metric, sigma in sigmas.items():
        if metric not in margins:
            continue
        rows.append(PowerRow(
            metric=metric, sigma=float(sigma), margin=float(margins[metric]),
            n_required=tost_n_per_group(float(sigma), float(margins[metric])),
            n_planned=n_planned,
        ))
    return rows


def format_power_table(rows: list[PowerRow]) -> str:
    lines = [f"{'metric':<8}{'sd':>10}{'margin':>10}{'n needed':>10}{'n planned':>11}  status"]
    for r in rows:
        status = "ok" if r.covered else "RAISE n OR WIDEN MARGIN"
        lines.append(f"{r.metric:<8}{r.sigma:>10.4f}{r.margin:>10.3f}"
                     f"{r.n_required:>10.1f}{r.n_planned:>11d}  {status}")
    return "\n".join(lines)


def calibration_v4(values_by_metric: dict[str, list[float | None]], margins: dict[str, float],
                   *, splits: int = CALIBRATION_SPLITS, seed: int = CALIBRATION_SEED,
                   alpha: float = ALPHA) -> dict:
    """Randomly split untreated sessions and count how often anything alarms.

    `values_by_metric` holds one list per metric, aligned by session index, so a
    split keeps each session's metrics together (shuffling metrics
    independently would destroy the correlations and flatter the result).
    """
    import numpy as np

    from stats.analysis import HOLM_FAMILY, estimate
    from stats.verdict import holm

    metrics = [m for m in HOLM_FAMILY if m in values_by_metric]
    if not metrics:
        raise ValueError("no Holm-family metrics supplied")

    n_sessions = len(next(iter(values_by_metric.values())))
    for metric, values in values_by_metric.items():
        if len(values) != n_sessions:
            raise ValueError(f"{metric} has {len(values)} values, expected {n_sessions}")

    half = n_sessions // 2
    rng = np.random.default_rng(seed)
    alarms = 0
    per_metric = {m: 0 for m in metrics}
    min_p_per_split: list[float] = []

    for _ in range(splits):
        order = rng.permutation(n_sessions)
        group_a, group_b = order[:half], order[half:half * 2]

        p_values: list[float | None] = []
        for metric in metrics:
            values = values_by_metric[metric]
            a = [values[i] for i in group_a if values[i] is not None]
            b = [values[i] for i in group_b if values[i] is not None]
            est = estimate(metric, "V4", a, b, margins[metric], alpha=alpha, bootstrap=False)
            p_values.append(est.p_raw)

        adjusted = holm(p_values, alpha=alpha)
        any_alarm = False
        present = [p for p in adjusted if p is not None]
        min_p_per_split.append(min(present) if present else 1.0)
        for metric, p in zip(metrics, adjusted):
            if p is not None and p < alpha:
                per_metric[metric] += 1
                any_alarm = True
        if any_alarm:
            alarms += 1

    rate = alarms / splits
    return {
        "splits": splits,
        "n_sessions": n_sessions,
        "group_size": half,
        "alarm_rate": rate,
        "alarm_rate_by_metric": {m: c / splits for m, c in per_metric.items()},
        "min_p_per_split": min_p_per_split,
        "passes": rate <= CALIBRATION_MAX_ALARM_RATE,
        "max_allowed": CALIBRATION_MAX_ALARM_RATE,
    }
