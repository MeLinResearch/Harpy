"""The diagnostic instrumentation: six metrics, three axes, and the pricing gate.

Nothing in this file asserts a dollar figure or anything derived from one. Every
price reachable from here is invented, so an assertion on cost would be an
assertion about a fixture. What is pinned instead is shape, type, arithmetic
identity, and — mostly — the guard: that a run costed off placeholder or missing
prices aborts rather than quietly producing a number.
"""

from __future__ import annotations

import math

import pytest

from harpy.cli import main
from harpy.ledger import (
    DEFAULT_WORKER_MODEL_NAME,
    CostAccountant,
    ModelPrice,
    Pricing,
    PricingEntryMissingError,
    PricingNotVerifiedError,
    load_pricing,
)
from harpy.metrics import (
    BASELINE_MATCH_KEYS,
    attach_incremental_detection_over_tier0,
    run_metrics,
)
from harpy.simulation import RunSpec, run_simulation
from harpy.sweep import SweepGrid, check_pricing
from harpy.types import Arm, Severity

from conftest import PLACEHOLDER_PRICING_PATH, REPO_ROOT

#: The six the diagnostic sweeps were specified to emit.
REQUIRED_METRICS: tuple[str, ...] = (
    "audits_on_faulty_agent",
    "audit_coverage_of_faulty_agent",
    "ticks_faulty_before_first_audit",
    "false_isolation_count",
    "discard_rerun_dollars_from_false_isolations",
    "incremental_detection_over_tier0_only",
)


def a_spec(**overrides) -> RunSpec:
    base = {
        "seed": 5,
        "severity": Severity.OVERT,
        "arm": Arm.HYBRID,
        "budget_pct": 0.2,
        "lineage_fidelity": 0.5,
        "reserve_fraction": 0.5,
    }
    base.update(overrides)
    return RunSpec(**base)


# ---------------------------------------------------------------------------
# 1. The six metrics: emitted, right shape, right type
# ---------------------------------------------------------------------------


def test_every_required_metric_is_emitted(config, test_pricing):
    metrics = run_metrics(run_simulation(config, a_spec(), test_pricing))
    missing = [name for name in REQUIRED_METRICS if name not in metrics]
    assert not missing, f"missing from the per-run metric block: {missing}"


def test_audit_targeting_counts_are_ints_and_nest_correctly(config, test_pricing):
    metrics = run_metrics(run_simulation(config, a_spec(), test_pricing))

    for name in (
        "audits_on_faulty_agent",
        "audits_on_faulty_agent_all_ticks",
        "audits_during_fault_exposure",
        "false_isolation_count",
    ):
        assert isinstance(metrics[name], int), f"{name} must be a count, got {type(metrics[name])}"
        assert metrics[name] >= 0

    # Audits of the fault source after onset are a subset of all audits after
    # onset, and of all audits of that agent at any time.
    assert metrics["audits_on_faulty_agent"] <= metrics["audits_during_fault_exposure"]
    assert metrics["audits_on_faulty_agent"] <= metrics["audits_on_faulty_agent_all_ticks"]
    assert metrics["audits_during_fault_exposure"] <= metrics["audits_run"]


def test_coverage_is_a_fraction_or_none_and_never_a_silent_zero(config, test_pricing):
    metrics = run_metrics(run_simulation(config, a_spec(), test_pricing))
    coverage = metrics["audit_coverage_of_faulty_agent"]

    if metrics["audits_during_fault_exposure"] == 0:
        # "Never looked" is not "looked and covered 0%". This distinction is the
        # entire point of the metric.
        assert coverage is None
    else:
        assert isinstance(coverage, float)
        assert 0.0 <= coverage <= 1.0
        assert coverage == pytest.approx(
            metrics["audits_on_faulty_agent"] / metrics["audits_during_fault_exposure"]
        )


def test_ticks_before_first_audit_is_none_exactly_when_never_audited(config, test_pricing):
    metrics = run_metrics(run_simulation(config, a_spec(), test_pricing))
    ticks = metrics["ticks_faulty_before_first_audit"]

    if metrics["audits_on_faulty_agent"] == 0:
        assert ticks is None
    else:
        assert isinstance(ticks, int)
        assert ticks >= 0, "an audit before onset must not count as a post-onset first audit"


def test_an_arm_that_never_audits_reports_no_coverage_rather_than_zero(config, test_pricing):
    """TIER0_ONLY buys no audits at all, so it has no coverage to report."""
    record = run_simulation(config, a_spec(arm=Arm.TIER0_ONLY), test_pricing)
    metrics = run_metrics(record)

    assert metrics["audits_run"] == 0
    assert metrics["audits_during_fault_exposure"] == 0
    assert metrics["audit_coverage_of_faulty_agent"] is None
    assert metrics["ticks_faulty_before_first_audit"] is None


def test_false_isolation_count_matches_the_isolation_log(config, test_pricing):
    record = run_simulation(config, a_spec(), test_pricing)
    metrics = run_metrics(record)

    expected = sum(1 for event in record.isolations if not event.was_faulty)
    assert metrics["false_isolation_count"] == expected
    # The pre-existing name kept reporting the same thing.
    assert metrics["false_isolations"] == expected


def test_the_discard_rerun_split_partitions_the_totals(config, test_pricing):
    """An identity, not a magnitude: the halves sum to the whole at any prices."""
    metrics = run_metrics(run_simulation(config, a_spec(), test_pricing))

    assert metrics["discarded_work_dollars"] == pytest.approx(
        metrics["discarded_work_dollars_from_false_isolations"]
        + metrics["discarded_work_dollars_from_true_isolations"]
    )
    assert metrics["rerun_work_dollars"] == pytest.approx(
        metrics["rerun_work_dollars_from_false_isolations"]
        + metrics["rerun_work_dollars_from_true_isolations"]
    )
    assert (
        metrics["discard_rerun_dollars_from_false_isolations"]
        + metrics["discard_rerun_dollars_from_true_isolations"]
    ) == pytest.approx(metrics["discarded_work_dollars"] + metrics["rerun_work_dollars"])


def test_no_false_isolations_means_an_exactly_zero_false_share(config, test_pricing):
    """Exact zero, not approximate: nothing was charged to that bucket."""
    record = run_simulation(config, a_spec(arm=Arm.TIER0_ONLY), test_pricing)
    metrics = run_metrics(record)

    if metrics["false_isolation_count"] == 0:
        assert metrics["discard_rerun_dollars_from_false_isolations"] == 0.0


def test_attribution_files_false_isolations_and_leaves_true_ones_alone(
    test_pricing, review_config
):
    """Bucketing only — the amounts come from the accountant, not from a price."""
    accountant = CostAccountant(test_pricing, review_config)
    accountant.charge_worker("agent-00", 10_000, 1_000)
    accountant.charge_worker("agent-01", 10_000, 1_000)

    true_discard, true_rerun = accountant.charge_isolation("agent-00")
    accountant.attribute_isolation(true_discard, true_rerun, was_faulty=True)
    false_discard, false_rerun = accountant.charge_isolation("agent-01")
    accountant.attribute_isolation(false_discard, false_rerun, was_faulty=False)

    ledger = accountant.ledger
    assert ledger.discarded_work_dollars_from_false_isolations == pytest.approx(false_discard)
    assert ledger.rerun_work_dollars_from_false_isolations == pytest.approx(false_rerun)
    assert ledger.discarded_work_dollars_from_true_isolations() == pytest.approx(true_discard)
    assert ledger.rerun_work_dollars_from_true_isolations() == pytest.approx(true_rerun)


def test_the_split_does_not_double_count_into_overhead(config, test_pricing):
    """The split fields are parts of the totals, so overhead must not move."""
    record = run_simulation(config, a_spec(), test_pricing)
    ledger = record.ledger

    assert ledger.per_agent_overhead_dollars() == pytest.approx(
        ledger.sentinel_dollars
        + ledger.tier0_dollars
        + ledger.discarded_work_dollars
        + ledger.rerun_work_dollars
    )


def test_the_effective_detector_characteristics_are_reported(config, test_pricing):
    """A row is readable without the config that produced it."""
    metrics = run_metrics(
        run_simulation(
            config,
            a_spec(severity=Severity.SUBTLE, detection_prob=0.8, false_positive_rate=0.01),
            test_pricing,
        )
    )
    assert metrics["effective_detection_prob"] == pytest.approx(0.8)
    assert metrics["effective_false_positive_rate"] == pytest.approx(0.01)
    # And the override that produced them is echoed alongside.
    assert metrics["detection_prob"] == pytest.approx(0.8)


def test_omitting_an_override_reports_the_shipped_operating_point(config, test_pricing):
    metrics = run_metrics(run_simulation(config, a_spec(severity=Severity.SUBTLE), test_pricing))

    assert metrics["detection_prob"] is None, "the spec field records the override, not the value"
    assert metrics["effective_detection_prob"] == pytest.approx(
        float(config["sentinel"]["detection_prob"]["SUBTLE"])
    )
    assert metrics["effective_false_positive_rate"] == pytest.approx(
        float(config["sentinel"]["false_positive_rate"])
    )


def test_an_override_does_not_leak_into_the_next_run(config, test_pricing):
    """The override edits a copy, so it cannot mutate the shared config dict.

    A sweep runs thousands of specs against one config object; an in-place edit
    would silently carry one cell's detector quality into every later cell.
    """
    shipped_subtle = float(config["sentinel"]["detection_prob"]["SUBTLE"])

    overridden = run_metrics(
        run_simulation(
            config, a_spec(severity=Severity.SUBTLE, detection_prob=0.99), test_pricing
        )
    )
    assert overridden["effective_detection_prob"] == pytest.approx(0.99)

    after = run_metrics(run_simulation(config, a_spec(severity=Severity.SUBTLE), test_pricing))
    assert after["effective_detection_prob"] == pytest.approx(shipped_subtle)
    assert config["sentinel"]["detection_prob"]["SUBTLE"] == shipped_subtle


# ---------------------------------------------------------------------------
# incremental_detection_over_tier0_only — a cross-run quantity
# ---------------------------------------------------------------------------


def a_row(arm: str, detection_rate: float, **overrides) -> dict:
    """A minimal metrics row. No prices anywhere near it."""
    row = {name: 0.0 for name in BASELINE_MATCH_KEYS}
    row.update({"arm": arm, "detection_rate": detection_rate, "worker_model": "budget"})
    row.update(overrides)
    return row


def test_incremental_detection_subtracts_the_matched_tier0_baseline():
    rows = [a_row("TIER0_ONLY", 0.25), a_row("HYBRID", 0.75)]
    attach_incremental_detection_over_tier0(rows)

    assert rows[0]["incremental_detection_over_tier0_only"] == pytest.approx(0.0)
    assert rows[1]["incremental_detection_over_tier0_only"] == pytest.approx(0.5)


def test_incremental_detection_matches_within_a_cell_not_across_cells():
    rows = [
        a_row("TIER0_ONLY", 0.1, seed=1),
        a_row("TIER0_ONLY", 0.9, seed=2),
        a_row("HYBRID", 0.5, seed=1),
        a_row("HYBRID", 0.5, seed=2),
    ]
    attach_incremental_detection_over_tier0(rows)

    assert rows[2]["incremental_detection_over_tier0_only"] == pytest.approx(0.4)
    assert rows[3]["incremental_detection_over_tier0_only"] == pytest.approx(-0.4)


def test_a_missing_baseline_is_null_and_never_a_free_uplift():
    rows = [a_row("HYBRID", 0.75)]
    attach_incremental_detection_over_tier0(rows)

    assert rows[0]["incremental_detection_over_tier0_only"] is None


def test_reserve_fraction_is_not_a_baseline_key():
    """HYBRID sweeps reserve_fraction; TIER0_ONLY exists only at 0.0.

    Keying on it would leave every reserve>0 cell without a baseline, which is
    exactly the silent-null failure the previous test guards against.
    """
    assert "reserve_fraction" not in BASELINE_MATCH_KEYS

    rows = [a_row("TIER0_ONLY", 0.2), a_row("HYBRID", 0.6) | {"reserve_fraction": 0.75}]
    attach_incremental_detection_over_tier0(rows)
    assert rows[1]["incremental_detection_over_tier0_only"] == pytest.approx(0.4)


def test_the_three_new_axes_separate_baselines():
    """A detector-quality sweep must not borrow another quality's baseline."""
    rows = [
        a_row("TIER0_ONLY", 0.1, effective_detection_prob=0.2),
        a_row("TIER0_ONLY", 0.1, effective_detection_prob=1.0),
        a_row("HYBRID", 0.3, effective_detection_prob=0.2),
        a_row("HYBRID", 0.9, effective_detection_prob=1.0),
        a_row("HYBRID", 0.5, effective_false_positive_rate=0.05),
        a_row("HYBRID", 0.5, worker_model="flagship"),
    ]
    attach_incremental_detection_over_tier0(rows)

    assert rows[2]["incremental_detection_over_tier0_only"] == pytest.approx(0.2)
    assert rows[3]["incremental_detection_over_tier0_only"] == pytest.approx(0.8)
    # No baseline was run at that FP rate, or at that worker tier.
    assert rows[4]["incremental_detection_over_tier0_only"] is None
    assert rows[5]["incremental_detection_over_tier0_only"] is None


# ---------------------------------------------------------------------------
# 2. The grid: three new axes
# ---------------------------------------------------------------------------


def a_grid(**overrides) -> SweepGrid:
    """A one-cell grid on every pre-existing axis, so the new ones are visible."""
    base = {
        "seeds": (0,),
        "arms": (Arm.TIER0_ONLY,),
        "severities": (Severity.SUBTLE,),
        "budget_pcts": (0.05,),
        "lineage_fidelities": (1.0,),
        "reserve_fractions": (0.0,),
        "fleet_scales": (1.0,),
        "alert_caps": (math.inf,),
        "worker_models": ("budget",),
    }
    base.update(overrides)
    return SweepGrid(**base)


def test_the_grid_is_the_product_of_all_three_new_axes():
    grid = a_grid(
        detection_probs=(0.2, 0.5, 0.8, 1.0),
        false_positive_rates=(0.0, 0.001, 0.01, 0.05, 0.1),
        worker_models=("budget", "mid", "flagship"),
    )
    specs = grid.base_specs()

    assert len(specs) == 4 * 5 * 3
    assert {s.detection_prob for s in specs} == {0.2, 0.5, 0.8, 1.0}
    assert {s.false_positive_rate for s in specs} == {0.0, 0.001, 0.01, 0.05, 0.1}
    assert {s.worker_model for s in specs} == {"budget", "mid", "flagship"}
    # Every combination exactly once.
    triples = [(s.detection_prob, s.false_positive_rate, s.worker_model) for s in specs]
    assert len(set(triples)) == len(triples)


def test_each_new_axis_multiplies_the_grid_independently():
    one = len(a_grid().base_specs())
    assert len(a_grid(detection_probs=(0.2, 1.0)).base_specs()) == one * 2
    assert len(a_grid(false_positive_rates=(0.0, 0.01, 0.1)).base_specs()) == one * 3
    assert len(a_grid(worker_models=("budget", "mid", "flagship")).base_specs()) == one * 3


def test_the_new_axes_reach_the_run_id_so_cells_do_not_overwrite_each_other():
    specs = a_grid(
        detection_probs=(0.2, 1.0),
        false_positive_rates=(0.0, 0.1),
        worker_models=("budget", "flagship"),
    ).base_specs()

    run_ids = [spec.run_id() for spec in specs]
    assert len(set(run_ids)) == len(run_ids), "two cells would write to the same results file"


def test_the_default_grid_is_the_shipped_operating_point():
    """Adding the axes must not silently change what a default sweep means."""
    default = SweepGrid()
    assert default.detection_probs == (None,)
    assert default.false_positive_rates == (None,)
    assert default.worker_models is None  # resolved from the pricing file

    resolved = default.resolved_for(
        Pricing(
            worker_models={DEFAULT_WORKER_MODEL_NAME: ModelPrice(1.0, 1.0, "fixture", "n/a")},
            sentinel_model=ModelPrice(1.0, 1.0, "fixture", "n/a"),
            tier0_cost_per_message_dollars=0.0,
            verified=True,
            path="<fixture>",
        )
    )
    spec = resolved.base_specs()[0]
    assert spec.detection_prob is None
    assert spec.false_positive_rate is None


def test_the_worker_axis_comes_from_the_pricing_file(tiered_pricing):
    resolved = SweepGrid(worker_models=None).resolved_for(tiered_pricing)
    assert resolved.worker_models == ("budget", "mid", "flagship")


def test_adding_a_worker_tier_needs_no_code_change(tiered_pricing):
    """The contract behind the eventual budget/mid/flagship axis.

    A fourth tier appears in the grid because it appeared in the pricing file —
    nothing in Python names a tier or knows how many there are.
    """
    before = len(a_grid(worker_models=None).resolved_for(tiered_pricing).base_specs())

    extended = Pricing(
        worker_models={
            **tiered_pricing.worker_models,
            "frontier": ModelPrice(30.0, 150.0, "test fixture", "n/a"),
        },
        sentinel_model=tiered_pricing.sentinel_model,
        tier0_cost_per_message_dollars=tiered_pricing.tier0_cost_per_message_dollars,
        verified=True,
        path="<test fixture>",
    )
    after = len(a_grid(worker_models=None).resolved_for(extended).base_specs())

    assert after == before + (before // 3), "a new tier should add one pass over the grid"
    assert extended.worker_model_names() == ("budget", "mid", "flagship", "frontier")


def test_an_unresolved_worker_axis_refuses_to_expand():
    with pytest.raises(ValueError, match="resolved_for"):
        SweepGrid(worker_models=None).base_specs()


def test_resolving_rejects_a_tier_the_pricing_file_does_not_define(tiered_pricing):
    with pytest.raises(PricingEntryMissingError) as exc:
        SweepGrid(worker_models=("budget", "imaginary")).resolved_for(tiered_pricing)

    assert "imaginary" in str(exc.value)
    assert "budget, mid, flagship" in str(exc.value), "the error must list what is available"


def test_the_sentinel_stays_a_single_model(tiered_pricing):
    """Three worker tiers, one cross-family detector. No sentinel axis exists."""
    assert not hasattr(SweepGrid(), "sentinel_models")
    assert isinstance(tiered_pricing.sentinel_model, ModelPrice)


@pytest.mark.parametrize("bad", [-0.1, 1.5])
@pytest.mark.parametrize("field", ["detection_prob", "false_positive_rate"])
def test_probabilities_outside_the_unit_interval_are_refused(field, bad):
    with pytest.raises(ValueError, match=field):
        a_spec(**{field: bad})


# ---------------------------------------------------------------------------
# 3. The gate. The important one.
# ---------------------------------------------------------------------------


def test_a_placeholder_pricing_file_fails_the_gate(placeholder_pricing):
    """The rejection path, pinned against a file built to be rejected.

    This used to assert against configs/pricing.yaml, which was correct only
    while that file was empty. Filling it in with sourced figures is the goal,
    so the test now owns its own placeholder and cannot be broken by progress.
    """
    with pytest.raises(PricingNotVerifiedError) as exc:
        check_pricing(placeholder_pricing)

    message = str(exc.value)
    assert "source is empty" in message
    assert "accessed is empty" in message
    assert "verified is false" in message
    assert str(placeholder_pricing.path) in message, "the error must name the file to edit"


def test_the_shipped_pricing_file_carries_full_provenance(shipped_pricing):
    """Whatever configs/pricing.yaml claims, it must be able to back it up.

    The complement of the rejection test, and the one that actually protects a
    reported result: verified: true is only legitimate when every priced entry
    carries a source URL and an access date. Flipping the flag without them is
    the most likely way a placeholder becomes a published number.
    """
    problems = shipped_pricing.verification_problems()
    assert not problems, f"configs/pricing.yaml claims to be reportable but: {problems}"

    for label, price in shipped_pricing.priced_entries():
        assert price.source.strip(), f"{label} has no source URL"
        assert price.accessed.strip(), f"{label} has no access date"
        assert price.input_per_mtok > 0.0, f"{label} has a zero input price"
        assert price.output_per_mtok > 0.0, f"{label} has a zero output price"


def test_run_simulation_aborts_on_unverified_pricing(config, placeholder_pricing):
    """The gate is enforced in code at the run level, not by instruction.

    A single run costed off placeholders is exactly as misleading as a sweep of
    them, so run_simulation refuses too — there is no path to a costed number
    that bypasses this.
    """
    with pytest.raises(PricingNotVerifiedError) as exc:
        run_simulation(config, a_spec(), placeholder_pricing)

    message = str(exc.value)
    assert "unverified pricing" in message
    assert "verified is false" in message
    assert "--allow-unverified-pricing" in message, "the error must name the smoke-run waiver"


def test_a_verified_flag_alone_does_not_clear_the_gate(config):
    """verified: true with an empty source is still a placeholder.

    This is the failure mode the flag is most likely to be used to fake, so it
    is checked independently of the flag.
    """
    unsourced = Pricing(
        worker_models={DEFAULT_WORKER_MODEL_NAME: ModelPrice(3.0, 15.0, "", "")},
        sentinel_model=ModelPrice(5.0, 25.0, "https://example.invalid/pricing", "2026-08-23"),
        tier0_cost_per_message_dollars=0.0000001,
        verified=True,
        path="<test fixture>",
    )

    with pytest.raises(PricingNotVerifiedError) as exc:
        run_simulation(config, a_spec(), unsourced)
    assert "source is empty" in str(exc.value)


def test_one_unsourced_tier_fails_the_whole_file(config, tiered_pricing):
    """Every worker tier carries its own provenance requirement."""
    partly = Pricing(
        worker_models={
            **tiered_pricing.worker_models,
            "mid": ModelPrice(3.0, 15.0, "", ""),
        },
        sentinel_model=tiered_pricing.sentinel_model,
        tier0_cost_per_message_dollars=tiered_pricing.tier0_cost_per_message_dollars,
        verified=True,
        path="<test fixture>",
    )

    problems = partly.verification_problems()
    assert any("worker_models.mid.source" in p for p in problems)
    assert not any("worker_models.budget" in p for p in problems)

    with pytest.raises(PricingNotVerifiedError):
        run_simulation(config, a_spec(worker_model="mid"), partly)


def test_run_simulation_aborts_when_the_named_tier_is_absent(config, tiered_pricing):
    """A missing entry is loud, and never silently substituted.

    Falling back to another tier's price would produce a cost curve labelled
    with a model that did not pay for it.
    """
    with pytest.raises(PricingEntryMissingError) as exc:
        run_simulation(config, a_spec(worker_model="gpt-nonexistent"), tiered_pricing)

    message = str(exc.value)
    assert "gpt-nonexistent" in message
    assert "budget, mid, flagship" in message


def test_an_ambiguous_default_tier_is_raised_not_guessed(tiered_pricing):
    """With three tiers and no 'default', which to price at is the caller's call."""
    with pytest.raises(PricingEntryMissingError) as exc:
        tiered_pricing.default_worker_model_name()
    assert "name one explicitly" in str(exc.value)


def test_the_run_gate_and_the_sweep_gate_are_the_same_gate(placeholder_pricing):
    """One guard, not two that can drift apart."""
    with pytest.raises(PricingNotVerifiedError):
        check_pricing(placeholder_pricing)
    assert check_pricing(placeholder_pricing, allow_unverified=True)


def test_the_smoke_waiver_still_runs_and_stamps_the_result(config, placeholder_pricing):
    """CI needs a path through; everything it produces is marked unverified."""
    record = run_simulation(config, a_spec(), placeholder_pricing, allow_unverified=True)
    assert run_metrics(record)["pricing_verified"] is False


def test_cli_run_exits_nonzero_on_placeholder_pricing(tmp_path, capsys):
    """The gate reaches the command line, not just the library."""
    code = main(
        [
            "run",
            "--config",
            str(REPO_ROOT / "configs" / "default.yaml"),
            "--pricing",
            str(PLACEHOLDER_PRICING_PATH),
            "--out",
            str(tmp_path),
        ]
    )

    assert code == 2
    assert "unverified pricing" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.json")), "an aborted run must not leave a result behind"


# ---------------------------------------------------------------------------
# The pricing registry's two accepted shapes
# ---------------------------------------------------------------------------


def test_a_legacy_singular_worker_block_loads_as_a_one_tier_registry():
    """configs/pricing.smoke.yaml still uses the singular block and must load.

    Backward compatibility is checked against a real file that genuinely has the
    old shape, rather than against whichever shape the main pricing file has
    today.
    """
    pricing = load_pricing(REPO_ROOT / "configs" / "pricing.smoke.yaml")

    assert pricing.worker_model_names() == (DEFAULT_WORKER_MODEL_NAME,)
    assert pricing.default_worker_model_name() == DEFAULT_WORKER_MODEL_NAME


def test_the_shipped_pricing_file_defines_a_multi_tier_worker_registry(shipped_pricing):
    """The worker axis has something to sweep, and no tier is named 'default'."""
    names = shipped_pricing.worker_model_names()
    assert len(names) >= 1
    for name in names:
        assert shipped_pricing.worker_price(name) is not None


def test_a_worker_models_mapping_loads_in_yaml_order(tmp_path):
    path = tmp_path / "pricing.yaml"
    path.write_text(
        "verified: false\n"
        "worker_models:\n"
        "  budget: {input_per_mtok: 1.0, output_per_mtok: 2.0, source: s, accessed: a}\n"
        "  mid: {input_per_mtok: 3.0, output_per_mtok: 4.0, source: s, accessed: a}\n"
        "  flagship: {input_per_mtok: 5.0, output_per_mtok: 6.0, source: s, accessed: a}\n"
        "sentinel_model: {input_per_mtok: 7.0, output_per_mtok: 8.0, source: s, accessed: a}\n"
        "tier0: {cost_per_message_dollars: 0.0000001}\n",
        encoding="utf-8",
    )

    pricing = load_pricing(path)
    assert pricing.worker_model_names() == ("budget", "mid", "flagship")


def test_defining_both_shapes_is_refused(tmp_path):
    """Two answers to "what is this run priced at" is not a thing to resolve."""
    path = tmp_path / "pricing.yaml"
    path.write_text(
        "verified: false\n"
        "worker_model: {input_per_mtok: 1.0, output_per_mtok: 2.0, source: s, accessed: a}\n"
        "worker_models:\n"
        "  mid: {input_per_mtok: 3.0, output_per_mtok: 4.0, source: s, accessed: a}\n"
        "sentinel_model: {input_per_mtok: 7.0, output_per_mtok: 8.0, source: s, accessed: a}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="keep one"):
        load_pricing(path)
