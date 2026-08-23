"""The two diagnostic sweeps, defined once so a result can be reproduced.

SWEEP A asks whether SUBTLE is a targeting failure or a detection failure, by
sweeping detector quality with everything else at the shipped operating point.
SWEEP B asks what false-positive rate still clears the kill condition.

Both are held at the shipped operating point on every axis they do not sweep —
reserve_fraction 0.5, alert cap 0.05/agent-h, and for B, lineage fidelity 1.0 —
because "holding everything else fixed" is what makes a diagnostic a diagnostic.

Usage:
    python scripts/diagnostic_sweeps.py A --out results/sweep_a
    python scripts/diagnostic_sweeps.py B --out results/sweep_b --pricing configs/pricing.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harpy.cli import load_config
from harpy.ledger import load_pricing
from harpy.sweep import SweepGrid, run_sweep
from harpy.types import Arm, Severity

SEEDS = tuple(range(50))

#: Shipped operating point, restated here only where a sweep must hold it fixed.
SHIPPED_RESERVE = 0.5
SHIPPED_ALERT_CAP = 0.05
SHIPPED_FIDELITY = 1.0
SHIPPED_BUDGET_PCT = 0.05


def sweep_a(worker_models: tuple[str, ...] | None) -> SweepGrid:
    """Detector quality vs detection. SUBTLE only — it is SUBTLE's knob being swept."""
    return SweepGrid(
        seeds=SEEDS,
        arms=tuple(Arm),
        severities=(Severity.SUBTLE,),
        budget_pcts=(SHIPPED_BUDGET_PCT,),
        lineage_fidelities=(0.0, 0.25, 0.5, 0.75, 1.0),
        reserve_fractions=(SHIPPED_RESERVE,),
        fleet_scales=(1.0,),
        alert_caps=(SHIPPED_ALERT_CAP,),
        detection_probs=(0.2, 0.5, 0.8, 1.0),
        false_positive_rates=(None,),
        worker_models=worker_models,
    )


def sweep_b(worker_models: tuple[str, ...] | None) -> SweepGrid:
    """False-positive rate vs viability. All arms, all severities.

    fleet_scale is listed with both values because it is free: it multiplies
    four cost components after the fact and touches no decision, so the sweep
    simulates each cell once and re-costs it at each scale.
    """
    return SweepGrid(
        seeds=SEEDS,
        arms=tuple(Arm),
        severities=tuple(Severity),
        budget_pcts=(0.05, 0.10, 0.20),
        lineage_fidelities=(SHIPPED_FIDELITY,),
        reserve_fractions=(SHIPPED_RESERVE,),
        fleet_scales=(1.0, 100.0),
        alert_caps=(SHIPPED_ALERT_CAP,),
        detection_probs=(None,),
        false_positive_rates=(0.0, 0.001, 0.01, 0.05, 0.1),
        worker_models=worker_models,
    )


GRIDS = {"A": sweep_a, "B": sweep_b}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sweep", choices=sorted(GRIDS))
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--pricing", default="configs/pricing.yaml")
    parser.add_argument("--out", required=True)
    parser.add_argument("--processes", type=int, default=None)
    parser.add_argument(
        "--worker-models",
        default=None,
        help="comma-separated tier names (default: every tier in the pricing file)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the cell count and stop")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    pricing = load_pricing(args.pricing)
    names = (
        tuple(part.strip() for part in args.worker_models.split(",") if part.strip())
        if args.worker_models
        else None
    )
    grid = GRIDS[args.sweep](names).resolved_for(pricing)

    n_specs = len(grid.base_specs())
    print(
        f"SWEEP {args.sweep}: {n_specs:,} simulations"
        f" x {len(grid.fleet_scales)} fleet scale(s) = {n_specs * len(grid.fleet_scales):,} rows"
    )
    print(f"  worker tiers: {', '.join(grid.worker_models)}")
    print(f"  pricing: {pricing.path} (verified={pricing.verified})")
    if args.dry_run:
        return 0

    summary = run_sweep(config, pricing, args.out, grid=grid, processes=args.processes)
    print(f"wrote {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
