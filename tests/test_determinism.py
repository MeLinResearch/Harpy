"""Same seed, byte-identical results. Twice."""

from __future__ import annotations

import json

import pytest

from harpy.metrics import result_document
from harpy.simulation import RunSpec, run_simulation
from harpy.sweep import run_single
from harpy.types import Arm, Severity


def document_bytes(config, spec, pricing) -> str:
    document = result_document(run_simulation(config, spec, pricing))
    return json.dumps(document, indent=2, sort_keys=True)


@pytest.mark.parametrize("arm", list(Arm))
def test_same_seed_gives_byte_identical_results(config, test_pricing, arm):
    spec = RunSpec(
        seed=3,
        severity=Severity.MODERATE,
        arm=arm,
        budget_pct=0.1,
        lineage_fidelity=0.5,
        reserve_fraction=0.5,
    )
    assert document_bytes(config, spec, test_pricing) == document_bytes(config, spec, test_pricing)


def test_written_json_files_are_byte_identical(tmp_path, config, test_pricing):
    spec = RunSpec(
        seed=0,
        severity=Severity.OVERT,
        arm=Arm.HYBRID,
        budget_pct=0.05,
        lineage_fidelity=1.0,
        reserve_fraction=0.25,
    )
    first = run_single(config, spec, test_pricing, tmp_path / "a")
    second = run_single(config, spec, test_pricing, tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()


def test_different_seeds_give_different_worlds(config, test_pricing):
    def onset(seed: int):
        spec = RunSpec(seed, Severity.SUBTLE, Arm.RANDOM, 0.05, 1.0)
        record = run_simulation(config, spec, test_pricing)
        return record.faulty_agent_id, record.fault_onset_tick

    assert len({onset(seed) for seed in range(8)}) > 1


def test_the_arm_does_not_perturb_the_world_it_is_measured_on(config, test_pricing):
    """Arms must be compared on the same mesh, so world RNG is stream-isolated."""
    worlds = set()
    for arm in Arm:
        spec = RunSpec(5, Severity.SUBTLE, arm, 0.05, 1.0)
        record = run_simulation(config, spec, test_pricing)
        worlds.add((record.faulty_agent_id, record.fault_onset_tick))
    assert len(worlds) == 1


def test_results_json_carries_no_wall_clock_or_environment_fields(config, test_pricing):
    spec = RunSpec(1, Severity.MODERATE, Arm.DIRECTED, 0.05, 0.75)
    document = result_document(run_simulation(config, spec, test_pricing))
    text = json.dumps(document).lower()
    for forbidden in ("timestamp", "datetime", "hostname", "elapsed", "wall_clock"):
        assert forbidden not in text
