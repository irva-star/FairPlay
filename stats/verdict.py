"""The verdict rule (SPEC section 8). Pure logic, no dependencies.

Four outcomes from two tests, so that "we found nothing" is never silently
reported as "there is nothing". The distinction between *equivalent* and
*inconclusive* is the whole reason TOST is here:

    Holm p < .05   TOST p < .05   verdict
    yes            no             Difference detected
    yes            yes            Detected, below the margin of concern
    no             yes            Equivalent: no meaningful difference
    no             no             Inconclusive
"""

from __future__ import annotations

import math

DIFFERENCE_DETECTED = "Difference detected"
BELOW_MARGIN = "Detected, below the margin of concern"
EQUIVALENT = "Equivalent: no meaningful difference"
INCONCLUSIVE = "Inconclusive"

ALPHA = 0.05

# Never display "Inconclusive" as "no difference". The wording is the finding.
DISPLAY_NOTE = {
    DIFFERENCE_DETECTED: "A difference larger than the margin of concern.",
    BELOW_MARGIN: "A real difference, but smaller than the margin of concern.",
    EQUIVALENT: "Any difference is smaller than the margin of concern.",
    INCONCLUSIVE: "Not enough evidence either way. This is not a finding of no difference.",
}


def _is_missing(p: float | None) -> bool:
    return p is None or (isinstance(p, float) and math.isnan(p))


def decide(p_holm: float | None, p_tost: float | None, *, alpha: float = ALPHA) -> str:
    """Map the two p-values onto a verdict.

    A missing p-value means the test could not run (for example both groups had
    zero variance, which happens when every session scored identically). That is
    reported as inconclusive rather than passed through as NaN, because NaN
    rendered on a dashboard reads as a bug, not as a result.
    """
    if _is_missing(p_holm) and _is_missing(p_tost):
        return INCONCLUSIVE

    significant = (not _is_missing(p_holm)) and p_holm < alpha
    equivalent = (not _is_missing(p_tost)) and p_tost < alpha

    if significant and not equivalent:
        return DIFFERENCE_DETECTED
    if significant and equivalent:
        return BELOW_MARGIN
    if not significant and equivalent:
        return EQUIVALENT
    return INCONCLUSIVE


def detected(verdict: str) -> bool:
    return verdict in (DIFFERENCE_DETECTED, BELOW_MARGIN)


def holm(p_values: list[float | None], *, alpha: float = ALPHA) -> list[float | None]:
    """Holm step-down adjusted p-values, order preserved.

    Implemented here rather than called from statsmodels so a missing p-value
    (a metric that could not be computed) drops out of the family instead of
    poisoning it; tests cross-check the all-present case against
    statsmodels.stats.multitest.multipletests(method="holm").
    """
    indexed = [(i, p) for i, p in enumerate(p_values) if not _is_missing(p)]
    if not indexed:
        return [None] * len(p_values)

    indexed.sort(key=lambda t: t[1])
    m = len(indexed)
    out: list[float | None] = [None] * len(p_values)

    running = 0.0
    for rank, (idx, p) in enumerate(indexed):
        adjusted = (m - rank) * p
        running = max(running, adjusted)       # enforce monotonicity
        out[idx] = min(1.0, running)
    return out
