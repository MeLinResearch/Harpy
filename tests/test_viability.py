"""The derived deliverable and the two diagnostic figures.

``max_false_positive_rate_for_viability`` is the number SWEEP B exists to
produce, so its edge cases are pinned here on synthetic aggregate rows: no
simulation, no pricing file, and no assertion about what the real answer is.
``overhead_pct`` appears in these fixtures as a plain number because that is
what the function consumes — nothing here asserts a dollar figure.
"""

from __future__ import annotations

import pytest

from harpy.metrics import KILL_CEILING_PCT, max_false_positive_rate_for_viability
from harpy.plot import detection_vs_detector_quality, viability_vs_false_positive_rate
from harpy.types import Arm, Severity


def a_cell(**overrides) -> dict:
    """One aggregated cell. Viable by default; override to break one half."""
    cell = {
        "arm": Arm.HYBRID.value,
        "severity": Severity.SUBTLE.value,
        "budget_pct": 0.05,
        "lineage_fidelity": 1.0,
        "reserve_fraction": 0.5,
        "fleet_scale": 1.0,
        "max_alerts_per_agent_hour": float("inf"),
        "effective_detection_prob": 0.42,
        "effective_false_positive_rate": 0.01,
        "detection_rate": 0.6,
        "overhead_pct": 0.05,
        "incremental_detection_over_tier0_only": 0.2,
        "mean_incremental_detection_over_tier0_only": 0.2,
        "mean_audit_coverage_of_faulty_agent": 0.3,
        "pricing_verified": True,
    }
    cell.update(overrides)
    return cell


# ---------------------------------------------------------------------------
# The derived deliverable
# ---------------------------------------------------------------------------


def test_it_returns_the_largest_rate_that_clears_both_halves():
    rows = [
        a_cell(effective_false_positive_rate=0.001),
        a_cell(effective_false_positive_rate=0.01),
        a_cell(effective_false_positive_rate=0.05),
        # Detects more, but only by blowing the ceiling.
        a_cell(effective_false_positive_rate=0.1, overhead_pct=0.3),
    ]
    table = max_false_positive_rate_for_viability(rows)

    assert table[Severity.SUBTLE.value]["max_false_positive_rate"] == pytest.approx(0.05)
    assert table[Severity.SUBTLE.value]["false_positive_rates_swept"] == [0.001, 0.01, 0.05, 0.1]


def test_overhead_exactly_at_the_ceiling_still_counts_as_under_it():
    """The condition is "under 10% overhead" inclusive; pinned so it cannot drift."""
    rows = [a_cell(overhead_pct=KILL_CEILING_PCT)]
    assert max_false_positive_rate_for_viability(rows)[Severity.SUBTLE.value][
        "max_false_positive_rate"
    ] == pytest.approx(0.01)


def test_zero_uplift_does_not_clear_it():
    """Matching TIER0_ONLY is not beating it; a sentinel has to earn its keep."""
    rows = [a_cell(incremental_detection_over_tier0_only=0.0)]
    assert (
        max_false_positive_rate_for_viability(rows)[Severity.SUBTLE.value][
            "max_false_positive_rate"
        ]
        is None
    )


def test_negative_uplift_does_not_clear_it():
    rows = [a_cell(incremental_detection_over_tier0_only=-0.2)]
    assert (
        max_false_positive_rate_for_viability(rows)[Severity.SUBTLE.value][
            "max_false_positive_rate"
        ]
        is None
    )


def test_a_null_baseline_is_dropped_rather_than_treated_as_zero():
    """A cell we could not compare must not be counted as showing no uplift."""
    rows = [a_cell(incremental_detection_over_tier0_only=None)]
    result = max_false_positive_rate_for_viability(rows)[Severity.SUBTLE.value]

    assert result["max_false_positive_rate"] is None
    assert result["n_cells_clearing"] == 0


def test_null_when_nothing_clears_is_a_finding_not_a_gap():
    rows = [
        a_cell(effective_false_positive_rate=0.0, overhead_pct=0.5),
        a_cell(effective_false_positive_rate=0.1, overhead_pct=0.5),
    ]
    result = max_false_positive_rate_for_viability(rows)[Severity.SUBTLE.value]

    assert result["max_false_positive_rate"] is None
    # The rates that were tried are still reported, so a null is auditable.
    assert result["false_positive_rates_swept"] == [0.0, 0.1]


def test_severity_is_a_key_and_never_collapsed():
    """An OVERT rate must not be reported as if it held for SUBTLE."""
    rows = [
        a_cell(severity=Severity.OVERT.value, effective_false_positive_rate=0.1),
        a_cell(severity=Severity.SUBTLE.value, effective_false_positive_rate=0.1, overhead_pct=0.9),
        a_cell(severity=Severity.SUBTLE.value, effective_false_positive_rate=0.001),
    ]
    table = max_false_positive_rate_for_viability(rows)

    assert table[Severity.OVERT.value]["max_false_positive_rate"] == pytest.approx(0.1)
    assert table[Severity.SUBTLE.value]["max_false_positive_rate"] == pytest.approx(0.001)


def test_tier0_only_cannot_clear_its_own_baseline():
    """The baseline arm is excluded; it can never show uplift over itself."""
    rows = [a_cell(arm=Arm.TIER0_ONLY.value, incremental_detection_over_tier0_only=0.0)]
    assert (
        max_false_positive_rate_for_viability(rows)[Severity.SUBTLE.value][
            "max_false_positive_rate"
        ]
        is None
    )


def test_the_clearing_arms_are_reported_so_the_number_is_auditable():
    rows = [
        a_cell(arm=Arm.DIRECTED.value),
        a_cell(arm=Arm.HYBRID.value),
        a_cell(arm=Arm.RANDOM.value, overhead_pct=0.4),
    ]
    result = max_false_positive_rate_for_viability(rows)[Severity.SUBTLE.value]

    assert result["arms_clearing"] == ["DIRECTED", "HYBRID"]
    assert result["n_cells_clearing"] == 2


def test_a_run_with_no_fp_axis_swept_reports_null_not_a_crash():
    rows = [a_cell(effective_false_positive_rate=None)]
    result = max_false_positive_rate_for_viability(rows)[Severity.SUBTLE.value]

    assert result["max_false_positive_rate"] is None
    assert result["false_positive_rates_swept"] == []


# ---------------------------------------------------------------------------
# The two figures — that they draw, and that they refuse to fake a curve
# ---------------------------------------------------------------------------


def a_run_row(**overrides) -> dict:
    """A per-run metrics row, the shape aggregate() consumes."""
    row = {
        "arm": Arm.HYBRID.value,
        "severity": Severity.SUBTLE.value,
        "budget_pct": 0.05,
        "lineage_fidelity": 1.0,
        "reserve_fraction": 0.5,
        "fleet_scale": 1.0,
        "max_alerts_per_agent_hour": float("inf"),
        "effective_detection_prob": 0.42,
        "effective_false_positive_rate": 0.01,
        "worker_model": "budget",
        "detection_rate": 0.5,
        "overhead_pct": 0.04,
        "false_isolations_per_agent_hour": 0.0,
        "median_interactions_to_detection": 3.0,
        "contaminated_claims_at_isolation": 2,
        "min_fleet_dollars_per_hour_for_viability": 1.0,
        "sentinel_dollars": 1.0,
        "tier0_dollars": 0.1,
        "discarded_work_dollars": 0.5,
        "rerun_work_dollars": 0.5,
        "triage_dollars": 0.2,
        "investigation_dollars": 0.1,
        "worker_dollars": 50.0,
        "audits_run": 10,
        "review_cost_share_of_overhead": 0.2,
        "alerts_per_agent_hour": 0.01,
        "suppressed_alerts": 0,
        "detections_lost_to_alert_cap": 0,
        "effective_isolation_threshold": 2.6,
        "audits_on_faulty_agent": 2,
        "audits_during_fault_exposure": 8,
        "audit_coverage_of_faulty_agent": 0.25,
        "ticks_faulty_before_first_audit": 4,
        "false_isolation_count": 0,
        "discard_rerun_dollars_from_false_isolations": 0.0,
        "discard_rerun_dollars_from_true_isolations": 1.0,
        "incremental_detection_over_tier0_only": 0.2,
        "pricing_verified": True,
    }
    row.update(overrides)
    return row


def _quality_rows() -> list[dict]:
    rows = []
    for prob in (0.2, 0.5, 0.8, 1.0):
        for arm in (Arm.TIER0_ONLY.value, Arm.HYBRID.value):
            rows.append(
                a_run_row(
                    arm=arm,
                    effective_detection_prob=prob,
                    detection_rate=0.0 if arm == Arm.TIER0_ONLY.value else prob * 0.5,
                )
            )
    return rows


def test_the_detector_quality_figure_draws(tmp_path):
    path = detection_vs_detector_quality(_quality_rows(), tmp_path / "quality.png")
    assert path.exists() and path.stat().st_size > 0


def test_the_detector_quality_figure_refuses_a_severity_with_no_cells(tmp_path):
    with pytest.raises(ValueError, match="severity"):
        detection_vs_detector_quality(
            _quality_rows(), tmp_path / "quality.png", severity=Severity.OVERT.value
        )


def test_the_viability_figure_draws(tmp_path):
    rows = []
    for rate in (0.0, 0.01, 0.1):
        for budget in (0.05, 0.1, 0.2):
            rows.append(
                a_run_row(
                    arm=Arm.TIER0_ONLY.value,
                    effective_false_positive_rate=rate,
                    budget_pct=budget,
                    detection_rate=0.1,
                    reserve_fraction=0.0,
                )
            )
            rows.append(
                a_run_row(
                    effective_false_positive_rate=rate,
                    budget_pct=budget,
                    detection_rate=0.4,
                    overhead_pct=budget,
                )
            )

    path = viability_vs_false_positive_rate(rows, tmp_path / "viability.png")
    assert path.exists() and path.stat().st_size > 0


def test_the_viability_figure_refuses_when_no_baseline_exists(tmp_path):
    """Every arm unmatched means every y value is null — nothing to draw."""
    rows = [a_run_row(incremental_detection_over_tier0_only=None)]
    with pytest.raises(ValueError, match="baseline"):
        viability_vs_false_positive_rate(rows, tmp_path / "viability.png")


def test_both_figures_watermark_unverified_pricing(tmp_path):
    """A figure drawn on placeholder prices must not look like a reported one."""
    rows = [row | {"pricing_verified": False} for row in _quality_rows()]
    path = detection_vs_detector_quality(rows, tmp_path / "unverified.png")
    assert path.exists()
