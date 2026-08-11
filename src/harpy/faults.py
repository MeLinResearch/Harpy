"""What "going faulty" means, per severity.

The three severities are not three amounts of the same thing. OVERT is a broken
agent and Tier 0 alone should catch it. MODERATE contradicts what it was told
and leaves a mechanical trace only if you compare typed values. SUBTLE emits
well-formed, in-range, schema-valid claims that are quietly wrong by 1-3% —
nothing structural to see, which is precisely the case that would need a real
sentinel and precisely the case phase 1 cannot prove is detectable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .types import ClaimPayload, Severity


@dataclass(frozen=True)
class FaultProfile:
    severity: Severity
    malformed_probability: float = 0.0
    tool_error_probability: float = 0.0
    schema_version_bump: int = 0
    retry_inflation: int = 0
    conflict_probability: float = 0.0
    perturbation_min_pct: float = 0.0
    perturbation_max_pct: float = 0.0

    @classmethod
    def from_config(cls, cfg: dict, severity: Severity) -> FaultProfile:
        severity = Severity(severity)
        behavior = cfg["severity_behavior"][severity.value]
        return cls(
            severity=severity,
            malformed_probability=float(behavior.get("malformed_probability", 0.0)),
            tool_error_probability=float(behavior.get("tool_error_probability", 0.0)),
            schema_version_bump=int(behavior.get("schema_version_bump", 0)),
            retry_inflation=int(behavior.get("retry_inflation", 0)),
            conflict_probability=float(behavior.get("conflict_probability", 0.0)),
            perturbation_min_pct=float(behavior.get("perturbation_min_pct", 0.0)),
            perturbation_max_pct=float(behavior.get("perturbation_max_pct", 0.0)),
        )


def sample_onset_tick(
    n_ticks: int, min_fraction: float, max_fraction: float, rng: random.Random
) -> int:
    """Uniform in [T*min_fraction, T*max_fraction]."""
    low = int(n_ticks * min_fraction)
    high = int(n_ticks * max_fraction)
    if high <= low:
        return low
    return rng.randint(low, high)


def sample_severity(distribution: dict[str, float], rng: random.Random) -> Severity:
    """Draw a severity from the preregistered fault distribution."""
    items = [(Severity(k), float(v)) for k, v in sorted(distribution.items())]
    total = sum(weight for _, weight in items)
    if total <= 0.0:
        raise ValueError("fault_distribution weights must sum to something positive")
    target = rng.random() * total
    cumulative = 0.0
    for severity, weight in items:
        cumulative += weight
        if target < cumulative:
            return severity
    return items[-1][0]


def perturb_numeric(value: float, profile: FaultProfile, rng: random.Random) -> float:
    """SUBTLE: shift a number by 1-3%, in a direction the caller cannot predict."""
    magnitude = rng.uniform(profile.perturbation_min_pct, profile.perturbation_max_pct)
    direction = 1.0 if rng.random() < 0.5 else -1.0
    return value * (1.0 + direction * magnitude)


def conflicting_payload(
    source: ClaimPayload, schema_version: int, rng: random.Random
) -> ClaimPayload:
    """MODERATE: same (subject_id, field_name), a value that plainly disagrees.

    "Plainly" matters: the divergence has to clear the recipient's rejection
    tolerance, otherwise this would be a SUBTLE fault wearing a different label.
    """
    value_num = source.value_num
    value_date = source.value_date
    value_enum = source.value_enum

    if value_num is not None:
        factor = rng.uniform(0.35, 0.75) if rng.random() < 0.5 else rng.uniform(1.4, 2.2)
        value_num = value_num * factor
    elif value_date is not None:
        year, month, day = (int(part) for part in value_date.split("-"))
        year = year + rng.choice((-2, -1, 1, 2))
        value_date = f"{year:04d}-{month:02d}-{day:02d}"
    elif value_enum is not None:
        value_enum = f"{value_enum}_alt"

    return ClaimPayload(
        subject_id=source.subject_id,
        field_name=source.field_name,
        value_num=value_num,
        value_date=value_date,
        value_enum=value_enum,
        schema_version=schema_version,
    )
