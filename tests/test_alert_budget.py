"""The second binding constraint: the cap, the adaptation, and what they cost.

A cap that can be exceeded is not a cap, so the first group of tests drives the
budget past saturation and checks the reported rate against it. The rest cover
the two branches at the cap — raise the gate, or suppress and count — and the
ground-truth scoring of what suppression cost, which has to happen on the
world's side of the isolation guard.
"""

from __future__ import annotations

import math

import pytest

from harpy.alert_budget import AlertBudget, AlertBudgetConfig
from harpy.metrics import run_metrics
from harpy.simulation import RunSpec, run_simulation
from harpy.types import Arm, GroundTruthLeakError, Severity

from conftest import make_alert_budget, make_claim

# 30 agents at one tick per minute is half an agent-hour per tick, so a cap of
# 0.05/agent-hour buys one alert every 40 ticks.
AGENTS = 30


def drive(budget: AlertBudget, ticks: int, agents: int = AGENTS) -> int:
    """Ask for an alert every tick for ``ticks`` ticks. Returns alerts fired."""
    for _ in range(ticks):
        budget.observe_tick(agents)
        if budget.allows():
            budget.record_alert()
        else:
            budget.record_suppression()
        budget.settle()
    return budget.alerts_fired


# -- the cap ----------------------------------------------------------------


@pytest.mark.parametrize("cap", [0.01, 0.05, 0.25, 1.0])
def test_the_cap_is_never_exceeded_under_saturation(cap):
    """Demand an alert every single tick; the reported rate still clears the cap."""
    budget = make_alert_budget(max_alerts_per_agent_hour=cap, threshold_adaptation=False)
    drive(budget, ticks=500)
    assert budget.alerts_per_agent_hour() <= cap
    assert budget.alerts_fired + budget.suppressed_alerts == 500


@pytest.mark.parametrize("cap", [0.01, 0.05, 0.25, 1.0])
def test_the_cap_is_never_exceeded_at_any_prefix_of_the_run(cap):
    """Not just at the end: a cap satisfied only on average is not satisfied."""
    budget = make_alert_budget(max_alerts_per_agent_hour=cap, threshold_adaptation=False)
    for _ in range(500):
        budget.observe_tick(AGENTS)
        if budget.allows():
            budget.record_alert()
        budget.settle()
        assert budget.alerts_per_agent_hour() <= cap


def test_an_infinite_cap_never_refuses():
    budget = make_alert_budget(max_alerts_per_agent_hour=math.inf)
    assert drive(budget, ticks=200) == 200
    assert budget.suppressed_alerts == 0


def test_a_zero_cap_always_refuses():
    budget = make_alert_budget(max_alerts_per_agent_hour=0.0, threshold_adaptation=False)
    assert drive(budget, ticks=50) == 0
    assert budget.suppressed_alerts == 50


def test_the_cap_lets_alerts_through_once_enough_agent_hours_have_accrued():
    """0.05/agent-hour over 30 agents is roughly one alert per 40 ticks, not zero.

    The rolling window is the tighter of the two constraints here: 100 ticks of
    30 agents is 50 agent-hours, which buys 2 alerts, not 2.5 — an alert is
    indivisible. So ~2 per window rather than the ~2.5 the cumulative rate alone
    would allow.
    """
    budget = make_alert_budget(max_alerts_per_agent_hour=0.05, threshold_adaptation=False)
    fired = drive(budget, ticks=400)
    assert 6 <= fired <= 10
    assert budget.alerts_per_agent_hour() <= 0.05


def test_a_quiet_stretch_buys_back_headroom():
    """The rolling window is what makes the constraint a rate and not a quota."""
    budget = make_alert_budget(max_alerts_per_agent_hour=0.05, threshold_adaptation=False)
    drive(budget, ticks=60)          # saturate
    assert not budget.allows()
    for _ in range(60):              # ...then ask for nothing for a while
        budget.observe_tick(AGENTS)
        budget.settle()
    assert budget.allows()


# -- threshold adaptation ---------------------------------------------------


def test_adaptation_raises_the_threshold_while_the_cap_binds():
    budget = make_alert_budget(max_alerts_per_agent_hour=0.05, threshold_adaptation=True)
    start = budget.isolation_threshold
    drive(budget, ticks=60)
    assert budget.isolation_threshold > start


def test_adaptation_decays_back_down_once_there_is_headroom():
    budget = make_alert_budget(max_alerts_per_agent_hour=0.05, threshold_adaptation=True)
    drive(budget, ticks=60)
    raised = budget.isolation_threshold
    for _ in range(200):
        budget.observe_tick(AGENTS)
        budget.settle()
    assert budget.isolation_threshold < raised
    assert budget.isolation_threshold == pytest.approx(budget.config.base_isolation_threshold)


def test_adaptation_never_decays_below_the_configured_gate():
    budget = make_alert_budget(max_alerts_per_agent_hour=math.inf, threshold_adaptation=True)
    drive(budget, ticks=200)
    assert budget.isolation_threshold == pytest.approx(budget.config.base_isolation_threshold)


def test_adaptation_is_bounded_by_threshold_max():
    budget = make_alert_budget(
        max_alerts_per_agent_hour=0.0, threshold_adaptation=True, threshold_max=3.4
    )
    drive(budget, ticks=500)
    assert budget.isolation_threshold == pytest.approx(3.4)


def test_adaptation_off_leaves_the_threshold_alone():
    budget = make_alert_budget(max_alerts_per_agent_hour=0.0, threshold_adaptation=False)
    drive(budget, ticks=200)
    assert budget.isolation_threshold == pytest.approx(budget.config.base_isolation_threshold)


def test_effective_threshold_is_time_weighted_and_sits_between_base_and_max():
    budget = make_alert_budget(
        max_alerts_per_agent_hour=0.0, threshold_adaptation=True, threshold_max=4.0
    )
    drive(budget, ticks=100)
    effective = budget.effective_isolation_threshold()
    assert budget.config.base_isolation_threshold <= effective <= 4.0
    # Time-weighted, not final: the run spent its first ticks at the base gate.
    assert effective < budget.isolation_threshold


def test_effective_threshold_equals_the_configured_gate_when_the_cap_never_binds():
    budget = make_alert_budget(max_alerts_per_agent_hour=math.inf)
    drive(budget, ticks=200)
    assert budget.effective_isolation_threshold() == pytest.approx(
        budget.config.base_isolation_threshold
    )


# -- configuration ----------------------------------------------------------


def test_config_rejects_a_ceiling_below_the_gate_it_is_meant_to_raise():
    with pytest.raises(ValueError, match="threshold_max"):
        AlertBudgetConfig(
            max_alerts_per_agent_hour=0.05,
            threshold_adaptation=True,
            threshold_step=0.2,
            threshold_max=1.0,
            window_ticks=100,
            tick_seconds=60,
            base_isolation_threshold=2.6,
        )


def test_the_swept_cap_overrides_the_configured_one(config):
    from_yaml = AlertBudgetConfig.from_config(config)
    swept = AlertBudgetConfig.from_config(config, 0.25)
    assert from_yaml.max_alerts_per_agent_hour == pytest.approx(0.05)
    assert swept.max_alerts_per_agent_hour == pytest.approx(0.25)
    assert swept.base_isolation_threshold == pytest.approx(
        config["suspicion"]["isolation_threshold"]
    )


# -- end to end, inside a run -----------------------------------------------


def a_spec(cap: float, arm: Arm = Arm.HYBRID, seed: int = 0) -> RunSpec:
    return RunSpec(
        seed=seed,
        severity=Severity.OVERT,
        arm=arm,
        budget_pct=0.10,
        lineage_fidelity=1.0,
        reserve_fraction=0.5,
        max_alerts_per_agent_hour=cap,
    )


@pytest.mark.parametrize("cap", [0.01, 0.05, 0.25])
def test_a_whole_run_stays_under_its_cap(config, test_pricing, cap):
    metrics = run_metrics(run_simulation(config, a_spec(cap), test_pricing))
    assert metrics["alerts_per_agent_hour"] <= cap


def test_suppressed_alerts_are_counted_when_adaptation_is_off(config, test_pricing):
    """With the gate frozen, the cap eats the alerts and the count records it."""
    frozen = {**config, "alert_budget": {**config["alert_budget"], "threshold_adaptation": False}}
    frozen_record = run_simulation(frozen, a_spec(0.0), test_pricing)

    assert frozen_record.suppressed_alerts > 0
    assert frozen_record.alerts_fired == 0
    assert frozen_record.ledger.review_dollars() == 0.0
    assert frozen_record.effective_isolation_threshold == pytest.approx(
        frozen_record.base_isolation_threshold
    )


def test_adaptation_pays_for_the_saved_attention_in_detection_delay(config, test_pricing):
    """The two branches at the cap are a real trade, not a relabelling.

    Frozen: the isolation still happens on time and the alert is dropped.
    Adaptive: the gate rises, so the isolation happens later — the layer really
    is being more conservative, and the delay is what that costs.
    """
    frozen = {**config, "alert_budget": {**config["alert_budget"], "threshold_adaptation": False}}
    adaptive_record = run_simulation(config, a_spec(0.0), test_pricing)
    frozen_record = run_simulation(frozen, a_spec(0.0), test_pricing)

    assert adaptive_record.effective_isolation_threshold > (
        frozen_record.effective_isolation_threshold
    )
    assert adaptive_record.detected_tick > frozen_record.detected_tick


def test_an_uncapped_run_fires_more_alerts_than_a_tightly_capped_one(config, test_pricing):
    loose = run_simulation(config, a_spec(math.inf), test_pricing)
    tight = run_simulation(config, a_spec(0.01), test_pricing)
    assert loose.alerts_fired > tight.alerts_fired


def test_detections_lost_to_alert_cap_is_scored_from_ground_truth(config, test_pricing):
    """A cap of zero must cost detections that an uncapped run gets for free."""
    capped = run_simulation(config, a_spec(0.0, arm=Arm.TIER0_ONLY), test_pricing)
    uncapped = run_simulation(config, a_spec(math.inf, arm=Arm.TIER0_ONLY), test_pricing)

    assert capped.detections_lost_to_alert_cap > 0
    assert uncapped.detections_lost_to_alert_cap == 0
    assert uncapped.detected_tick is not None
    assert capped.detected_tick is None


def test_detections_lost_is_scored_against_the_fault_source_not_any_agent(
    config, test_pricing
):
    """It counts crossings by the faulty agent, so an arm with no gate scores zero.

    NONE runs no oversight at all: nobody's suspicion ever moves, so nobody
    crosses, so nothing is lost to the cap however tight the cap is. A metric
    that counted any agent's crossing — or counted ticks rather than crossings —
    would not come out at zero here.
    """
    record = run_simulation(config, a_spec(0.0, arm=Arm.NONE), test_pricing)
    assert record.detections_lost_to_alert_cap == 0

    capped = run_simulation(config, a_spec(0.0, arm=Arm.TIER0_ONLY), test_pricing)
    # A crossing count, not a tick count: the faulty agent sits above the gate
    # for hundreds of ticks once it gets there.
    assert 0 < capped.detections_lost_to_alert_cap < 10


def test_the_alert_budget_still_cannot_read_ground_truth():
    """It is on HARPY's decision path, so the guard covers it like the other five."""
    from harpy import alert_budget as alert_budget_module

    code = compile("claim.is_corrupt", alert_budget_module.__file__, "eval")
    with pytest.raises(GroundTruthLeakError, match="is_corrupt"):
        eval(code, {"__builtins__": {}}, {"claim": make_claim(is_corrupt=True)})  # noqa: S307
