"""The grid. Runs are independent, so they fan out across processes.

The sweep is also the gate on pricing: it refuses to start while
configs/pricing.yaml still carries placeholders, because a cost curve drawn from
made-up prices is worse than no cost curve — it looks like a finding.
"""

from __future__ import annotations

import dataclasses
import json
import multiprocessing as mp
from dataclasses import dataclass
from pathlib import Path

from .ledger import (
    Pricing,
    PricingNotVerifiedError,
    load_pricing,
    require_verified_pricing,
)
from .metrics import attach_incremental_detection_over_tier0, result_document, run_metrics
from .simulation import RunSpec, rescale, run_simulation
from .types import Arm, Severity

SEEDS: tuple[int, ...] = tuple(range(50))
ARMS: tuple[Arm, ...] = tuple(Arm)
SEVERITIES: tuple[Severity, ...] = tuple(Severity)
BUDGET_PCTS: tuple[float, ...] = (0.01, 0.025, 0.05, 0.10, 0.20)
LINEAGE_FIDELITIES: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
RESERVE_FRACTIONS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
#: How many times larger the real fleet is than the 30-agent simulated slice.
#: The slice is a sample, not a fleet: at scale 1 an alert is a sixth of the
#: oversight bill and at scale 1000 it is invisible, and neither number is a
#: fact about HARPY until the scale it was computed at is stated.
FLEET_SCALES: tuple[float, ...] = (1.0, 10.0, 100.0, 1000.0)
#: The second budget's cap. ``inf`` is the no-cap control.
ALERT_CAPS: tuple[float, ...] = (0.01, 0.05, 0.25, 1.0, float("inf"))
#: ``None`` is the shipped operating point: the configured
#: ``sentinel.detection_prob[severity]`` and ``sentinel.false_positive_rate``.
#: The default grid is therefore the same size it was before these axes existed,
#: and a diagnostic sweep supplies its own values.
DETECTION_PROBS: tuple[float | None, ...] = (None,)
FALSE_POSITIVE_RATES: tuple[float | None, ...] = (None,)


@dataclass(frozen=True)
class SweepGrid:
    seeds: tuple[int, ...] = SEEDS
    arms: tuple[Arm, ...] = ARMS
    severities: tuple[Severity, ...] = SEVERITIES
    budget_pcts: tuple[float, ...] = BUDGET_PCTS
    lineage_fidelities: tuple[float, ...] = LINEAGE_FIDELITIES
    reserve_fractions: tuple[float, ...] = RESERVE_FRACTIONS
    fleet_scales: tuple[float, ...] = FLEET_SCALES
    alert_caps: tuple[float, ...] = ALERT_CAPS
    detection_probs: tuple[float | None, ...] = DETECTION_PROBS
    false_positive_rates: tuple[float | None, ...] = FALSE_POSITIVE_RATES
    #: Worker tiers to price runs at, by name. ``None`` means "every tier
    #: configs/pricing.yaml defines" — resolved by :meth:`resolved_for`, which is
    #: what makes adding a tier a YAML edit and not a code change.
    worker_models: tuple[str, ...] | None = None

    def resolved_for(self, pricing: Pricing) -> SweepGrid:
        """This grid with its worker axis filled in from the pricing file."""
        if self.worker_models is not None:
            for name in self.worker_models:
                pricing.worker_price(name)  # fail now, not mid-sweep
            return self
        return dataclasses.replace(self, worker_models=pricing.worker_model_names())

    def base_specs(self) -> list[RunSpec]:
        """One spec per distinct simulation, at ``fleet_scales[0]``.

        Every axis here changes the run. ``fleet_scale`` does not, so it is not
        one of them — see :meth:`specs`.
        """
        if self.worker_models is None:
            raise ValueError(
                "worker_models is unresolved; call grid.resolved_for(pricing) first so the "
                "worker axis comes from the pricing file rather than from Python"
            )
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
                                for cap in self.alert_caps:
                                    for detection in self.detection_probs:
                                        for fpr in self.false_positive_rates:
                                            for worker in self.worker_models:
                                                specs.append(
                                                    RunSpec(
                                                        seed=seed,
                                                        severity=severity,
                                                        arm=arm,
                                                        budget_pct=budget,
                                                        lineage_fidelity=fidelity,
                                                        reserve_fraction=reserve,
                                                        fleet_scale=self.fleet_scales[0],
                                                        max_alerts_per_agent_hour=cap,
                                                        detection_prob=detection,
                                                        false_positive_rate=fpr,
                                                        worker_model=worker,
                                                    )
                                                )
        return specs

    def specs(self) -> list[RunSpec]:
        """Every cell of the grid, fleet_scale included.

        ``fleet_scale`` multiplies four cost components after the fact and
        touches no RNG stream, no threshold and no decision, so the sweep
        simulates each :meth:`base_specs` entry once and re-costs it at each
        scale. The results are identical to simulating every cell separately,
        and this enumeration is what a cell count should be read off.
        """
        return [
            dataclasses.replace(spec, fleet_scale=scale)
            for spec in self.base_specs()
            for scale in self.fleet_scales
        ]


def check_pricing(pricing: Pricing, allow_unverified: bool = False) -> list[str]:
    """Return the problems with this pricing file, raising unless waived.

    Thin wrapper over :func:`~harpy.ledger.require_verified_pricing`, which is
    where the guard now lives so that every entry point honours it — including
    a bare :func:`~harpy.simulation.run_simulation` call that never touches this
    module.
    """
    return require_verified_pricing(pricing, allow_unverified=allow_unverified, context="sweep")


def _worker(payload: tuple) -> list[dict]:
    """One simulation, costed at every fleet scale it is asked for."""
    config, spec, pricing, fleet_scales, allow_unverified = payload
    record = run_simulation(config, spec, pricing, allow_unverified=allow_unverified)
    return [result_document(rescale(record, scale)) for scale in fleet_scales]


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

    grid = (grid or SweepGrid()).resolved_for(pricing)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_specs = grid.base_specs()
    n_cells = len(base_specs) * len(grid.fleet_scales)
    payloads = [
        (config, spec, pricing, grid.fleet_scales, allow_unverified) for spec in base_specs
    ]

    processes = processes or max(mp.cpu_count() - 1, 1)
    rows: list[dict] = []

    def drain(batches) -> None:
        # Written as they land rather than at the end: the full grid is hundreds
        # of thousands of cells, and a crash an hour in should not cost all of
        # them.
        for batch in batches:
            for document in batch:
                run_id = document["metrics"]["run_id"]
                (out_dir / f"{run_id}.json").write_text(
                    json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                rows.append(document["metrics"])
            if progress and len(rows) % 400 < len(batch):
                print(f"  {len(rows)}/{n_cells} cells", flush=True)

    if processes == 1:
        drain(_worker(payload) for payload in payloads)
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes) as pool:
            drain(pool.imap_unordered(_worker, payloads, chunksize=4))

    # The baseline subtraction needs the whole grid, so it runs once here rather
    # than per run. The individual JSONs on disk keep null for it — they were
    # written as they landed, which is what makes a crash mid-sweep survivable —
    # and load_result_rows re-derives it whenever they are read back.
    attach_incremental_detection_over_tier0(rows)
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


def run_single(
    config: dict,
    spec: RunSpec,
    pricing: Pricing,
    out_dir: str | Path,
    allow_unverified: bool = False,
) -> Path:
    """One run, one JSON. Used by ``harpy run``.

    Gated on pricing exactly like the sweep is: the gate lives inside
    :func:`~harpy.simulation.run_simulation` now, so there is no longer a path
    that quietly produces a costed run off placeholders.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    document = result_document(
        run_simulation(config, spec, pricing, allow_unverified=allow_unverified)
    )
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
    # Re-derived on read: the baseline is a different run, so it is a property
    # of the set of results, not of any file in it.
    return attach_incremental_detection_over_tier0(rows)


__all__ = [
    "SweepGrid",
    "PricingNotVerifiedError",
    "attach_incremental_detection_over_tier0",
    "check_pricing",
    "load_pricing",
    "load_result_rows",
    "run_single",
    "run_sweep",
    "run_metrics",
    "write_summary",
]
