"""Pre-freeze feasibility across candidate v3 designs. A design input, not evidence.

Runs a disclosed pilot on seeds 100000+ (never study seeds) over:

    sentinel price x worker tier x arm x follow-up depth x budget

then replays the frozen v2 decision rule (10-point floor, <10% ceiling) on
outcomes drawn from each cell's pilot, under both review-scaling policies at
fleet scale 100. Writes:

    <out>/runs.jsonl      one line per simulated run and review policy
    <out>/grid.json       per-cell arithmetic, pilot estimates and assurance curve
    <out>/grid.csv        the same, flattened for sorting

Usage:
    uv run python scripts/v3_feasibility_grid.py --out results/v3-feasibility \
        --seeds 100000..100039 --processes 2
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import multiprocessing as mp
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean, pstdev

import yaml

from harpy.feasibility import PILOT_SEED_FLOOR, arithmetic, assurance
from harpy.ledger import load_pricing
from harpy.metrics import run_metrics
from harpy.simulation import RunSpec, rescale, run_simulation
from harpy.statistics import wilson_interval
from harpy.types import Arm, Severity

SENTINEL_PRICING = {
    "gpt-4.1": "configs/pricing.v3.gpt-4.1.yaml",
    "claude-haiku-4-5": "configs/pricing.v3.claude-haiku-4-5.yaml",
    "claude-haiku-5-5": "configs/pricing.v3.claude-haiku-5-5.yaml",
}
WORKERS = ("gpt-6-luna", "gpt-4.1-mini", "gpt-6.1-sol", "gpt-6-astra")
ARMS = (Arm.RANDOM, Arm.HYBRID, Arm.EXPOSURE)
FOLLOWUPS = (0, 4)
BUDGETS = (0.05,)
#: Above roughly 4 audits per tick every arm audits most of the mesh most ticks;
#: these combinations are skipped as saturated rather than spent compute on.
SKIP = {("gpt-6-astra", "claude-haiku-5-5")}
SEVERITY = Severity.SUBTLE
FIDELITY = 1.0
RESERVE = 0.5
FLEET_SCALE = 100.0
ALERT_CAP = 0.05
REVIEWS = ("fixed", "linear")
FLOOR, CEILING = 0.10, 0.10
SEED_COUNTS = (30, 50, 100, 150, 200, 300, 500)
TARGET = 0.8


def _task(payload: tuple) -> list[dict]:
    config, pricing_path, sentinel, spec = payload
    pricing = load_pricing(pricing_path)
    record = run_simulation(config, spec, pricing)
    rows = []
    for review in REVIEWS:
        row = run_metrics(rescale(record, FLEET_SCALE, review))
        row["sentinel"] = sentinel
        rows.append(row)
    return rows


def _specs(seeds: list[int]) -> list[tuple[str, RunSpec]]:
    out: list[tuple[str, RunSpec]] = []
    for seed in seeds:
        for worker in WORKERS:
            # One TIER0_ONLY baseline per worker and seed: it buys no audits, so
            # the sentinel price cannot touch it.
            out.append(
                ("none", RunSpec(seed, SEVERITY, Arm.TIER0_ONLY, BUDGETS[0], FIDELITY, 0.0,
                                 FLEET_SCALE, ALERT_CAP, worker_model=worker))
            )
            for sentinel in SENTINEL_PRICING:
                if (worker, sentinel) in SKIP:
                    continue
                for arm in ARMS:
                    for k in FOLLOWUPS:
                        for budget in BUDGETS:
                            out.append(
                                (sentinel, RunSpec(
                                    seed, SEVERITY, arm, budget, FIDELITY,
                                    RESERVE if arm is Arm.HYBRID else 0.0, FLEET_SCALE, ALERT_CAP,
                                    worker_model=worker, followup_audits=k,
                                ))
                            )
    return out


def simulate(config: dict, seeds: list[int], out: Path, processes: int) -> None:
    runs = out / "runs.jsonl"
    done: set[tuple] = set()
    if runs.exists():
        for line in runs.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done.add((row["sentinel"], row["run_id"]))
    payloads = []
    for sentinel, spec in _specs(seeds):
        pricing_path = SENTINEL_PRICING.get(sentinel, SENTINEL_PRICING["gpt-4.1"])
        if (sentinel, dataclasses.replace(spec, review_scaling="fixed").run_id()) in done:
            continue
        payloads.append((config, pricing_path, sentinel, spec))
    print(f"{len(payloads)} simulations to run ({len(done) // len(REVIEWS)} already done)",
          flush=True)
    with runs.open("a", encoding="utf-8") as sink, mp.get_context("spawn").Pool(processes) as pool:
        for i, rows in enumerate(pool.imap_unordered(_task, payloads, chunksize=2), 1):
            for row in rows:
                sink.write(json.dumps(row, sort_keys=True) + "\n")
            sink.flush()
            if i % 100 == 0:
                print(f"  {i}/{len(payloads)}", flush=True)


def analyze(config: dict, out: Path) -> None:
    rows = [json.loads(line) for line in (out / "runs.jsonl").read_text().splitlines()]
    base: dict[tuple, int] = {}
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row["arm"] == "TIER0_ONLY":
            base[(row["worker_model"], row["review_scaling"], row["seed"])] = row["detection_rate"]
        else:
            key = (row["sentinel"], row["worker_model"], row["arm"], row["followup_audits"],
                   row["budget_pct"], row["review_scaling"])
            cells[key].append(row)

    table = []
    for key in sorted(cells):
        sentinel, worker, arm, k, budget, review = key
        group = sorted(cells[key], key=lambda r: r["seed"])
        n = len(group)
        c = sum(int(r["detection_rate"]) for r in group)
        b = sum(int(base[(worker, review, r["seed"])]) for r in group)
        overheads = [r["overhead_pct"] for r in group]
        pricing = load_pricing(SENTINEL_PRICING[sentinel])
        spec = RunSpec(0, SEVERITY, Arm(arm), budget, FIDELITY,
                       RESERVE if arm == "HYBRID" else 0.0, FLEET_SCALE, ALERT_CAP,
                       worker_model=worker, review_scaling=review, followup_audits=k)
        math_block = arithmetic(config, pricing, spec, CEILING)
        curve = {}
        if not math_block["criteria_conflict"]:
            for n_study in SEED_COUNTS:
                curve[n_study] = assurance(c, b, n, overheads, n_study, FLOOR, CEILING,
                                           reps=1000, seed=n_study)
        enough = [m for m, a in curve.items() if a >= TARGET]
        verdict = (
            "CRITERIA_CONFLICT" if math_block["criteria_conflict"]
            else "FREEZABLE" if enough else "NOT_FREEZABLE"
        )
        table.append({
            "sentinel": sentinel,
            "worker_model": worker,
            "arm": arm,
            "followup_audits": k,
            "budget_pct": budget,
            "review_scaling": review,
            "affordable_audits_per_tick": round(math_block["affordable_audits_per_tick"], 4),
            "audit_cost_in_worker_messages": round(math_block["audit_cost_in_worker_messages"], 2),
            "triage_share_one_detection": round(
                math_block["one_detection_triage_share_of_worker_cost"], 4),
            "pilot_n": n,
            "detection_rate": c / n,
            "detection_ci95": wilson_interval(c, n),
            "baseline_rate": b / n,
            "overhead_mean": fmean(overheads),
            "overhead_sd": pstdev(overheads),
            "false_isolations_mean": fmean(r["false_isolations"] for r in group),
            "audits_on_faulty_agent_mean": fmean(r["audits_on_faulty_agent"] for r in group),
            "assurance": {str(m): round(a, 3) for m, a in curve.items()},
            "verdict": verdict,
            "seeds_needed": min(enough) if enough else None,
        })
    (out / "grid.json").write_text(json.dumps({
        "schema": "harpy/v3-feasibility-grid/1",
        "evidence_kind": "pre-freeze pilot; not study evidence",
        "pilot_seeds": sorted({r["seed"] for r in rows}),
        "decision": {"floor": FLOOR, "ceiling": CEILING, "target_assurance": TARGET},
        "fixed": {"severity": SEVERITY.value, "lineage_fidelity": FIDELITY,
                  "reserve_fraction": RESERVE, "fleet_scale": FLEET_SCALE,
                  "max_alerts_per_agent_hour": ALERT_CAP},
        "cells": table,
    }, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    with (out / "grid.csv").open("w", newline="", encoding="utf-8") as handle:
        flat = [{**row, "detection_ci95": row["detection_ci95"],
                 "assurance": json.dumps(row["assurance"])} for row in table]
        writer = csv.DictWriter(handle, fieldnames=list(flat[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(flat)
    print(f"wrote {out / 'grid.json'} ({len(table)} cells)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/v3.yaml")
    parser.add_argument("--out", default="results/v3-feasibility")
    parser.add_argument("--seeds", default="100000..100039")
    parser.add_argument("--processes", type=int, default=2)
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()
    low, high = (int(x) for x in args.seeds.split(".."))
    if low < PILOT_SEED_FLOOR:
        print(f"pilot seeds must be >= {PILOT_SEED_FLOOR}", file=sys.stderr)
        return 2
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not args.analyze_only:
        simulate(config, list(range(low, high + 1)), out, args.processes)
    analyze(config, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
