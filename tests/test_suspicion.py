"""Suspicion dynamics, and the invariant that one audit is never enough."""

from __future__ import annotations

import pytest

from harpy.suspicion import SuspicionConfig, SuspicionTracker
from harpy.types import AuditResult


def config(**overrides) -> SuspicionConfig:
    base = {
        "decay": 0.98,
        "w_tier0": 0.15,
        "audit_weight": 1.0,
        "clear_credit": 0.25,
        "isolation_threshold": 2.6,
    }
    base.update(overrides)
    return SuspicionConfig(**base)


def flagged(agent_id: str = "agent-00", tick: int = 0) -> AuditResult:
    return AuditResult(agent_id, tick, True, 9000, 600)


def cleared(agent_id: str = "agent-00", tick: int = 0) -> AuditResult:
    return AuditResult(agent_id, tick, False, 9000, 600)


def test_isolation_never_fires_on_a_single_audit():
    tracker = SuspicionTracker(config())
    tracker.apply_audit(flagged())
    assert not tracker.should_isolate("agent-00")


def test_isolation_never_fires_on_a_single_audit_even_at_the_worst_tier0():
    """A saturated Tier 0 score plus one flag must still be short of the line."""
    tracker = SuspicionTracker(config())
    tracker.tick({"agent-00": 1.0})
    tracker.apply_audit(flagged())
    assert tracker.score("agent-00") < tracker.config.isolation_threshold
    assert not tracker.should_isolate("agent-00")


def test_config_rejects_a_threshold_a_single_audit_could_clear():
    with pytest.raises(ValueError, match="single audit"):
        config(audit_weight=3.0, isolation_threshold=2.6)
    with pytest.raises(ValueError, match="single audit"):
        config(audit_weight=2.6, isolation_threshold=2.6)


def test_repeated_flags_do_cross_the_threshold():
    tracker = SuspicionTracker(config())
    for _ in range(3):
        tracker.apply_audit(flagged())
    assert tracker.should_isolate("agent-00")


def test_decay_shrinks_an_unrefreshed_score_geometrically():
    tracker = SuspicionTracker(config(w_tier0=0.0))
    tracker.apply_audit(flagged())
    start = tracker.score("agent-00")
    tracker.tick({"agent-00": 0.0})
    assert tracker.score("agent-00") == pytest.approx(0.98 * start)
    for _ in range(9):
        tracker.tick({"agent-00": 0.0})
    assert tracker.score("agent-00") == pytest.approx(start * 0.98**10)


def test_tier0_alone_saturates_at_the_expected_fixed_point():
    """Sustained Tier 0 converges to w_tier0 * score / (1 - decay)."""
    tracker = SuspicionTracker(config())
    for _ in range(4000):
        tracker.tick({"agent-00": 0.5})
    assert tracker.score("agent-00") == pytest.approx(0.15 * 0.5 / 0.02, rel=1e-3)
    assert tracker.should_isolate("agent-00")


def test_a_healthy_tier0_level_never_reaches_the_threshold():
    tracker = SuspicionTracker(config())
    for _ in range(4000):
        tracker.tick({"agent-00": 0.06})
    assert not tracker.should_isolate("agent-00")


def test_clean_audits_buy_credit_back_and_the_score_floors_at_zero():
    tracker = SuspicionTracker(config())
    tracker.apply_audit(flagged())
    tracker.apply_audit(cleared())
    assert tracker.score("agent-00") == pytest.approx(0.75)
    for _ in range(10):
        tracker.apply_audit(cleared())
    assert tracker.score("agent-00") == 0.0


def test_threshold_is_inclusive():
    tracker = SuspicionTracker(config(isolation_threshold=2.0))
    tracker.apply_audit(flagged())
    tracker.apply_audit(flagged())
    assert tracker.score("agent-00") == pytest.approx(2.0)
    assert tracker.should_isolate("agent-00")


def test_reset_clears_one_agent_only():
    tracker = SuspicionTracker(config())
    tracker.apply_audit(flagged("agent-00"))
    tracker.apply_audit(flagged("agent-01"))
    tracker.reset("agent-00")
    assert tracker.score("agent-00") == 0.0
    assert tracker.score("agent-01") == pytest.approx(1.0)
