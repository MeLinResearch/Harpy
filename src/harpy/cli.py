"""``harpy run`` / ``harpy sweep`` / ``harpy plot``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .ledger import load_pricing
from .plot import detection_vs_overhead, hybrid_reserve_breakdown
from .simulation import RunSpec, resolve_severity
from .sweep import (
    PricingNotVerifiedError,
    SweepGrid,
    load_result_rows,
    run_single,
    run_sweep,
)
from .types import Arm, Severity

DEFAULT_CONFIG = "configs/default.yaml"
DEFAULT_PRICING = "configs/pricing.yaml"


def load_config(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _pricing_path(args, config_path: str) -> str:
    if args.pricing:
        return args.pricing
    sibling = Path(config_path).parent / "pricing.yaml"
    return str(sibling) if sibling.exists() else DEFAULT_PRICING


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    pricing = load_pricing(_pricing_path(args, args.config))
    spec = RunSpec(
        seed=args.seed,
        # With no severity pinned on either side, draw one from the
        # preregistered fault distribution.
        severity=resolve_severity(
            config, args.severity or config["run"].get("severity"), args.seed
        ),
        arm=Arm(args.arm or config["sampler"]["arm"]),
        budget_pct=(
            args.budget_pct if args.budget_pct is not None else config["sampler"]["budget_pct"]
        ),
        lineage_fidelity=(
            args.lineage_fidelity
            if args.lineage_fidelity is not None
            else config["lineage"]["fidelity"]
        ),
        reserve_fraction=(
            args.reserve_fraction
            if args.reserve_fraction is not None
            else config["sampler"]["reserve_fraction"]
        ),
    )
    path = run_single(config, spec, pricing, args.out)
    document = json.loads(path.read_text(encoding="utf-8"))
    metrics = document["metrics"]
    print(f"wrote {path}")
    print(f"  arm={metrics['arm']} severity={metrics['severity']} seed={metrics['seed']}")
    print(f"  detected={bool(metrics['detection_rate'])} at tick {metrics['detected_tick']}")
    print(f"  audits={metrics['audits_run']} overhead={metrics['overhead_pct'] * 100:.3f}%")
    print(f"  pricing_verified={metrics['pricing_verified']}")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    pricing = load_pricing(_pricing_path(args, args.config))
    grid = SweepGrid()
    if args.seeds:
        grid = SweepGrid(seeds=tuple(_parse_seeds(args.seeds)))
    try:
        summary = run_sweep(
            config,
            pricing,
            args.out,
            grid=grid,
            processes=args.processes,
            allow_unverified=args.allow_unverified_pricing,
        )
    except PricingNotVerifiedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"wrote {summary}")
    return 0


def _cmd_plot(args: argparse.Namespace) -> int:
    rows = load_result_rows(args.results)
    if not rows:
        print(f"no run JSONs found in {args.results}", file=sys.stderr)
        return 2
    out = detection_vs_overhead(rows, args.out)
    print(f"wrote {out}")
    companion = Path(args.out).with_name(Path(args.out).stem + "_hybrid_reserve.png")
    if hybrid_reserve_breakdown(rows, companion) is not None:
        print(f"wrote {companion}")
    return 0


def _parse_seeds(text: str) -> list[int]:
    """Accept ``0..2`` or ``0,1,2``."""
    if ".." in text:
        low, high = text.split("..", 1)
        return list(range(int(low), int(high) + 1))
    return [int(part) for part in text.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harpy", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="one simulation run")
    run.add_argument("--config", default=DEFAULT_CONFIG)
    run.add_argument("--pricing", default=None)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--arm", default=None, choices=[a.value for a in Arm])
    run.add_argument("--severity", default=None, choices=[s.value for s in Severity])
    run.add_argument("--budget-pct", type=float, default=None)
    run.add_argument("--lineage-fidelity", type=float, default=None)
    run.add_argument("--reserve-fraction", type=float, default=None)
    run.add_argument("--out", default="results/")
    run.set_defaults(func=_cmd_run)

    sweep = sub.add_parser("sweep", help="the full grid, across processes")
    sweep.add_argument("--config", default=DEFAULT_CONFIG)
    sweep.add_argument("--pricing", default=None)
    sweep.add_argument("--out", default="results/")
    sweep.add_argument("--seeds", default=None, help="e.g. 0..2 or 0,1,2 (default 0..49)")
    sweep.add_argument("--processes", type=int, default=None)
    sweep.add_argument(
        "--allow-unverified-pricing",
        action="store_true",
        help="smoke runs only; stamps every result pricing_verified=false",
    )
    sweep.set_defaults(func=_cmd_sweep)

    plot = sub.add_parser("plot", help="detection vs overhead")
    plot.add_argument("--results", default="results/")
    plot.add_argument("--out", default="results/detection_vs_overhead.png")
    plot.set_defaults(func=_cmd_plot)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
