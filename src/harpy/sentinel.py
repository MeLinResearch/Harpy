"""The expensive tier: a model-backed detector, and the phase-1 mock that stands in.

Phase 1 does not contain a detector. :class:`MockSentinel` *is* its configured
detection probability — it reads ground truth and returns a coin flip weighted
by ``detection_prob[severity]``. That is why it is the one component whitelisted
in the ground-truth guard, and why no phase-1 number is evidence that any model
detects anything. See the README, "What this CANNOT show".
"""

from __future__ import annotations

import random
from typing import Protocol, runtime_checkable

from .types import AuditResult, Message, Severity


@runtime_checkable
class Sentinel(Protocol):
    def audit(self, agent_id: str, window: tuple[Message, ...]) -> AuditResult: ...


class MockSentinel:
    """Simulates a detector with known characteristics. Detects nothing itself.

    ``severity`` is required because ``detection_prob`` is keyed by it and the
    ``audit`` signature is fixed — a claim does not carry the severity of the
    fault that produced it, so the run's severity is supplied at construction.
    """

    def __init__(
        self,
        detection_prob: dict[Severity, float],
        false_positive_rate: float,
        input_tokens: int,
        output_tokens: int,
        rng: random.Random,
        severity: Severity,
    ) -> None:
        self.detection_prob = {Severity(k): float(v) for k, v in detection_prob.items()}
        self.false_positive_rate = float(false_positive_rate)
        self.input_tokens = int(input_tokens)
        self.output_tokens = int(output_tokens)
        self.rng = rng
        self.severity = Severity(severity)
        if self.severity not in self.detection_prob:
            raise ValueError(f"no detection_prob configured for severity {self.severity}")

    def audit(self, agent_id: str, window: tuple[Message, ...]) -> AuditResult:
        contains_corrupt = any(claim.is_corrupt for message in window for claim in message.claims)
        probability = (
            self.detection_prob[self.severity] if contains_corrupt else self.false_positive_rate
        )
        flagged = self.rng.random() < probability
        tick = window[-1].tick if window else -1
        return AuditResult(
            agent_id=agent_id,
            tick=tick,
            flagged=flagged,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


class RealSentinel:
    """Phase 2 (HARPY-REPLAY): a real candidate detector over recorded traces.

    Phase 1 answers "what detector characteristics would make this architecture
    pay for itself". Phase 2 answers "does such a detector exist, at what price"
    by replaying recorded multi-agent traces through actual candidate models and
    measuring detection and cost instead of assuming them. Not implemented here
    on purpose: implementing it inside the simulator would let assumed numbers
    masquerade as measured ones.
    """

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "RealSentinel lands in phase 2 (HARPY-REPLAY): recorded traces, real "
            "candidate sentinels, measured detection and measured price."
        )

    def audit(self, agent_id: str, window: tuple[Message, ...]) -> AuditResult:
        raise NotImplementedError("RealSentinel lands in phase 2 (HARPY-REPLAY).")
