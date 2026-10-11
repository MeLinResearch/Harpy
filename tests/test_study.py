from __future__ import annotations

import copy
import json

import pytest
import yaml

from harpy.metrics import BASELINE_MATCH_KEYS, min_fleet_dollars_per_hour_for_viability
from harpy.provenance import verify_manifest
from harpy.simulation import RunSpec, rescale, run_simulation
from harpy.statistics import bootstrap_mean_interval, difference_interval, wilson_interval
from harpy.study import evaluate_cell, run_study
from harpy.sweep import SweepGrid
from harpy.types import Arm, BudgetLedger, Severity


def test_linear_review_cost_has_no_artificial_fleet_discount():
    base = BudgetLedger(
        worker_dollars=100,
        sentinel_dollars=4,
        triage_dollars=3,
        investigation_dollars=5,
        review_scaling="linear",
    )
    scaled = copy.deepcopy(base)
    scaled.fleet_scale = 100
    assert scaled.overhead_pct() == pytest.approx(base.overhead_pct())
    assert scaled.fleet_component("triage_dollars") == 300
    assert scaled.review_cost_share_of_overhead() == pytest.approx(8 / 12)
    base.review_scaling = scaled.review_scaling = "fixed"
    assert scaled.overhead_pct() < base.overhead_pct()


def test_review_policy_is_recosting_and_cannot_change_detection(config, test_pricing):
    spec = RunSpec(6, Severity.OVERT, Arm.HYBRID, 0.1, 1, review_scaling="linear")
    record = run_simulation(config, spec, test_pricing)
    scaled = rescale(record, 100)
    assert record.detected_tick == scaled.detected_tick
    assert record.ledger.overhead_pct() == pytest.approx(scaled.ledger.overhead_pct())
    expected = 0.0 if record.ledger.overhead_pct() < 0.1 else None
    assert min_fleet_dollars_per_hour_for_viability(scaled) == expected


def test_zero_successes_still_have_sampling_uncertainty():
    assert wilson_interval(0, 30)[1] > 0.10
    interval = difference_interval([0] * 30, [0] * 30)
    assert interval[0] < 0 < interval[1]
    assert interval[1] > 0.10


def test_missing_baseline_cannot_produce_a_pass():
    row = {k: None for k in BASELINE_MATCH_KEYS}
    row.update(seed=0, arm="HYBRID", reserve_fraction=0.5)
    assert evaluate_cell([row], {}, {})["decision"] == "INCOMPLETE"


def test_policy_axes_are_distinct_run_ids():
    grid = SweepGrid(
        seeds=(0,),
        arms=(Arm.NONE,),
        severities=(Severity.SUBTLE,),
        budget_pcts=(0.05,),
        lineage_fidelities=(1,),
        reserve_fractions=(0.5,),
        fleet_scales=(1, 100),
        alert_caps=(0.05,),
        worker_models=("default",),
        review_scalings=("fixed", "linear"),
    )
    assert len(grid.base_specs()) == 1
    assert len({s.run_id() for s in grid.specs()}) == 4
    with pytest.raises(ValueError):
        SweepGrid(review_scalings=("unknown",))


@pytest.mark.parametrize(
    "detected, baseline_detected, overhead, expected",
    [
        (True, False, 0.05, "PASS"),
        (True, False, 0.20, "FAIL"),
        (False, True, 0.05, "FAIL"),
        (False, False, 0.05, "INCONCLUSIVE"),
    ],
)
def test_preregistered_decision_uses_uncertainty(detected, baseline_detected, overhead, expected):
    candidates, baselines = [], {}
    for seed in range(30):
        row = {k: None for k in BASELINE_MATCH_KEYS}
        row.update(
            seed=seed,
            arm="HYBRID",
            reserve_fraction=0.5,
            detection_rate=int(detected),
            overhead_pct=overhead,
            false_isolations=0,
            alerts_per_agent_hour=0,
        )
        candidates.append(row)
        baselines[tuple(row[k] for k in BASELINE_MATCH_KEYS)] = row | {
            "arm": "TIER0_ONLY",
            "detection_rate": int(baseline_detected),
        }
    decision = {
        "minimum_seeds": 30,
        "minimum_incremental_detection": 0.10,
        "maximum_overhead": 0.10,
        "bootstrap_samples": 200,
    }
    assert evaluate_cell(candidates, baselines, decision)["decision"] == expected


def test_bootstrap_is_reproducible_and_rejects_nonfinite_costs():
    costs = [0.02, 0.04, 0.12, 0.30]
    assert bootstrap_mean_interval(costs, 42) == bootstrap_mean_interval(costs, 42)
    with pytest.raises(ValueError, match="finite"):
        bootstrap_mean_interval([float("nan")])


def test_small_study_persists_design_metrics_and_verifiable_evidence(
    tmp_path, config, test_pricing
):
    config["simulation"]["n_ticks"] = 12
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    pricing_path = tmp_path / "pricing.yaml"
    pricing_path.write_text(yaml.safe_dump(test_pricing.as_dict()))
    primary = {
        "arm": "HYBRID",
        "severity": "SUBTLE",
        "budget_pct": 0.05,
        "lineage_fidelity": 1.0,
        "reserve_fraction": 0.5,
        "fleet_scale": 1.0,
        "review_scaling": "linear",
        "max_alerts_per_agent_hour": 0.05,
        "worker_model": "default",
    }
    study = {
        "schema": "harpy/study/2",
        "study_id": "integration-fixture",
        "simulation_config": str(config_path),
        "pricing": str(pricing_path),
        "grid": {
            "seeds": [0, 1],
            "arms": ["TIER0_ONLY", "HYBRID"],
            "severities": ["SUBTLE"],
            "budget_pcts": [0.05],
            "lineage_fidelities": [1.0],
            "reserve_fractions": [0.5],
            "fleet_scales": [1.0],
            "review_scalings": ["linear"],
            "alert_caps": [0.05],
            "worker_models": ["default"],
        },
        "primary_cell": primary,
        "decision": {
            "minimum_seeds": 2,
            "minimum_incremental_detection": 0.1,
            "maximum_overhead": 0.1,
            "bootstrap_samples": 100,
        },
    }
    specification = tmp_path / "study.yaml"
    specification.write_text(yaml.safe_dump(study))
    output = tmp_path / "output"
    report = json.loads(run_study(specification, output, processes=1).read_text())
    assert report["n_run_records"] == 4
    assert report["primary"]["n_seeds"] == 2
    assert (output / "preregistration.json").is_file()
    assert (output / "seed-metrics.json.gz").is_file()
    assert verify_manifest(output / "manifest.json") == []
