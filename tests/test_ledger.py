"""Cost arithmetic, budget-bounded audit counts, and discard scaling."""

from __future__ import annotations

import random

import pytest

from harpy.ledger import CostAccountant, load_pricing
from harpy.sampler import Sampler, SamplerConfig
from harpy.types import Arm, AuditResult, BudgetLedger

from conftest import REPO_ROOT


def test_incremental_excludes_worker_dollars():
    ledger = BudgetLedger(
        worker_dollars=100.0,
        sentinel_dollars=3.0,
        tier0_dollars=0.5,
        discarded_work_dollars=4.0,
        rerun_work_dollars=4.0,
        human_review_dollars=25.0,
    )
    assert ledger.incremental() == pytest.approx(36.5)
    assert ledger.overhead_pct() == pytest.approx(0.365)


def test_overhead_pct_with_no_worker_spend_is_zero():
    assert BudgetLedger(sentinel_dollars=5.0).overhead_pct() == 0.0


def test_shipped_pricing_is_unverified_placeholder():
    pricing = load_pricing(REPO_ROOT / "configs" / "pricing.yaml")
    problems = pricing.verification_problems()
    assert problems, "configs/pricing.yaml must ship unverified"
    assert any("source" in problem for problem in problems)


def test_model_price_arithmetic(test_pricing):
    # 1M input + 1M output at 3/15 per Mtok.
    assert test_pricing.worker_model.cost(1_000_000, 1_000_000) == pytest.approx(18.0)
    assert test_pricing.sentinel_model.cost(0, 200_000) == pytest.approx(5.0)


def test_discarded_work_scales_with_elapsed_spend_not_a_flat_constant(test_pricing):
    accountant = CostAccountant(test_pricing, rerun_multiplier=1.0)
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


def test_clean_restart_resets_the_discard_clock(test_pricing):
    accountant = CostAccountant(test_pricing)
    for _ in range(10):
        accountant.charge_worker("agent-00", 1400, 420)
    accountant.reset_agent_spend("agent-00")
    discarded, rerun = accountant.charge_isolation("agent-00")
    assert discarded == 0.0
    assert rerun == 0.0


def test_rerun_multiplier_applies(test_pricing):
    accountant = CostAccountant(test_pricing, rerun_multiplier=2.5)
    accountant.charge_worker("agent-00", 1400, 420)
    discarded, rerun = accountant.charge_isolation("agent-00")
    assert rerun == pytest.approx(2.5 * discarded)


def test_human_review_is_a_flat_per_alert_cost(test_pricing):
    accountant = CostAccountant(test_pricing, human_review_dollars=25.0)
    accountant.charge_human_review()
    accountant.charge_human_review()
    assert accountant.ledger.human_review_dollars == pytest.approx(50.0)


@pytest.mark.parametrize("budget_pct", [0.01, 0.025, 0.05, 0.10, 0.20])
@pytest.mark.parametrize("arm", [Arm.RANDOM, Arm.DIRECTED, Arm.HYBRID])
def test_derived_audit_count_never_exceeds_the_budget(test_pricing, budget_pct, arm):
    """Audits are bought, not configured: spend can never outrun the allowance."""
    accountant = CostAccountant(test_pricing)
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


def test_zero_price_audits_are_still_bounded_by_one_per_agent_per_tick(placeholder_pricing):
    """Placeholder pricing makes audits free; the per-agent cap keeps it finite."""
    accountant = CostAccountant(placeholder_pricing)
    sampler = Sampler(
        SamplerConfig(arm=Arm.RANDOM, budget_pct=0.05),
        audit_cost_dollars=accountant.audit_cost(9000, 600),
        rng=random.Random(3),
    )
    agents = tuple(f"agent-{i:02d}" for i in range(30))
    sampler.accrue(0.0, 0.0)
    assert len(sampler.select(agents, dict.fromkeys(agents, 0.0))) == len(agents)
