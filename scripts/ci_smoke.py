"""Exercise every arm and severity with endpoint axes, not an exhaustive study."""

from __future__ import annotations

import argparse

from harpy.cli import load_config
from harpy.ledger import load_pricing
from harpy.sweep import SweepGrid, run_sweep


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--processes", type=int, default=2)
    args = parser.parse_args()
    grid = SweepGrid(
        seeds=(0,),
        budget_pcts=(0.01, 0.20),
        lineage_fidelities=(0.0, 1.0),
        reserve_fractions=(0.0, 1.0),
        fleet_scales=(1.0, 1000.0),
        alert_caps=(0.05, float("inf")),
        review_scalings=("fixed", "linear"),
    )
    run_sweep(
        load_config("configs/default.yaml"),
        load_pricing("configs/pricing.smoke.yaml"),
        args.out,
        grid,
        processes=args.processes,
        allow_unverified=True,
    )


if __name__ == "__main__":
    main()
