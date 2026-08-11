"""All five arms exist, behave as described, and stay inside the budget."""

from __future__ import annotations

import random

import pytest

from harpy.sampler import Sampler, SamplerConfig
from harpy.types import Arm

AGENTS = tuple(f"agent-{i:02d}" for i in range(30))
AUDIT_COST = 0.06


def sampler(arm: Arm, budget_pct: float = 0.05, reserve_fraction: float = 0.5, seed: int = 0):
    return Sampler(
        SamplerConfig(arm=arm, budget_pct=budget_pct, reserve_fraction=reserve_fraction),
        audit_cost_dollars=AUDIT_COST,
        rng=random.Random(seed),
    )


def flat_scores(value: float = 0.05) -> dict[str, float]:
    return dict.fromkeys(AGENTS, value)


def skewed_scores(target: str = "agent-07", high: float = 0.8, low: float = 0.01):
    return {agent: (high if agent == target else low) for agent in AGENTS}


def test_all_five_arms_are_present():
    assert {arm.value for arm in Arm} == {"NONE", "TIER0_ONLY", "RANDOM", "DIRECTED", "HYBRID"}


@pytest.mark.parametrize("arm", [Arm.NONE, Arm.TIER0_ONLY])
def test_the_non_auditing_arms_never_audit(arm):
    sampled = sampler(arm, budget_pct=1.0)
    for _ in range(50):
        sampled.accrue(100.0, 0.0)
        assert sampled.select(AGENTS, flat_scores()) == ()
    assert sampled.audits_drawn == 0


@pytest.mark.parametrize("arm", [Arm.RANDOM, Arm.DIRECTED, Arm.HYBRID])
def test_the_auditing_arms_audit(arm):
    sampled = sampler(arm, budget_pct=0.5)
    sampled.accrue(10.0, 0.0)
    assert len(sampled.select(AGENTS, flat_scores())) > 0


@pytest.mark.parametrize("arm", list(Arm))
def test_no_arm_spends_more_than_the_pool_holds(arm):
    sampled = sampler(arm, budget_pct=0.2)
    for _ in range(100):
        sampled.accrue(1.0, 0.0)
        sampled.select(AGENTS, flat_scores())
        assert sampled.pool_dollars >= 0.0
    # 100 ticks x $1.00 worker x 20% = $20 of allowance, minus the capped
    # carryover that was never spendable.
    assert sampled.dollars_committed <= 20.0


@pytest.mark.parametrize("arm", [Arm.RANDOM, Arm.DIRECTED, Arm.HYBRID])
def test_audit_count_is_derived_from_the_budget(arm):
    """Doubling the allowance roughly doubles the audits. Nothing configures a count."""
    counts = {}
    for budget in (0.05, 0.10, 0.20):
        sampled = sampler(arm, budget_pct=budget)
        for _ in range(200):
            sampled.accrue(0.1, 0.0)
            sampled.select(AGENTS, flat_scores())
        counts[budget] = sampled.audits_drawn
    assert counts[0.05] < counts[0.10] < counts[0.20]
    assert counts[0.10] == pytest.approx(2 * counts[0.05], rel=0.15)


@pytest.mark.parametrize("arm", list(Arm))
def test_a_zero_budget_buys_nothing(arm):
    sampled = sampler(arm, budget_pct=0.0)
    for _ in range(50):
        sampled.accrue(100.0, 0.0)
        assert sampled.select(AGENTS, flat_scores()) == ()


def test_random_ignores_tier0_scores():
    picks = []
    for seed in range(40):
        sampled = sampler(Arm.RANDOM, budget_pct=0.05, seed=seed)
        sampled.accrue(2.0, 0.0)
        picks.extend(sampled.select(AGENTS, skewed_scores()))
    # The high-scoring agent gets no more attention than anyone else.
    assert picks.count("agent-07") < len(picks) / 4


def test_directed_concentrates_on_the_high_scoring_agent():
    picks = []
    for seed in range(40):
        sampled = sampler(Arm.DIRECTED, budget_pct=0.05, seed=seed)
        sampled.accrue(2.0, 0.0)
        picks.extend(sampled.select(AGENTS, skewed_scores()))
    assert picks.count("agent-07") > len(picks) / 2


def test_directed_falls_back_to_uniform_when_there_is_no_signal_yet():
    picks = []
    for seed in range(60):
        sampled = sampler(Arm.DIRECTED, budget_pct=0.05, seed=seed)
        sampled.accrue(2.0, 0.0)
        picks.extend(sampled.select(AGENTS, dict.fromkeys(AGENTS, 0.0)))
    assert len(set(picks)) > 10


def test_hybrid_reserve_fraction_interpolates_between_random_and_directed():
    def concentration(reserve: float) -> float:
        picks = []
        for seed in range(60):
            sampled = sampler(Arm.HYBRID, budget_pct=0.05, reserve_fraction=reserve, seed=seed)
            sampled.accrue(4.0, 0.0)
            picks.extend(sampled.select(AGENTS, skewed_scores()))
        return picks.count("agent-07") / max(len(picks), 1)

    all_directed = concentration(0.0)
    half = concentration(0.5)
    all_random = concentration(1.0)
    assert all_directed > half > all_random


def test_hybrid_at_reserve_zero_matches_directed():
    hybrid = sampler(Arm.HYBRID, budget_pct=0.1, reserve_fraction=0.0, seed=4)
    directed = sampler(Arm.DIRECTED, budget_pct=0.1, seed=4)
    for _ in range(30):
        hybrid.accrue(1.0, 0.0)
        directed.accrue(1.0, 0.0)
        assert hybrid.select(AGENTS, skewed_scores()) == directed.select(AGENTS, skewed_scores())


def test_no_agent_is_audited_twice_in_one_tick():
    sampled = sampler(Arm.HYBRID, budget_pct=1.0)
    sampled.accrue(100.0, 0.0)
    picks = sampled.select(AGENTS, skewed_scores())
    assert len(picks) == len(set(picks))
    assert len(picks) <= len(AGENTS)


def test_tier0_spend_comes_off_the_same_allowance():
    sampled = sampler(Arm.RANDOM, budget_pct=0.1)
    sampled.accrue(10.0, 0.0)
    without_tier0 = sampled.pool_dollars

    sampled = sampler(Arm.RANDOM, budget_pct=0.1)
    sampled.accrue(10.0, 0.25)
    assert sampled.pool_dollars == pytest.approx(without_tier0 - 0.25)


def test_carryover_is_capped():
    sampled = sampler(Arm.RANDOM, budget_pct=0.1)
    for _ in range(100):
        sampled.accrue(1.0, 0.0)  # $0.10/tick allowance, never spent
    assert sampled.pool_dollars == pytest.approx(5 * 0.10)


def test_reserve_fraction_outside_the_unit_interval_is_rejected():
    with pytest.raises(ValueError):
        SamplerConfig(arm=Arm.HYBRID, budget_pct=0.05, reserve_fraction=1.5)
