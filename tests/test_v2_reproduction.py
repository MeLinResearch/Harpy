"""The published v2 evidence still reproduces with every v3 option off.

A sample of v2 per-seed records is re-simulated from the frozen config and the
study pricing, and every metric the packet recorded must match exactly. Only
two fields are exempt: ``experiment_fingerprint`` (it hashes executing source,
which v3 changed) and ``incremental_detection_over_tier0_only`` (a cross-run
post-pass, not a per-run metric).
"""

from __future__ import annotations

import gzip
import json

import pytest

from harpy.cli import load_config
from harpy.ledger import load_pricing
from harpy.metrics import run_metrics
from harpy.simulation import RunSpec, rescale, run_simulation
from harpy.types import Arm, Severity

from conftest import REPO_ROOT

EXEMPT = {"experiment_fingerprint", "incremental_detection_over_tier0_only"}
SAMPLE = [
    # (seed, arm, severity, budget, fidelity)
    (0, "HYBRID", "SUBTLE", 0.05, 1.0),
    (3, "TIER0_ONLY", "OVERT", 0.10, 0.5),
    (7, "RANDOM", "MODERATE", 0.05, 0.5),
    (12, "DIRECTED", "OVERT", 0.10, 1.0),
    (21, "HYBRID", "MODERATE", 0.10, 0.5),
]


@pytest.fixture(scope="module")
def packet():
    path = REPO_ROOT / "evidence" / "study-v2" / "seed-metrics.json.gz"
    with gzip.open(path) as stream:
        return {row["run_id"]: row for row in json.load(stream)}


@pytest.mark.parametrize("seed,arm,severity,budget,fidelity", SAMPLE)
def test_v2_records_reproduce_with_v3_options_off(packet, seed, arm, severity, budget, fidelity):
    config = load_config(REPO_ROOT / "configs" / "default.yaml")
    pricing = load_pricing(REPO_ROOT / "configs" / "pricing.study.yaml")
    reserve = 0.5 if arm == "HYBRID" else 0.0
    spec = RunSpec(seed, Severity(severity), Arm(arm), budget, fidelity, reserve, 1.0, 0.05,
                   worker_model="gpt-4.1-mini", review_scaling="fixed")
    record = run_simulation(config, spec, pricing)
    checked = 0
    for scaling in ("fixed", "linear"):
        for scale in (1.0, 100.0):
            row = run_metrics(rescale(record, scale, scaling))
            ref = packet[row["run_id"]]
            for key, expected in ref.items():
                if key in EXEMPT:
                    continue
                assert row[key] == expected, (row["run_id"], key, row[key], expected)
                checked += 1
    assert checked > 200
