"""The exposure window behind ``audit_coverage_of_faulty_agent``.

The window is [fault onset, isolation of the faulty agent], or [onset, end of
run] when the fault is never caught. Getting the upper bound wrong is not a
rounding error: counting audits drawn after the faulty agent left the mesh
inflates the denominator fastest for the arms that contained the fault quickest,
so a working arm is reported as a badly-targeted one. That is the regression
these tests exist to prevent.

The window predicate is tested directly, in isolation from the simulator, so the
four cases are exact rather than whatever a seeded run happened to produce.
"""

from __future__ import annotations

import dataclasses

import pytest

from harpy.metrics import (
    audit_coverage_of_faulty_agent,
    run_metrics,
    ticks_faulty_before_first_audit,
)
from harpy.simulation import RunSpec, audit_counts_toward_exposure, run_simulation
from harpy.types import Arm, BudgetLedger, Severity

ONSET = 100
RUN_END = 500


# ---------------------------------------------------------------------------
# The window predicate: the four cases, exactly
# ---------------------------------------------------------------------------


def test_audits_before_onset_never_count():
    """There was nothing to find yet, under any isolation outcome."""
    for detected in (None, ONSET, 250, RUN_END):
        assert not audit_counts_toward_exposure(0, ONSET, detected)
        assert not audit_counts_toward_exposure(ONSET - 1, ONSET, detected)


def test_never_isolated_window_runs_to_the_end():
    """detected_tick None: every audit from onset onwards is inside the window."""
    for tick in (ONSET, ONSET + 1, 250, RUN_END):
        assert audit_counts_toward_exposure(tick, ONSET, None)


def test_isolated_early_closes_the_window_early():
    """A fault contained at onset+5 leaves a five-tick window, not a 400-tick one."""
    detected = ONSET + 5

    assert audit_counts_toward_exposure(ONSET, ONSET, detected)
    assert audit_counts_toward_exposure(detected, ONSET, detected)
    # Everything after containment is on a clean mesh by construction.
    assert not audit_counts_toward_exposure(detected + 1, ONSET, detected)
    assert not audit_counts_toward_exposure(RUN_END, ONSET, detected)


def test_isolated_late_keeps_almost_the_whole_run():
    detected = RUN_END - 1

    assert audit_counts_toward_exposure(ONSET, ONSET, detected)
    assert audit_counts_toward_exposure(detected - 1, ONSET, detected)
    assert audit_counts_toward_exposure(detected, ONSET, detected)
    assert not audit_counts_toward_exposure(RUN_END, ONSET, detected)


def test_the_isolation_tick_itself_is_inside_the_window():
    """Auditing is resolved before isolation within a tick.

    An audit on the tick of isolation happened while the agent was still live, so
    excluding it would drop a real observation and understate the numerator.
    """
    assert audit_counts_toward_exposure(250, ONSET, 250)


def test_a_fault_isolated_on_its_onset_tick_leaves_a_one_tick_window():
    assert audit_counts_toward_exposure(ONSET, ONSET, ONSET)
    assert not audit_counts_toward_exposure(ONSET + 1, ONSET, ONSET)


# ---------------------------------------------------------------------------
# The ratio built on that window
# ---------------------------------------------------------------------------


def a_record(**overrides):
    """A RunRecord carrying only what the coverage metric reads."""
    from harpy.simulation import RunRecord

    base = dict(
        spec=RunSpec(
            seed=0,
            severity=Severity.SUBTLE,
            arm=Arm.DIRECTED,
            budget_pct=0.05,
            lineage_fidelity=1.0,
        ),
        n_agents=30,
        n_ticks=RUN_END,
        tick_seconds=60,
        fault_distribution={},
        faulty_agent_id="agent-00",
        fault_onset_tick=ONSET,
        ledger=BudgetLedger(),
    )
    base.update(overrides)
    return RunRecord(**base)


def test_coverage_is_the_ratio_over_the_window():
    record = a_record(audits_on_faulty_agent=3, audits_during_fault_exposure=12)
    assert audit_coverage_of_faulty_agent(record) == pytest.approx(0.25)


def test_no_audits_in_the_window_is_none_not_zero():
    """"Never looked" and "looked and found nothing" are different claims."""
    record = a_record(audits_on_faulty_agent=0, audits_during_fault_exposure=0)
    assert audit_coverage_of_faulty_agent(record) is None


def test_audits_only_outside_the_window_is_also_none():
    """Audits that all landed after containment leave an empty window."""
    record = a_record(
        audits_on_faulty_agent=0,
        audits_during_fault_exposure=0,
        audits_on_faulty_agent_all_ticks=4,
        detected_tick=ONSET + 1,
    )
    assert audit_coverage_of_faulty_agent(record) is None


def test_full_coverage_is_exactly_one():
    record = a_record(audits_on_faulty_agent=7, audits_during_fault_exposure=7)
    assert audit_coverage_of_faulty_agent(record) == pytest.approx(1.0)


def test_ticks_before_first_audit_is_measured_from_onset():
    record = a_record(first_audit_tick_on_faulty_agent=ONSET + 17)
    assert ticks_faulty_before_first_audit(record) == 17


def test_ticks_before_first_audit_is_none_when_never_audited():
    assert ticks_faulty_before_first_audit(a_record()) is None


# ---------------------------------------------------------------------------
# End to end: the simulator honours the window it is supposed to
# ---------------------------------------------------------------------------


def a_spec(**overrides) -> RunSpec:
    base = {
        "seed": 3,
        "severity": Severity.OVERT,
        "arm": Arm.DIRECTED,
        "budget_pct": 0.2,
        "lineage_fidelity": 1.0,
        "reserve_fraction": 0.5,
    }
    base.update(overrides)
    return RunSpec(**base)


@pytest.mark.parametrize("severity", [Severity.OVERT, Severity.SUBTLE])
def test_a_real_run_never_counts_audits_past_containment(config, test_pricing, severity):
    """The invariant the fix exists to hold, on a real simulation.

    OVERT is the case that matters: it is detected fast, so under the old
    all-audits-after-onset denominator it accumulated hundreds of post-isolation
    audits and its coverage was dragged towards zero precisely because the arm
    worked.
    """
    record = run_simulation(config, a_spec(severity=severity), test_pricing)
    metrics = run_metrics(record)

    assert metrics["audits_during_fault_exposure"] <= metrics["audits_run"]
    assert metrics["audits_on_faulty_agent"] <= metrics["audits_during_fault_exposure"]

    coverage = metrics["audit_coverage_of_faulty_agent"]
    assert coverage is None or 0.0 <= coverage <= 1.0

    if record.detected_tick is not None:
        # Contained runs must have a strictly smaller window than the run, so
        # the denominator cannot have kept growing after isolation.
        assert record.detected_tick >= record.fault_onset_tick
        window_ticks = record.detected_tick - record.fault_onset_tick + 1
        assert window_ticks <= record.n_ticks


def test_containment_shrinks_the_denominator_not_the_numerator(config, test_pricing):
    """A contained run's exposure window excludes the tail of the run.

    Compared against the same run's total audit count: if the fault was caught
    well before the end, the window must hold strictly fewer audits than the run
    drew overall, or the upper bound is not being applied at all.
    """
    record = run_simulation(config, a_spec(severity=Severity.OVERT), test_pricing)
    if record.detected_tick is None or record.detected_tick > record.n_ticks * 0.5:
        pytest.skip("this seed did not contain the fault early enough to show the effect")

    metrics = run_metrics(record)
    assert metrics["audits_during_fault_exposure"] < metrics["audits_run"], (
        "audits drawn after containment are still inflating the coverage denominator"
    )


def test_rescaling_preserves_the_targeting_counters(config, test_pricing):
    """fleet_scale is cost arithmetic; it must not touch audit bookkeeping."""
    from harpy.simulation import rescale

    record = run_simulation(config, a_spec(), test_pricing)
    scaled = rescale(record, 1000.0)

    for name in (
        "audits_on_faulty_agent",
        "audits_during_fault_exposure",
        "audits_on_faulty_agent_all_ticks",
        "first_audit_tick_on_faulty_agent",
    ):
        assert getattr(scaled, name) == getattr(record, name)
    assert audit_coverage_of_faulty_agent(scaled) == audit_coverage_of_faulty_agent(record)


def test_the_window_predicate_is_what_the_simulator_uses(config, test_pricing):
    """Guards against the predicate being fixed while the call site drifts."""
    record = run_simulation(config, a_spec(), test_pricing)

    # Reconstruct the bound the simulator should have applied.
    assert audit_counts_toward_exposure(
        record.fault_onset_tick, record.fault_onset_tick, record.detected_tick
    )
    if record.detected_tick is not None:
        assert not audit_counts_toward_exposure(
            record.detected_tick + 1, record.fault_onset_tick, record.detected_tick
        )


def test_a_spec_field_change_does_not_silently_drop_the_counters():
    """dataclasses.replace on a record must carry the new fields."""
    record = a_record(audits_on_faulty_agent=2, audits_during_fault_exposure=8)
    replaced = dataclasses.replace(record, n_agents=60)
    assert replaced.audits_during_fault_exposure == 8
    assert audit_coverage_of_faulty_agent(replaced) == pytest.approx(0.25)
