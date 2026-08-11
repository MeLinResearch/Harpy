"""Per-agent suspicion: a decaying accumulator that gates isolation.

One audit must never be enough to throw an agent away. A single flagged audit
from a detector with a false-positive rate is weak evidence, and isolation is
expensive and disruptive, so the threshold is set above ``audit_weight`` and the
invariant is enforced at construction rather than left to configuration care.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import AuditResult


@dataclass(frozen=True)
class SuspicionConfig:
    decay: float
    w_tier0: float
    audit_weight: float
    clear_credit: float
    isolation_threshold: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.decay <= 1.0:
            raise ValueError(f"decay must be in [0, 1], got {self.decay}")
        if self.audit_weight >= self.isolation_threshold:
            raise ValueError(
                "audit_weight must be strictly below isolation_threshold so isolation can "
                f"never fire off a single audit (got {self.audit_weight} >= "
                f"{self.isolation_threshold})"
            )
        for name in ("w_tier0", "audit_weight", "clear_credit", "isolation_threshold"):
            if getattr(self, name) < 0.0:
                raise ValueError(f"{name} must be >= 0")

    @classmethod
    def from_config(cls, cfg: dict) -> SuspicionConfig:
        sus = cfg["suspicion"]
        return cls(
            decay=float(sus["decay"]),
            w_tier0=float(sus["w_tier0"]),
            audit_weight=float(sus["audit_weight"]),
            clear_credit=float(sus["clear_credit"]),
            isolation_threshold=float(sus["isolation_threshold"]),
        )


class SuspicionTracker:
    def __init__(self, config: SuspicionConfig) -> None:
        self.config = config
        self._scores: dict[str, float] = {}
        self._isolation_threshold = config.isolation_threshold
        self.audits_applied = 0

    # -- the gate ----------------------------------------------------------

    @property
    def isolation_threshold(self) -> float:
        """The gate currently in force. Starts at the configured value.

        It is mutable because :mod:`harpy.alert_budget` raises it when the alert
        cap binds: spending less human attention has to mean being more
        conservative about isolating, not quietly dropping alerts about
        isolations that happened anyway.
        """
        return self._isolation_threshold

    def set_isolation_threshold(self, value: float) -> None:
        """Move the gate. The one-audit invariant is re-checked, not assumed."""
        value = float(value)
        if value <= self.config.audit_weight:
            raise ValueError(
                "isolation_threshold must stay strictly above audit_weight so isolation can "
                f"never fire off a single audit (got {value} <= {self.config.audit_weight})"
            )
        self._isolation_threshold = value

    def score(self, agent_id: str) -> float:
        return self._scores.get(agent_id, 0.0)

    def scores(self) -> dict[str, float]:
        return dict(self._scores)

    def tick(self, tier0_scores: dict[str, float]) -> None:
        """score <- decay * score + w_tier0 * Tier0Score, for every known agent."""
        for agent_id in sorted(set(self._scores) | set(tier0_scores)):
            previous = self._scores.get(agent_id, 0.0)
            updated = self.config.decay * previous + self.config.w_tier0 * tier0_scores.get(
                agent_id, 0.0
            )
            self._scores[agent_id] = max(updated, 0.0)

    def apply_audit(self, result: AuditResult) -> float:
        """A flagged audit adds weight; a clean one buys the agent credit back."""
        current = self._scores.get(result.agent_id, 0.0)
        if result.flagged:
            current += self.config.audit_weight
        else:
            current -= self.config.clear_credit
        self._scores[result.agent_id] = max(current, 0.0)
        self.audits_applied += 1
        return self._scores[result.agent_id]

    def should_isolate(self, agent_id: str) -> bool:
        return self.score(agent_id) >= self._isolation_threshold

    def isolation_candidates(self, agent_ids) -> tuple[str, ...]:
        return tuple(sorted(a for a in agent_ids if self.should_isolate(a)))

    def reset(self, agent_id: str) -> None:
        self._scores[agent_id] = 0.0
