"""v3 EXPOSURE arm: audit gap times observable fan-out, observables only."""

from __future__ import annotations

import random

from harpy.sampler import ExposureTracker, Sampler, SamplerConfig
from harpy.simulation import RunSpec, run_simulation
from harpy.types import V2_ARMS, Arm, LineageEdge, Severity

from conftest import make_claim, make_message

AGENTS = tuple(f"agent-{i:02d}" for i in range(6))


def test_exposure_is_a_v3_arm_outside_the_historical_set():
    assert Arm.EXPOSURE not in V2_ARMS
    assert {a.value for a in V2_ARMS} == {"NONE", "TIER0_ONLY", "RANDOM", "DIRECTED", "HYBRID"}


def test_score_is_gap_times_one_plus_fanout():
    tracker = ExposureTracker(refresh_ticks=1)
    tracker.observe(make_message("agent-00", claims=(make_claim("a1", "agent-00"),)))
    tracker.observe(make_message("agent-01", claims=(make_claim("b1", "agent-01"),)))
    tracker.refresh({LineageEdge("a1", "x"), LineageEdge("a1", "y"), LineageEdge("b1", "z")}, 0)
    scores = tracker.scores(("agent-00", "agent-01", "agent-02"), tick=9)
    # Never audited: gap = tick + 1 = 10.
    assert scores == {"agent-00": 30.0, "agent-01": 20.0, "agent-02": 10.0}


def test_an_audit_resets_gap_and_fanout():
    tracker = ExposureTracker(refresh_ticks=1)
    tracker.observe(make_message("agent-00", claims=(make_claim("a1", "agent-00"),)))
    tracker.refresh({LineageEdge("a1", "x")}, 0)
    tracker.audited("agent-00", tick=5)
    assert tracker.scores(("agent-00",), tick=8) == {"agent-00": 3.0}


def test_refresh_cadence():
    tracker = ExposureTracker(refresh_ticks=10)
    assert tracker.refresh_due(0)
    tracker.refresh(set(), 0)
    assert not tracker.refresh_due(9)
    assert tracker.refresh_due(10)


def test_the_arm_audits_the_highest_exposure_first_within_budget():
    s = Sampler(SamplerConfig(arm=Arm.EXPOSURE, budget_pct=0.12), 0.06, random.Random(0))
    s.accrue(1.0, 0.0)  # $0.12 -> two audits
    exposure = {"agent-00": 1.0, "agent-01": 50.0, "agent-02": 7.0, "agent-03": 0.0,
                "agent-04": 2.0, "agent-05": 3.0}
    assert s.select(AGENTS, {}, exposure) == ("agent-01", "agent-02")
    assert s.pool_dollars < 0.06


def test_ties_are_broken_at_random_not_alphabetically():
    firsts = set()
    for seed in range(20):
        s = Sampler(SamplerConfig(arm=Arm.EXPOSURE, budget_pct=0.06), 0.06, random.Random(seed))
        s.accrue(1.0, 0.0)
        firsts.add(s.select(AGENTS, {}, dict.fromkeys(AGENTS, 5.0))[0])
    assert len(firsts) > 2


def test_exposure_runs_on_observables_and_is_deterministic(config, test_pricing):
    """The guard raises if any HARPY-side code touches ground truth."""
    spec = RunSpec(3, Severity.SUBTLE, Arm.EXPOSURE, 0.1, 0.5, followup_audits=2)
    a = run_simulation(config, spec, test_pricing)
    b = run_simulation(config, spec, test_pricing)
    assert a.audits_run > 0
    assert (a.audits_run, a.detected_tick, a.followup_audits_drawn) == (
        b.audits_run, b.detected_tick, b.followup_audits_drawn)


def test_exposure_spreads_audits_more_evenly_than_random(config, test_pricing):
    """Staleness weighting is a coverage policy: fewer agents go unaudited."""
    def never_audited(arm):
        missed = 0
        for seed in range(3):
            spec = RunSpec(seed, Severity.SUBTLE, arm, 0.2, 1.0)
            record = run_simulation(config, spec, test_pricing)
            missed += int(record.audits_on_faulty_agent == 0)
        return missed
    assert never_audited(Arm.EXPOSURE) <= never_audited(Arm.RANDOM)
