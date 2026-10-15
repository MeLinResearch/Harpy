"""v3 study plumbing: follow-up depth and EXPOSURE reach the frozen-study path."""

from __future__ import annotations

import json

import pytest
import yaml

from harpy.study import CELL_KEYS, run_study


def write_study(tmp_path, config, test_pricing, **extra):
    config["simulation"]["n_ticks"] = 12
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    pricing_path = tmp_path / "pricing.yaml"
    pricing_path.write_text(yaml.safe_dump(test_pricing.as_dict()))
    study = {
        "schema": "harpy/study/2",
        "study_id": "v3-fixture",
        "simulation_config": str(config_path),
        "pricing": str(pricing_path),
        "grid": {
            "seeds": [0, 1],
            "arms": ["TIER0_ONLY", "HYBRID", "EXPOSURE"],
            "severities": ["SUBTLE"],
            "budget_pcts": [0.05],
            "lineage_fidelities": [1.0],
            "reserve_fractions": [0.5],
            "fleet_scales": [1.0],
            "review_scalings": ["fixed"],
            "alert_caps": [0.05],
            "worker_models": ["default"],
            "followup_audits": [0, 3],
        },
        "primary_cell": {
            "arm": "EXPOSURE",
            "severity": "SUBTLE",
            "budget_pct": 0.05,
            "lineage_fidelity": 1.0,
            "reserve_fraction": 0.0,
            "fleet_scale": 1.0,
            "review_scaling": "fixed",
            "max_alerts_per_agent_hour": 0.05,
            "worker_model": "default",
            "followup_audits": 3,
        },
        "decision": {
            "minimum_seeds": 2,
            "minimum_incremental_detection": 0.1,
            "maximum_overhead": 0.1,
            "bootstrap_samples": 100,
        },
        **extra,
    }
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(study))
    return path


def test_followup_depth_keys_a_study_cell():
    assert "followup_audits" in CELL_KEYS


def test_a_draft_specification_cannot_produce_evidence(tmp_path, config, test_pricing):
    path = write_study(tmp_path, config, test_pricing, status="draft")
    with pytest.raises(ValueError, match="only a frozen specification"):
        run_study(path, tmp_path / "out", processes=1)
    assert not (tmp_path / "out").exists()


def test_a_v3_study_evaluates_exposure_and_followup_cells(tmp_path, config, test_pricing):
    path = write_study(tmp_path, config, test_pricing, status="frozen")
    report = json.loads(run_study(path, tmp_path / "out", processes=1).read_text())
    # 2 seeds x (TIER0_ONLY + HYBRID at k=0,3 + EXPOSURE at k=0,3).
    assert report["n_run_records"] == 2 * 5
    assert report["primary"]["arm"] == "EXPOSURE"
    assert report["primary"]["followup_audits"] == 3
    cells = {(c["arm"], c["followup_audits"]) for c in report["exploratory_cells"]}
    assert cells == {("HYBRID", 0), ("HYBRID", 3), ("EXPOSURE", 0), ("EXPOSURE", 3)}
