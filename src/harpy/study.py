"""A frozen, reproducible sensitivity study with a designated primary test."""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path
from statistics import fmean

import yaml

from .ledger import load_pricing
from .metrics import BASELINE_MATCH_KEYS, attach_incremental_detection_over_tier0
from .provenance import digest, file_digest, write_manifest
from .statistics import bootstrap_mean_interval, difference_interval, wilson_interval
from .sweep import SweepGrid, load_result_rows, run_sweep
from .types import Arm, Severity

CELL_KEYS = tuple(k for k in BASELINE_MATCH_KEYS if k != "seed") + ("arm", "reserve_fraction")


def evaluate_cell(candidate: list[dict], baselines: dict, decision: dict) -> dict:
    ordered = sorted(candidate, key=lambda row: row["seed"])
    matched = [baselines.get(tuple(r.get(k) for k in BASELINE_MATCH_KEYS)) for r in ordered]
    identity = {k: ordered[0].get(k) for k in CELL_KEYS}
    if any(row is None for row in matched):
        return {**identity, "decision": "INCOMPLETE", "reason": "missing matched baseline"}
    seeds = [row["seed"] for row in ordered]
    if len(set(seeds)) != len(seeds):
        raise ValueError("duplicate seeds in a study cell")
    observed = [int(r["detection_rate"]) for r in ordered]
    baseline = [int(r["detection_rate"]) for r in matched]
    delta = [a - b for a, b in zip(observed, baseline, strict=True)]
    overhead = [r["overhead_pct"] for r in ordered]
    interval = difference_interval(observed, baseline)
    resample_seed = int(digest(identity)[:16], 16)
    samples = int(decision["bootstrap_samples"])
    cost_interval = bootstrap_mean_interval(overhead, resample_seed, samples)
    floor, ceiling = decision["minimum_incremental_detection"], decision["maximum_overhead"]
    if len(ordered) < decision["minimum_seeds"]:
        verdict = "INCOMPLETE"
    elif interval[0] >= floor and cost_interval[1] < ceiling:
        verdict = "PASS"
    elif interval[1] < floor or cost_interval[0] >= ceiling:
        verdict = "FAIL"
    else:
        verdict = "INCONCLUSIVE"
    return {
        **identity,
        "n_seeds": len(ordered),
        "decision": verdict,
        "detection_rate": fmean(observed),
        "detection_ci95": wilson_interval(sum(observed), len(observed)),
        "baseline_detection_rate": fmean(baseline),
        "incremental_detection": fmean(delta),
        "incremental_detection_ci95": interval,
        "paired_bootstrap_delta_ci95": bootstrap_mean_interval(delta, resample_seed, samples),
        "overhead_pct": fmean(overhead),
        "overhead_ci95": cost_interval,
        "mean_false_isolations": fmean(r["false_isolations"] for r in ordered),
        "mean_alerts_per_agent_hour": fmean(r["alerts_per_agent_hour"] for r in ordered),
    }


def analyze_rows(rows: list[dict], study: dict) -> dict:
    attach_incremental_detection_over_tier0(rows)
    baselines, cells = {}, defaultdict(list)
    for row in rows:
        if row["arm"] == "TIER0_ONLY":
            key = tuple(row.get(k) for k in BASELINE_MATCH_KEYS)
            if key in baselines:
                raise ValueError("duplicate matched baseline")
            baselines[key] = row
        if row["arm"] in ("RANDOM", "DIRECTED", "HYBRID"):
            cells[tuple(row.get(k) for k in CELL_KEYS)].append(row)
    estimates = [
        evaluate_cell(group, baselines, study["decision"])
        for _, group in sorted(cells.items(), key=lambda item: str(item[0]))
    ]
    primary = [
        cell
        for cell in estimates
        if all(cell.get(k) == v for k, v in study["primary_cell"].items())
    ]
    if len(primary) != 1:
        raise ValueError("study must contain exactly one designated primary cell")
    return {
        "schema": "harpy/study-report/2",
        "study_id": study["study_id"],
        "evidence_kind": "conditional_simulation",
        "real_detector_evaluated": False,
        "study": study,
        "study_sha256": digest(study),
        "primary": primary[0],
        "exploratory_cells": estimates,
        "uncertainty": "Conservative nominal 95% differences from two 97.5% Wilson intervals; "
        "paired bootstrap effects and costs. "
        "Only the preregistered primary cell is confirmatory; exploratory "
        "cells have no simultaneous-coverage claim.",
        "n_run_records": len(rows),
        "experiment_fingerprints": sorted({r["experiment_fingerprint"] for r in rows}),
    }


def run_study(study_path: str | Path, out_dir: str | Path, processes: int = 2) -> Path:
    study_path, out_dir = Path(study_path), Path(out_dir)
    study = yaml.safe_load(study_path.read_text(encoding="utf-8"))
    if study.get("schema") != "harpy/study/2":
        raise ValueError("unsupported study specification")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("use an empty output directory to preserve the frozen study")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Persist the design and raw YAML identity BEFORE collecting any observations.
    frozen = {"specification": study, "yaml_sha256": file_digest(study_path)}
    (out_dir / "preregistration.json").write_text(
        json.dumps(frozen, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    grid_cfg = dict(study["grid"])
    start, end = grid_cfg.pop("seeds")
    grid_cfg["seeds"] = tuple(range(start, end + 1))
    grid_cfg["arms"] = tuple(Arm(v) for v in grid_cfg["arms"])
    grid_cfg["severities"] = tuple(Severity(v) for v in grid_cfg["severities"])
    grid = SweepGrid(**{k: tuple(v) for k, v in grid_cfg.items()})
    root = study_path.resolve().parent.parent
    config_path = root / study["simulation_config"]
    pricing_path = root / study["pricing"]
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    run_sweep(config, load_pricing(pricing_path), out_dir / "runs", grid, processes)
    rows = load_result_rows(out_dir / "runs")
    report = analyze_rows(rows, study)
    report["preregistration_yaml_sha256"] = frozen["yaml_sha256"]
    with (out_dir / "seed-metrics.json.gz").open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(json.dumps(rows, sort_keys=True).encode())
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_manifest(out_dir)
    return path
