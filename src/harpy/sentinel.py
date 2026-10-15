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
        evidence_accuracy: float | None = None,
    ) -> None:
        self.detection_prob = {Severity(k): float(v) for k, v in detection_prob.items()}
        self.false_positive_rate = float(false_positive_rate)
        self.input_tokens = int(input_tokens)
        self.output_tokens = int(output_tokens)
        self.rng = rng
        self.severity = Severity(severity)
        if self.severity not in self.detection_prob:
            raise ValueError(f"no detection_prob configured for severity {self.severity}")
        #: v3, and an assumption rather than a measurement: when set, a flag
        #: names one claim as its evidence. A true detection names a corrupt
        #: claim with this probability and otherwise a claim drawn uniformly
        #: from the window; a false positive always names a uniform draw.
        #: ``None`` names nothing and draws nothing, which is v1/v2.
        if evidence_accuracy is not None and not 0.0 <= float(evidence_accuracy) <= 1.0:
            raise ValueError(f"evidence_accuracy must be in [0, 1], got {evidence_accuracy}")
        self.evidence_accuracy = None if evidence_accuracy is None else float(evidence_accuracy)

    def audit(self, agent_id: str, window: tuple[Message, ...]) -> AuditResult:
        contains_corrupt = any(claim.is_corrupt for message in window for claim in message.claims)
        probability = (
            self.detection_prob[self.severity] if contains_corrupt else self.false_positive_rate
        )
        flagged = self.rng.random() < probability
        tick = window[-1].tick if window else -1
        evidence: tuple[str, ...] = ()
        if flagged and self.evidence_accuracy is not None:
            evidence = self._name_evidence(window, contains_corrupt)
        return AuditResult(
            agent_id=agent_id,
            tick=tick,
            flagged=flagged,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            evidence_claim_ids=evidence,
        )

    def _name_evidence(
        self, window: tuple[Message, ...], contains_corrupt: bool
    ) -> tuple[str, ...]:
        claims = [claim for message in window for claim in message.claims]
        if not claims:
            return ()
        if contains_corrupt and self.rng.random() < self.evidence_accuracy:
            corrupt = [claim for claim in claims if claim.is_corrupt]
            return (corrupt[self.rng.randrange(len(corrupt))].claim_id,)
        return (claims[self.rng.randrange(len(claims))].claim_id,)


class RealSentinel:
    """An observable-only detector boundary used by HARPY-REPLAY.

    The simulator continues to instantiate MockSentinel. A backend must be
    supplied explicitly so replay measurements cannot become simulation inputs
    without a separately versioned experiment.
    """

    def __init__(self, backend) -> None:
        self.backend = backend
        self.last_detection = None

    def audit(self, agent_id: str, window: tuple[Message, ...]) -> AuditResult:
        observation = {
            "agent_id": agent_id,
            "window": [{"message_id": m.message_id, "sender_id": m.sender_id,
                        "recipient_id": m.recipient_id, "tick": m.tick,
                        "malformed": m.malformed, "tool_error": m.tool_error,
                        "retry_count": m.retry_count, "token_len": m.token_len,
                        "claims": [{"claim_id": c.claim_id, "origin_agent_id": c.origin_agent_id,
                                    "tick": c.tick, "payload": c.payload.as_dict()}
                                   for c in m.claims]} for m in window],
        }
        detection = self.backend.detect(observation)
        self.last_detection = detection
        return AuditResult(agent_id=agent_id, tick=window[-1].tick if window else -1,
                           flagged=detection.flagged, input_tokens=detection.input_tokens,
                           output_tokens=detection.output_tokens,
                           evidence_claim_ids=tuple(detection.evidence_ids or ()))

