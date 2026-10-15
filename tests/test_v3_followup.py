"""v3 targeted re-audit: queued on a flag, served first, paid from the same pool."""

from __future__ import annotations

import random

import pytest

from harpy.sampler import Sampler, SamplerConfig
from harpy.simulation import RunSpec, run_simulation
from harpy.types import Arm, Severity

AGENTS = tuple(f"agent-{i:02d}" for i in range(30))
AUDIT_COST = 0.06


def sampler(arm=Arm.RANDOM, budget_pct=0.05, k=4, seed=0, reserve=0.5):
    return Sampler(
        SamplerConfig(arm=arm, budget_pct=budget_pct, reserve_fraction=reserve, followup_audits=k),
        audit_cost_dollars=AUDIT_COST,
        rng=random.Random(seed),
    )


def flat(value=0.05):
    return dict.fromkeys(AGENTS, value)


@pytest.mark.parametrize("k", [-1, 1.5])
def test_followup_depth_must_be_a_non_negative_integer(k):
    with pytest.raises(ValueError):
        SamplerConfig(arm=Arm.RANDOM, budget_pct=0.05, followup_audits=k)


def test_depth_zero_queues_nothing_and_matches_v2_draws():
    """k=0 is v2: same picks from the same RNG, flags or not."""
    v2 = Sampler(SamplerConfig(arm=Arm.HYBRID, budget_pct=0.1, reserve_fraction=0.5),
                 AUDIT_COST, random.Random(7))
    v3 = sampler(Arm.HYBRID, budget_pct=0.1, k=0, seed=7)
    for _ in range(60):
        v2.accrue(1.0, 0.0)
        v3.accrue(1.0, 0.0)
        a = v2.select(AGENTS, flat())
        b = v3.select(AGENTS, flat())
        assert a == b
        for agent in b:
            v3.record_result(agent, flagged=True)
        assert v3.pending_followups() == {}


def test_a_flag_queues_k_followups_and_a_clean_result_queues_none():
    s = sampler(k=3)
    s.record_result("agent-05", flagged=False)
    assert s.pending_followups() == {}
    s.record_result("agent-05", flagged=True)
    assert s.pending_followups() == {"agent-05": 3}


def test_followups_are_served_first_one_per_tick_until_spent():
    s = sampler(Arm.RANDOM, budget_pct=0.06, k=3)  # exactly one audit per tick at $1/tick
    s.record_result("agent-05", flagged=True)
    served = []
    for _ in range(5):
        s.accrue(1.0, 0.0)
        picks = s.select(AGENTS, flat())
        served.append(picks[0] if picks else None)
    assert served[:3] == ["agent-05"] * 3
    assert served[3] != "agent-05" or served[4] != "agent-05"
    assert s.followup_audits_drawn == 3


def test_a_new_flag_refills_rather_than_stacks():
    s = sampler(k=4)
    s.record_result("agent-01", flagged=True)
    s._pending["agent-01"] = 1  # one follow-up left
    s.record_result("agent-01", flagged=True)
    assert s.pending_followups()["agent-01"] == 4
    s.record_result("agent-01", flagged=True)
    assert s.pending_followups()["agent-01"] == 4


def test_followups_on_isolated_agents_are_dropped():
    s = sampler(k=4)
    s.record_result("agent-01", flagged=True)
    s.record_result("agent-02", flagged=True)
    s.forget("agent-01")
    assert "agent-01" not in s.pending_followups()
    s.accrue(10.0, 0.0)
    picks = s.select(tuple(a for a in AGENTS if a != "agent-02"), flat())
    assert "agent-02" not in picks
    assert "agent-02" not in s.pending_followups()


@pytest.mark.parametrize("arm", [Arm.RANDOM, Arm.DIRECTED, Arm.HYBRID, Arm.EXPOSURE])
def test_followups_buy_no_extra_audits(arm):
    """Same money, same audit count: follow-ups only redirect spending."""
    counts = {}
    for k in (0, 4):
        s = sampler(arm, budget_pct=0.2, k=k, seed=3)
        flagger = random.Random(11)
        for _ in range(200):
            s.accrue(1.0, 0.0)
            for agent in s.select(AGENTS, flat()):
                s.record_result(agent, flagged=flagger.random() < 0.3)
        counts[k] = (s.audits_drawn, s.dollars_committed)
        assert s.dollars_committed <= 0.2 * 200 + 1e-9
    assert counts[0][0] == counts[4][0]
    assert counts[0][1] == pytest.approx(counts[4][1])


def test_no_agent_is_audited_twice_in_a_tick_with_followups_owed():
    s = sampler(Arm.RANDOM, budget_pct=1.0, k=4)
    for agent in AGENTS[:10]:
        s.record_result(agent, flagged=True)
    s.accrue(100.0, 0.0)
    picks = s.select(AGENTS, flat())
    assert len(picks) == len(set(picks))


# -- through the simulation ---------------------------------------------------


def spec(k: int, seed: int = 2, arm: Arm = Arm.HYBRID) -> RunSpec:
    return RunSpec(seed, Severity.SUBTLE, arm, 0.1, 1.0, 0.5, followup_audits=k)


def test_run_id_and_spec_carry_the_depth_only_when_it_is_on():
    assert "_k" not in spec(0).run_id()
    assert spec(4).run_id().endswith("_k4")
    assert spec(0).as_dict()["followup_audits"] == 0
    assert spec(4).as_dict()["followup_audits"] == 4


def test_followups_change_targeting_not_spending(config, test_pricing):
    off = run_simulation(config, spec(0), test_pricing)
    on = run_simulation(config, spec(4), test_pricing)
    assert off.followup_audits_drawn == 0
    assert on.followup_audits_drawn > 0
    # Same allowance, same audit price: the totals can differ only by the
    # last partial purchase, because isolations change worker spend slightly.
    assert abs(on.audits_run - off.audits_run) <= max(2, off.audits_run // 20)
    assert on.audit_cost_dollars == off.audit_cost_dollars > 0


def test_followups_convert_hits_into_isolations(config, test_pricing):
    """The mechanism v3 exists for: a flag is followed up until it crosses the gate.

    Not "more audits on the faulty agent": an agent caught sooner is isolated
    sooner and then receives no audits at all, so that count can fall while
    detection rises.
    """
    detected = {0: 0, 4: 0}
    for seed in range(6):
        for k in detected:
            record = run_simulation(config, spec(k, seed), test_pricing)
            detected[k] += record.detected_tick is not None
    assert detected[4] > detected[0]


def test_relay_isolations_are_a_subset_of_false_isolations(config, test_pricing):
    """Relays carried corrupt claims they did not originate; they are never the source."""
    from harpy.metrics import run_metrics

    relays = 0
    for seed in range(4):
        record = run_simulation(config, spec(4, seed), test_pricing)
        row = run_metrics(record)
        assert row["relay_isolations"] <= row["false_isolations"]
        assert all(not event.relay for event in record.isolations if event.was_faulty)
        relays += row["relay_isolations"]
    assert relays > 0
