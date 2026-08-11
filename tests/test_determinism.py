"""Same seed, byte-identical results. Twice."""

from __future__ import annotations

import json
import math

import pytest

from harpy.metrics import result_document
from harpy.simulation import RunSpec, rescale, run_simulation
from harpy.sweep import run_single
from harpy.types import (
    PER_AGENT_OVERHEAD_COMPONENTS,
    REVIEW_COMPONENTS,
    Arm,
    Severity,
)


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


@pytest.mark.parametrize("cap", [0.01, 0.05, 0.25, 1.0, math.inf])
@pytest.mark.parametrize("fleet_scale", [1.0, 1000.0])
def test_the_new_rng_draws_and_cost_axes_stay_byte_identical(
    config, test_pricing, cap, fleet_scale
):
    """The escalation stream and both new axes leave determinism intact."""
    spec = RunSpec(
        seed=3,
        severity=Severity.OVERT,
        arm=Arm.HYBRID,
        budget_pct=0.1,
        lineage_fidelity=0.5,
        reserve_fraction=0.5,
        fleet_scale=fleet_scale,
        max_alerts_per_agent_hour=cap,
    )
    assert document_bytes(config, spec, test_pricing) == document_bytes(config, spec, test_pricing)


def test_escalation_draws_do_not_depend_on_the_sampler_stream(config, test_pricing):
    """Review escalation has its own stream, so arms are still compared fairly."""

    def escalations(arm: Arm) -> int:
        spec = RunSpec(4, Severity.OVERT, arm, 0.1, 1.0, 0.5, max_alerts_per_agent_hour=math.inf)
        return run_simulation(config, spec, test_pricing).escalated_reviews

    # Not an equality claim about counts — arms open different numbers of
    # reviews. The claim is that repeating an arm reproduces its own draws.
    for arm in Arm:
        assert escalations(arm) == escalations(arm)


# -- fleet_scale is cost arithmetic, not a second world ----------------------


@pytest.mark.parametrize("fleet_scale", [1.0, 10.0, 100.0, 1000.0])
def test_fleet_scale_changes_no_dynamics_at_all(config, test_pricing, fleet_scale):
    """Everything except the money must be identical across scales.

    This is what licenses the sweep to simulate each cell once and re-cost it,
    and it is also the substantive claim: the fleet scale is an accounting
    parameter, not a different experiment.
    """
    def record_at(scale: float):
        spec = RunSpec(6, Severity.OVERT, Arm.HYBRID, 0.1, 1.0, 0.5, fleet_scale=scale)
        return run_simulation(config, spec, test_pricing)

    base, scaled = record_at(1.0), record_at(fleet_scale)
    for attribute in (
        "detected_tick",
        "alerts_fired",
        "suppressed_alerts",
        "detections_lost_to_alert_cap",
        "effective_isolation_threshold",
        "audits_run",
        "total_claims",
        "agent_active_ticks",
        "reviews_opened",
        "escalated_reviews",
    ):
        assert getattr(base, attribute) == getattr(scaled, attribute)
    assert len(base.isolations) == len(scaled.isolations)


@pytest.mark.parametrize("fleet_scale", [1.0, 10.0, 100.0, 1000.0])
def test_rescaling_a_run_equals_simulating_it_at_that_scale(
    config, test_pricing, fleet_scale
):
    """Byte-identical, so the sweep's one-run-per-cell shortcut is not a shortcut."""
    def spec_at(scale: float) -> RunSpec:
        return RunSpec(7, Severity.MODERATE, Arm.DIRECTED, 0.05, 0.75, fleet_scale=scale)

    simulated = result_document(run_simulation(config, spec_at(fleet_scale), test_pricing))
    rescaled = result_document(
        rescale(run_simulation(config, spec_at(1.0), test_pricing), fleet_scale)
    )
    assert json.dumps(simulated, sort_keys=True) == json.dumps(rescaled, sort_keys=True)


@pytest.mark.parametrize("fleet_scale", [10.0, 1000.0])
def test_fleet_scale_multiplies_the_four_per_agent_components_end_to_end(
    config, test_pricing, fleet_scale
):
    def ledger_at(scale: float) -> dict:
        spec = RunSpec(6, Severity.OVERT, Arm.HYBRID, 0.1, 1.0, 0.5, fleet_scale=scale)
        return run_simulation(config, spec, test_pricing).ledger.as_dict()

    base, scaled = ledger_at(1.0), ledger_at(fleet_scale)
    assert scaled["worker_dollars"] == pytest.approx(base["worker_dollars"] * fleet_scale)
    for name in PER_AGENT_OVERHEAD_COMPONENTS:
        assert scaled[name] == pytest.approx(base[name] * fleet_scale)
    for name in REVIEW_COMPONENTS:
        assert scaled[name] == pytest.approx(base[name])
    assert base[REVIEW_COMPONENTS[0]] > 0.0, "a run with no reviews proves nothing here"


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
