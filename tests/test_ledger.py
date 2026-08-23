"""Cost arithmetic, budget-bounded audit counts, and discard scaling."""

from __future__ import annotations

import random
from dataclasses import fields, replace

import pytest

from harpy.ledger import CostAccountant
from harpy.sampler import Sampler, SamplerConfig
from harpy.types import (
    PER_AGENT_OVERHEAD_COMPONENTS,
    REVIEW_COMPONENTS,
    Arm,
    AuditResult,
    BudgetLedger,
)


def a_ledger(**overrides) -> BudgetLedger:
    base = {
        "worker_dollars": 100.0,
        "sentinel_dollars": 3.0,
        "tier0_dollars": 0.5,
        "discarded_work_dollars": 4.0,
        "rerun_work_dollars": 4.0,
        "triage_dollars": 2.0,
        "investigation_dollars": 6.0,
    }
    base.update(overrides)
    return BudgetLedger(**base)


def test_incremental_excludes_worker_dollars():
    ledger = a_ledger()
    assert ledger.incremental() == pytest.approx(19.5)
    assert ledger.overhead_pct() == pytest.approx(0.195)


def test_overhead_pct_with_no_worker_spend_is_zero():
    assert BudgetLedger(sentinel_dollars=5.0).overhead_pct() == 0.0


def test_an_unsourced_pricing_file_reports_problems(placeholder_pricing):
    """The rejection path, against a fixture built to be rejected.

    This previously asserted that configs/pricing.yaml itself was an unverified
    placeholder. That was true while the sweeps were blocked on missing prices
    and false the moment real, sourced figures were entered — which is the
    intended end state, not a regression. What must stay pinned is that a file
    without provenance is rejected; whether the shipped file has provenance is
    checked separately, and in the opposite direction.
    """
    problems = placeholder_pricing.verification_problems()
    assert problems, "a file with no source or access date must not be reportable"
    assert any("source" in problem for problem in problems)


def test_model_price_arithmetic(test_pricing):
    # 1M input + 1M output at 3/15 per Mtok.
    assert test_pricing.worker_model.cost(1_000_000, 1_000_000) == pytest.approx(18.0)
    assert test_pricing.sentinel_model.cost(0, 200_000) == pytest.approx(5.0)


def test_discarded_work_scales_with_elapsed_spend_not_a_flat_constant(
    test_pricing, review_config
):
    accountant = CostAccountant(test_pricing, review_config, rerun_multiplier=1.0)
    for _ in range(10):
        accountant.charge_worker("agent-00", 1400, 420)
    early_discard, early_rerun = accountant.charge_isolation("agent-00")
    accountant.reset_agent_spend("agent-00")

    for _ in range(40):
        accountant.charge_worker("agent-00", 1400, 420)
    late_discard, _ = accountant.charge_isolation("agent-00")

    assert early_discard > 0.0
    assert early_rerun == pytest.approx(early_discard)
    assert late_discard == pytest.approx(4 * early_discard)


def test_clean_restart_resets_the_discard_clock(test_pricing, review_config):
    accountant = CostAccountant(test_pricing, review_config)
    for _ in range(10):
        accountant.charge_worker("agent-00", 1400, 420)
    accountant.reset_agent_spend("agent-00")
    discarded, rerun = accountant.charge_isolation("agent-00")
    assert discarded == 0.0
    assert rerun == 0.0


def test_rerun_multiplier_applies(test_pricing, review_config):
    accountant = CostAccountant(test_pricing, review_config, rerun_multiplier=2.5)
    accountant.charge_worker("agent-00", 1400, 420)
    discarded, rerun = accountant.charge_isolation("agent-00")
    assert rerun == pytest.approx(2.5 * discarded)


# -- FIX 1: review cost is dimensionless ------------------------------------


def test_the_flat_per_alert_constant_is_gone(config):
    """A flat human_review_dollars would re-encode fleet size into the ratio."""
    assert "human_review_dollars" not in config["response"]
    assert "human_review_dollars" not in {f.name for f in fields(BudgetLedger)}
    assert {"triage_dollars", "investigation_dollars"} <= {
        f.name for f in fields(BudgetLedger)
    }


def test_review_cost_is_minutes_times_a_rate(review_config):
    assert review_config.triage_dollars() == pytest.approx(3.0 / 60.0 * 75.0)
    assert review_config.investigation_dollars() == pytest.approx(45.0 / 60.0 * 75.0)
    # (triage + escalation_rate * investigation) / 60 * rate
    assert review_config.expected_dollars_per_review() == pytest.approx(
        (3.0 + 0.25 * 45.0) / 60.0 * 75.0
    )


def test_triage_is_charged_for_every_review_and_investigation_only_on_escalation(
    test_pricing, review_config
):
    always = CostAccountant(
        test_pricing,
        replace(review_config, escalation_rate=1.0, batch_window_ticks=0),
        review_rng=random.Random(0),
    )
    never = CostAccountant(
        test_pricing,
        replace(review_config, escalation_rate=0.0, batch_window_ticks=0),
        review_rng=random.Random(0),
    )
    for tick in range(4):
        always.charge_human_review("agent-00", tick)
        never.charge_human_review("agent-00", tick)

    assert always.ledger.triage_dollars == pytest.approx(4 * review_config.triage_dollars())
    assert never.ledger.triage_dollars == pytest.approx(4 * review_config.triage_dollars())
    assert always.ledger.investigation_dollars == pytest.approx(
        4 * review_config.investigation_dollars()
    )
    assert never.ledger.investigation_dollars == 0.0


def test_batching_collapses_same_agent_alerts_inside_the_window(test_pricing, review_config):
    """Ten alerts about one agent inside the window are one person reading one case."""
    review = replace(review_config, batch_window_ticks=20, escalation_rate=0.0)
    accountant = CostAccountant(test_pricing, review, review_rng=random.Random(0))
    for tick in range(10):
        accountant.charge_human_review("agent-00", tick)

    assert accountant.alerts_charged == 10
    assert accountant.reviews_charged == 1
    assert accountant.alerts_batched == 9
    assert accountant.ledger.triage_dollars == pytest.approx(review.triage_dollars())


def test_batching_does_not_collapse_across_agents_or_across_the_window(
    test_pricing, review_config
):
    review = replace(review_config, batch_window_ticks=20, escalation_rate=0.0)
    accountant = CostAccountant(test_pricing, review, review_rng=random.Random(0))
    accountant.charge_human_review("agent-00", 0)
    accountant.charge_human_review("agent-01", 0)  # different agent: its own review
    accountant.charge_human_review("agent-00", 19)  # inside the window: collapsed
    accountant.charge_human_review("agent-00", 20)  # window closed: a second review

    assert accountant.reviews_charged == 3
    assert accountant.alerts_batched == 1
    assert accountant.ledger.triage_dollars == pytest.approx(3 * review.triage_dollars())


def test_batched_alerts_do_not_draw_an_escalation(test_pricing, review_config):
    """A collapsed alert must not spend an rng draw, or batching would be cosmetic."""
    review = replace(review_config, batch_window_ticks=20, escalation_rate=1.0)
    accountant = CostAccountant(test_pricing, review, review_rng=random.Random(11))
    for tick in range(10):
        accountant.charge_human_review("agent-00", tick)
    assert accountant.escalations_charged == 1
    assert accountant.ledger.investigation_dollars == pytest.approx(
        review.investigation_dollars()
    )


def test_escalation_draws_are_seeded_and_reproducible(test_pricing, review_config):
    review = replace(review_config, batch_window_ticks=0)

    def escalations(seed: int) -> tuple[int, float]:
        accountant = CostAccountant(test_pricing, review, review_rng=random.Random(seed))
        for tick in range(200):
            accountant.charge_human_review("agent-00", tick)
        return accountant.escalations_charged, accountant.ledger.investigation_dollars

    assert escalations(4) == escalations(4)
    # Drawn per review rather than averaged into a constant: two seeds must be
    # able to disagree, and the count must not equal the expectation exactly.
    assert escalations(4) != escalations(9)
    assert escalations(4)[0] != pytest.approx(200 * review.escalation_rate)


def test_escalation_draw_count_does_not_depend_on_the_configured_rate(
    test_pricing, review_config
):
    """The stream advances once per review either way, so rates stay comparable."""

    def tail(rate: float) -> float:
        review = replace(review_config, escalation_rate=rate, batch_window_ticks=0)
        rng = random.Random(2)
        accountant = CostAccountant(test_pricing, review, review_rng=rng)
        for tick in range(50):
            accountant.charge_human_review("agent-00", tick)
        return rng.random()

    assert tail(0.0) == tail(1.0) == tail(0.25)


# -- FIX 2: fleet scale ------------------------------------------------------


@pytest.mark.parametrize("fleet_scale", [1.0, 10.0, 100.0, 1000.0])
def test_fleet_scale_multiplies_the_four_per_agent_components_and_neither_review_one(
    fleet_scale,
):
    slice_ledger = a_ledger()
    scaled = a_ledger(fleet_scale=fleet_scale)

    for name in PER_AGENT_OVERHEAD_COMPONENTS:
        assert scaled.fleet_component(name) == pytest.approx(
            getattr(slice_ledger, name) * fleet_scale
        )
    for name in REVIEW_COMPONENTS:
        assert scaled.fleet_component(name) == pytest.approx(getattr(slice_ledger, name))
    assert scaled.fleet_worker_dollars() == pytest.approx(100.0 * fleet_scale)
    # And exactly those four: nothing else moved.
    assert set(PER_AGENT_OVERHEAD_COMPONENTS) == {
        "sentinel_dollars",
        "tier0_dollars",
        "discarded_work_dollars",
        "rerun_work_dollars",
    }
    assert set(REVIEW_COMPONENTS) == {"triage_dollars", "investigation_dollars"}


def test_review_share_of_overhead_falls_as_one_over_fleet_scale():
    shares = [a_ledger(fleet_scale=s).review_cost_share_of_overhead() for s in (1, 10, 100, 1000)]
    assert shares == sorted(shares, reverse=True)
    assert shares[0] > 0.4          # dominant on a 30-agent slice
    assert shares[-1] < 0.001       # a rounding error on a 30,000-agent fleet
    no_reviews = a_ledger(triage_dollars=0.0, investigation_dollars=0.0)
    assert no_reviews.review_cost_share_of_overhead() == 0.0


def test_overhead_ratio_falls_towards_the_scale_invariant_floor():
    ratios = [a_ledger(fleet_scale=s).overhead_pct() for s in (1, 10, 100, 1000)]
    assert ratios == sorted(ratios, reverse=True)
    # 11.5 per 100 of per-agent overhead is the floor no fleet size can undercut.
    assert ratios[-1] == pytest.approx(0.115, abs=1e-3)


def test_as_dict_reports_fleet_level_dollars_and_the_scale_it_used():
    payload = a_ledger(fleet_scale=10.0).as_dict()
    assert payload["fleet_scale"] == 10.0
    assert payload["worker_dollars"] == pytest.approx(1000.0)
    assert payload["sentinel_dollars"] == pytest.approx(30.0)
    assert payload["triage_dollars"] == pytest.approx(2.0)
    assert payload["investigation_dollars"] == pytest.approx(6.0)
    assert payload["incremental_dollars"] == pytest.approx(115.0 + 8.0)


@pytest.mark.parametrize("budget_pct", [0.01, 0.025, 0.05, 0.10, 0.20])
@pytest.mark.parametrize("arm", [Arm.RANDOM, Arm.DIRECTED, Arm.HYBRID])
def test_derived_audit_count_never_exceeds_the_budget(
    test_pricing, review_config, budget_pct, arm
):
    """Audits are bought, not configured: spend can never outrun the allowance."""
    accountant = CostAccountant(test_pricing, review_config)
    audit_cost = accountant.audit_cost(9000, 600)
    sampler = Sampler(
        SamplerConfig(arm=arm, budget_pct=budget_pct, reserve_fraction=0.5),
        audit_cost_dollars=audit_cost,
        rng=random.Random(7),
    )
    agents = tuple(f"agent-{i:02d}" for i in range(30))
    scores = {agent: 0.1 for agent in agents}

    worker_total = 0.0
    for tick in range(200):
        worker_this_tick = sum(
            accountant.charge_worker(agent, 1400, 420) for agent in agents
        )
        worker_total += worker_this_tick
        sampler.accrue(worker_this_tick, 0.0)
        for agent in sampler.select(agents, scores):
            accountant.charge_audit(
                AuditResult(agent, tick, False, 9000, 600)
            )

    assert accountant.audits_charged == sampler.audits_drawn
    assert accountant.ledger.sentinel_dollars == pytest.approx(sampler.dollars_committed)
    # Carryover is capped, so cumulative spend stays under the cumulative allowance.
    assert accountant.ledger.sentinel_dollars <= budget_pct * worker_total + audit_cost


def test_zero_price_audits_are_still_bounded_by_one_per_agent_per_tick(
    placeholder_pricing, review_config
):
    """Placeholder pricing makes audits free; the per-agent cap keeps it finite."""
    accountant = CostAccountant(placeholder_pricing, review_config)
    sampler = Sampler(
        SamplerConfig(arm=Arm.RANDOM, budget_pct=0.05),
        audit_cost_dollars=accountant.audit_cost(9000, 600),
        rng=random.Random(3),
    )
    agents = tuple(f"agent-{i:02d}" for i in range(30))
    sampler.accrue(0.0, 0.0)
    assert len(sampler.select(agents, dict.fromkeys(agents, 0.0))) == len(agents)
