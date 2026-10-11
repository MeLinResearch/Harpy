"""Uncertainty for simulation and replay, including all-zero observations."""

from __future__ import annotations

import math
import random
from statistics import NormalDist, fmean


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> list[float] | None:
    if n == 0:
        return None
    if not 0 <= successes <= n or not 0 < confidence < 1:
        raise ValueError("invalid binomial interval inputs")
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0.0, centre - radius), min(1.0, centre + radius)]


def difference_interval(candidate: list[int], baseline: list[int]) -> list[float]:
    """Conservative 95% marginal Wilson difference; valid at zero successes.

    Both marginal intervals use 97.5% confidence (a Bonferroni construction).
    Pairing is retained for the separate bootstrap effect estimate. This interval
    does not assert simultaneous coverage over the study's exploratory cells.
    """
    if len(candidate) != len(baseline) or not candidate:
        raise ValueError("paired observations must be nonempty and equally sized")
    left = wilson_interval(sum(candidate), len(candidate), 0.975)
    right = wilson_interval(sum(baseline), len(baseline), 0.975)
    return [left[0] - right[1], left[1] - right[0]]


def bootstrap_mean_interval(values: list[float], seed: int = 0, samples: int = 2000) -> list[float]:
    if not values or samples < 100:
        raise ValueError("bootstrap needs observations and at least 100 resamples")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("bootstrap observations must be finite")
    rng = random.Random(seed)
    estimates = sorted(fmean(rng.choices(values, k=len(values))) for _ in range(samples))
    return [estimates[int(0.025 * samples)], estimates[min(samples - 1, int(0.975 * samples))]]
