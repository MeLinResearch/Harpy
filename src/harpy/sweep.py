"""The grid. Runs are independent, so they fan out across processes.

The sweep is also the gate on pricing: it refuses to start while
configs/pricing.yaml still carries placeholders, because a cost curve drawn from
made-up prices is worse than no cost curve — it looks like a finding.
"""

from __future__ import annotations

import json
import multiprocessing as mp
from dataclasses import dataclass
from pathlib import Path

from .ledger import Pricing, load_pricing
from .metrics import result_document, run_metrics
from .simulation import RunSpec, run_simulation
from .types import Arm, Severity

SEEDS: tuple[int, ...] = tuple(range(50))
ARMS: tuple[Arm, ...] = tuple(Arm)
SEVERITIES: tuple[Severity, ...] = tuple(Severity)
BUDGET_PCTS: tuple[float, ...] = (0.01, 0.025, 0.05, 0.10, 0.20)
LINEAGE_FIDELITIES: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
RESERVE_FRACTIONS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)


class PricingNotVerifiedError(RuntimeError):
    """Raised when a sweep is asked to run on placeholder prices."""


@dataclass(frozen=True)
class SweepGrid:
    seeds: tuple[int, ...] = SEEDS
    arms: tuple[Arm, ...] = ARMS
    severities: tuple[Severity, ...] = SEVERITIES
    budget_pcts: tuple[float, ...] = BUDGET_PCTS
    lineage_fidelities: tuple[float, ...] = LINEAGE_FIDELITIES
    reserve_fractions: tuple[float, ...] = RESERVE_FRACTIONS

    def specs(self) -> list[RunSpec]:
        specs: list[RunSpec] = []
        for seed in self.seeds:
            for arm in self.arms:
                # reserve_fraction is a HYBRID-only knob; every other arm would
                # produce identical duplicate runs across it.
                reserves = self.reserve_fractions if arm is Arm.HYBRID else (0.0,)
                for severity in self.severities:
                    for budget in self.budget_pcts:
                        for fidelity in self.lineage_fidelities:
                            for reserve in reserves:
                                specs.append(
                                    RunSpec(
                                        seed=seed,
                                        severity=severity,
                                        arm=arm,
                                        budget_pct=budget,
                                        lineage_fidelity=fidelity,
                                        reserve_fraction=reserve,
                                    )
                                )
        return specs


def check_pricing(pricing: Pricing, allow_unverified: bool = False) -> list[str]:
    """Return the problems with this pricing file, raising unless waived.

    The waiver exists for smoke runs and CI only. Every run produced under it is
    stamped ``pricing_verified: false`` in its results JSON and the plot is
    watermarked, so an unverified curve cannot be mistaken for a reported one.
    """
    problems = pricing.verification_problems()
    if problems and not allow_unverified:
        raise PricingNotVerifiedError(
            "refusing to sweep on unverified pricing:\n  - "
            + "\n  - ".join(problems)
            + f"\nEdit {pricing.path}, or pass --allow-unverified-pricing for a smoke run "
            "whose numbers will be stamped unverified."
        )
    return problems


def _worker(payload: tuple) -> dict:
    config, spec, pricing = payload
    return result_document(run_simulation(config, spec, pricing))


def run_sweep(
    config: dict,
    pricing: Pricing,
    out_dir: str | Path,
    grid: SweepGrid | None = None,
    processes: int | None = None,
    allow_unverified: bool = False,
    progress: bool = True,
) -> Path:
    """Execute the grid, write one JSON per run, and return the summary path."""
    problems = check_pricing(pricing, allow_unverified=allow_unverified)
    if problems:
        print("WARNING: running on unverified pricing. Results are stamped unverified.")
        for problem in problems:
            print(f"  - {problem}")

    grid = grid or SweepGrid()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = grid.specs()
    payloads = [(config, spec, pricing) for spec in specs]

    processes = processes or max(mp.cpu_count() - 1, 1)
    rows: list[dict] = []

    def drain(documents) -> None:
        # Written as they land rather than at the end: the full grid is tens of
        # thousands of runs, and a crash an hour in should not cost all of them.
        for index, document in enumerate(documents, start=1):
            run_id = document["metrics"]["run_id"]
            (out_dir / f"{run_id}.json").write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            rows.append(document["metrics"])
            if progress and index % 200 == 0:
                print(f"  {index}/{len(specs)} runs", flush=True)

    if processes == 1:
        drain(_worker(payload) for payload in payloads)
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes) as pool:
            drain(pool.imap_unordered(_worker, payloads, chunksize=4))

    return write_summary(rows, out_dir)


def write_summary(rows: list[dict], out_dir: str | Path) -> Path:
    """One row per run. Aggregation happens at plot time, not here."""
    import pandas as pd

    out_dir = Path(out_dir)
    frame = pd.DataFrame(rows)
    if "fault_distribution" in frame.columns:
        # Parquet has no dict column type worth relying on; keep it as JSON text
        # so the preregistered distribution still travels with every row.
        frame["fault_distribution"] = frame["fault_distribution"].map(
            lambda value: json.dumps(value, sort_keys=True)
        )
    frame = frame.sort_values("run_id").reset_index(drop=True)
    path = out_dir / "summary.parquet"
    frame.to_parquet(path, index=False)
    return path


def run_single(config: dict, spec: RunSpec, pricing: Pricing, out_dir: str | Path) -> Path:
    """One run, one JSON. Used by ``harpy run``; no pricing gate."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    document = result_document(run_simulation(config, spec, pricing))
    path = out_dir / f"{document['metrics']['run_id']}.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_result_rows(results_dir: str | Path) -> list[dict]:
    """Read the metrics block out of every run JSON in a results directory."""
    rows: list[dict] = []
    for path in sorted(Path(results_dir).glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema") != "harpy-sim/run/1":
            continue
        rows.append(document["metrics"])
    return rows


__all__ = [
    "SweepGrid",
    "PricingNotVerifiedError",
    "check_pricing",
    "load_pricing",
    "load_result_rows",
    "run_single",
    "run_sweep",
    "run_metrics",
    "write_summary",
]
