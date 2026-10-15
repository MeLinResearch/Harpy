"""The pre-freeze check: catch an unpassable study before it is frozen."""

from __future__ import annotations

import pytest

from harpy.feasibility import (
    PILOT_SEED_FLOOR,
    arithmetic,
    assurance,
    baseline_spec,
    evaluate,
    run_pilot,
)
from harpy.ledger import load_pricing
from harpy.simulation import RunSpec, run_simulation
from harpy.types import Arm, Severity

from conftest import REPO_ROOT

DECISION = {"minimum_incremental_detection": 0.10, "maximum_overhead": 0.10, "minimum_seeds": 30}


@pytest.fixture
def v2_pricing():
    return load_pricing(REPO_ROOT / "configs" / "pricing.study.yaml")


def v2_primary(review_scaling: str = "linear", **overrides) -> RunSpec:
    fields = dict(
        seed=0, severity=Severity.SUBTLE, arm=Arm.HYBRID, budget_pct=0.05,
        lineage_fidelity=1.0, reserve_fraction=0.5, fleet_scale=100.0,
        max_alerts_per_agent_hour=0.05, worker_model="gpt-4.1-mini",
        review_scaling=review_scaling,
    )
    fields.update(overrides)
    return RunSpec(**fields)


def test_the_v2_primary_cell_is_flagged_as_a_criteria_conflict(config, v2_pricing):
    """One triage is ~20% of worker spend under linear review: unpassable."""
    block = arithmetic(config, v2_pricing, v2_primary("linear"), ceiling=0.10)
    assert block["criteria_conflict"]
    assert block["one_detection_triage_share_of_worker_cost"] == pytest.approx(0.203, abs=0.005)


def test_fixed_review_at_scale_has_no_conflict(config, v2_pricing):
    block = arithmetic(config, v2_pricing, v2_primary("fixed"), ceiling=0.10)
    assert not block["criteria_conflict"]


def test_conflict_short_circuits_before_any_simulation(config, v2_pricing):
    report = evaluate(config, v2_pricing, v2_primary("linear"), DECISION, (PILOT_SEED_FLOOR,))
    assert report["verdict"] == "CRITERIA_CONFLICT"
    assert "pilot" not in report


def test_arithmetic_audit_rate_matches_the_simulation(config, v2_pricing):
    """The no-simulation estimate agrees with what a run actually affords."""
    predicted = arithmetic(config, v2_pricing, v2_primary("fixed"), 0.10)
    record = run_simulation(config, v2_primary("fixed", seed=PILOT_SEED_FLOOR), v2_pricing)
    measured = record.mean_allowance_per_tick_dollars / record.audit_cost_dollars
    assert predicted["affordable_audits_per_tick"] == pytest.approx(measured, rel=0.05)
    assert predicted["audit_cost_in_worker_messages"] == pytest.approx(18.5, abs=0.1)


def test_pilot_seeds_must_be_disjoint_from_study_seeds(config, v2_pricing):
    with pytest.raises(ValueError, match=">="):
        run_pilot(config, v2_pricing, v2_primary("fixed"), (0, 1))
    with pytest.raises(ValueError, match="overlap"):
        run_pilot(config, v2_pricing, v2_primary("fixed"), (PILOT_SEED_FLOOR,),
                  forbidden_seeds=frozenset({PILOT_SEED_FLOOR}))


def test_baseline_spec_strips_everything_that_buys_audits():
    base = baseline_spec(v2_primary("fixed", followup_audits=4))
    assert (base.arm, base.reserve_fraction, base.followup_audits) == (Arm.TIER0_ONLY, 0.0, 0)
    assert base.worker_model == "gpt-4.1-mini" and base.budget_pct == 0.05


def test_assurance_is_zero_with_no_signal_and_high_with_a_clear_one():
    cheap = [0.03] * 40
    assert assurance(0, 0, 40, cheap, 200, 0.10, 0.10, reps=300) == 0.0
    assert assurance(36, 0, 40, cheap, 50, 0.10, 0.10, reps=300) > 0.95


def test_assurance_respects_the_cost_ceiling():
    expensive = [0.12] * 40
    assert assurance(36, 0, 40, expensive, 100, 0.10, 0.10, reps=300) == 0.0


def test_assurance_grows_with_seeds_for_a_modest_effect():
    overheads = [0.05] * 40
    small = assurance(10, 0, 40, overheads, 30, 0.10, 0.10, reps=500, seed=1)
    large = assurance(10, 0, 40, overheads, 300, 0.10, 0.10, reps=500, seed=1)
    assert large > small


def test_pilot_uncertainty_is_carried_into_assurance():
    """A thin pilot should not be as confident as its point estimate."""
    overheads = [0.05] * 10
    with_uncertainty = assurance(4, 0, 10, overheads, 100, 0.10, 0.10, reps=800, seed=2)
    point = assurance(4, 0, 10, overheads, 100, 0.10, 0.10, reps=800, seed=2,
                      propagate_pilot_uncertainty=False)
    assert with_uncertainty < point


def test_a_small_pilot_report_is_complete(config, v2_pricing):
    spec = v2_primary("fixed", followup_audits=4)
    seeds = tuple(range(PILOT_SEED_FLOOR, PILOT_SEED_FLOOR + 2))
    report = evaluate(config, v2_pricing, spec, DECISION, seeds, (30, 100), processes=1, reps=50)
    assert report["verdict"] in ("FREEZABLE", "NOT_FREEZABLE")
    assert report["pilot"]["n"] == 2
    assert [p["n_seeds"] for p in report["curve"]] == [30, 100]
    assert report["evidence_kind"].startswith("pre-freeze pilot")
