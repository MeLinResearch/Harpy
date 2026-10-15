"""Can a study cell pass? Answer that before freezing it, not after.

v2 froze a primary cell that could not pass. Two separate reasons, both
knowable in advance:

* **Its two success criteria contradicted each other.** Under linear review
  scaling every detection opens a human review, and in the 30-agent slice one
  3-minute triage is about 20% of gpt-4.1-mini worker spend. A cell that must
  detect the fault *and* stay under 10% overhead cannot do both.
* **Its detection rate was capped far below the floor.** At 5% budget the
  sentinel could afford about one audit per 12 ticks across 30 agents, and the
  isolation gate needs roughly three flagged audits on one agent close together.

This module runs two checks, in order, before any study outcome exists:

1. **Arithmetic, no simulation.** The audit rate the budget can afford, and the
   cost floor of a single detection under the cell's review policy. If one
   detection's triage alone reaches the cost ceiling, the cell is declared
   ``CRITERIA_CONFLICT`` without simulating anything.
2. **A disclosed pilot.** The candidate cell and its matched TIER0_ONLY baseline
   are run on pilot seeds that must be disjoint from every study seed
   (``>= PILOT_SEED_FLOOR``). The study's frozen decision rule is then replayed
   on synthetic outcomes drawn from the pilot's estimates, at each candidate seed
   count, giving the probability the study would return PASS (its assurance).
   Pilot uncertainty is carried through with Jeffreys draws rather than plugging
   in point estimates.

The pilot is a design input, not evidence. Its seeds are never study seeds, and
nothing here may change a threshold, a prior, or the world: only how many seeds
a study needs, and whether it should be frozen at all.
"""

from __future__ import annotations

import dataclasses
import math
import multiprocessing as mp
from statistics import fmean, pstdev

import numpy as np

from .ledger import HumanReviewConfig, Pricing
from .metrics import run_metrics
from .provenance import capture_inputs, digest
from .simulation import RunSpec, rescale, run_simulation
from .statistics import wilson_interval
from .types import Arm

#: Pilot seeds start here. Studies draw from small seed ranges (v2 used 0-29),
#: so a pilot below this floor is refused rather than trusted to be disjoint.
PILOT_SEED_FLOOR = 100_000
DEFAULT_SEED_COUNTS: tuple[int, ...] = (30, 50, 100, 150, 200, 300, 500)
#: Matches harpy.statistics.difference_interval: two 97.5% Wilson marginals.
_MARGINAL_CONFIDENCE = 0.975
_Z95 = 1.959963984540054


def baseline_spec(spec: RunSpec) -> RunSpec:
    """The matched TIER0_ONLY run: same world, no audits, nothing to follow up."""
    return dataclasses.replace(spec, arm=Arm.TIER0_ONLY, reserve_fraction=0.0, followup_audits=0)


# -- 1. arithmetic -----------------------------------------------------------


def arithmetic(config: dict, pricing: Pricing, spec: RunSpec, ceiling: float) -> dict:
    """What the budget buys and what one detection costs. No simulation.

    Uses expected token counts from the config, so these are expectations: the
    simulation draws output lengths per message. Tier 0's per-message charge is
    included even though it is negligible at every shipped price.
    """
    sim = config["simulation"]
    worker_cfg = config["worker"]
    sentinel_cfg = config["sentinel"]
    n_agents = int(sim["n_agents"])
    n_ticks = int(sim["n_ticks"])

    worker = pricing.worker_price(spec.worker_model)
    per_message = (
        float(worker_cfg["input_tokens_per_message"]) * worker.input_per_mtok
        + float(worker_cfg["output_tokens_mean"]) * worker.output_per_mtok
    ) / 1_000_000
    worker_per_tick = per_message * n_agents
    tier0_per_tick = pricing.tier0_cost_per_message_dollars * n_agents
    allowance_per_tick = max(spec.budget_pct * worker_per_tick - tier0_per_tick, 0.0)

    sentinel = pricing.sentinel_model
    audit_cost = (
        float(sentinel_cfg["input_tokens"]) * sentinel.input_per_mtok
        + float(sentinel_cfg["output_tokens"]) * sentinel.output_per_mtok
    ) / 1_000_000
    audits_per_tick = (
        min(allowance_per_tick / audit_cost, float(n_agents)) if audit_cost > 0 else float(n_agents)
    )
    onset_mean = (
        float(sim["fault_onset_fraction_min"]) + float(sim["fault_onset_fraction_max"])
    ) / 2
    faulty_ticks = n_ticks * (1.0 - onset_mean)

    review = HumanReviewConfig.from_config(config)
    worker_run = worker_per_tick * n_ticks
    # One detection = one alert = one review, when alerts fire on isolation.
    alerts = bool(config["response"]["alert_on_isolation"])
    scale_divisor = 1.0 if spec.review_scaling == "linear" else float(spec.fleet_scale)
    triage_share = (
        review.triage_dollars() / (worker_run * scale_divisor) if alerts and worker_run else 0.0
    )
    expected_review_share = (
        review.expected_dollars_per_review() / (worker_run * scale_divisor)
        if alerts and worker_run
        else 0.0
    )
    return {
        "worker_dollars_per_message": per_message,
        "worker_dollars_per_tick": worker_per_tick,
        "allowance_dollars_per_tick": allowance_per_tick,
        "audit_cost_dollars": audit_cost,
        "audit_cost_in_worker_messages": audit_cost / per_message if per_message else None,
        "affordable_audits_per_tick": audits_per_tick,
        "expected_faulty_ticks": faulty_ticks,
        "expected_audits_on_faulty_agent_if_uniform": audits_per_tick * faulty_ticks / n_agents,
        "one_detection_triage_share_of_worker_cost": triage_share,
        "one_detection_expected_review_share_of_worker_cost": expected_review_share,
        "criteria_conflict": triage_share >= ceiling,
    }


# -- 2. pilot ----------------------------------------------------------------


def _pilot_worker(payload: tuple) -> tuple[int, dict, dict]:
    config, spec, pricing = payload
    candidate = run_simulation(config, spec, pricing)
    base = run_simulation(config, baseline_spec(spec), pricing)
    return (
        spec.seed,
        run_metrics(rescale(candidate, spec.fleet_scale, spec.review_scaling)),
        run_metrics(rescale(base, spec.fleet_scale, spec.review_scaling)),
    )


def run_pilot(
    config: dict,
    pricing: Pricing,
    spec: RunSpec,
    seeds: tuple[int, ...],
    processes: int = 1,
    forbidden_seeds: frozenset[int] = frozenset(),
) -> list[tuple[int, dict, dict]]:
    """Candidate and matched baseline on each pilot seed, in seed order."""
    if not seeds:
        raise ValueError("a pilot needs at least one seed")
    low = min(seeds)
    if low < PILOT_SEED_FLOOR:
        raise ValueError(
            f"pilot seeds must be >= {PILOT_SEED_FLOOR} so they cannot be study seeds; got {low}"
        )
    overlap = forbidden_seeds.intersection(seeds)
    if overlap:
        raise ValueError(f"pilot seeds overlap study seeds: {sorted(overlap)[:5]}")
    payloads = [(config, dataclasses.replace(spec, seed=seed), pricing) for seed in seeds]
    if processes <= 1:
        results = [_pilot_worker(p) for p in payloads]
    else:
        with mp.get_context("spawn").Pool(processes) as pool:
            results = pool.map(_pilot_worker, payloads, chunksize=2)
    return sorted(results, key=lambda item: item[0])


# -- 3. the frozen rule, replayed ---------------------------------------------


def _passes(c: int, b: int, n: int, overheads: np.ndarray, floor: float, ceiling: float) -> bool:
    left = wilson_interval(c, n, _MARGINAL_CONFIDENCE)
    right = wilson_interval(b, n, _MARGINAL_CONFIDENCE)
    if left[0] - right[1] < floor:
        return False
    mean = float(overheads.mean())
    sd = float(overheads.std())
    # Normal approximation to the study's bootstrap interval on mean overhead.
    return mean + _Z95 * sd / math.sqrt(n) < ceiling


def assurance(
    candidate_successes: int,
    baseline_successes: int,
    n_pilot: int,
    overheads: list[float],
    n_study: int,
    floor: float,
    ceiling: float,
    reps: int = 2000,
    seed: int = 0,
    propagate_pilot_uncertainty: bool = True,
) -> float:
    """Probability the frozen rule returns PASS at ``n_study`` seeds.

    With ``propagate_pilot_uncertainty`` the true rates are redrawn each
    replicate from the Jeffreys posterior given the pilot (assurance); without
    it they are fixed at the pilot point estimates (conventional power). The
    cost side resamples pilot overheads, which is the study's own bootstrap
    applied to the pilot's distribution.
    """
    rng = np.random.default_rng(seed)
    pool = np.asarray(overheads, dtype=float)
    passes = 0
    for _ in range(reps):
        if propagate_pilot_uncertainty:
            p_c = rng.beta(candidate_successes + 0.5, n_pilot - candidate_successes + 0.5)
            p_b = rng.beta(baseline_successes + 0.5, n_pilot - baseline_successes + 0.5)
        else:
            p_c = candidate_successes / n_pilot
            p_b = baseline_successes / n_pilot
        c = int(rng.binomial(n_study, p_c))
        b = int(rng.binomial(n_study, p_b))
        drawn = rng.choice(pool, size=n_study, replace=True)
        passes += _passes(c, b, n_study, drawn, floor, ceiling)
    return passes / reps


def evaluate(
    config: dict,
    pricing: Pricing,
    spec: RunSpec,
    decision: dict,
    pilot_seeds: tuple[int, ...],
    seed_counts: tuple[int, ...] = DEFAULT_SEED_COUNTS,
    target: float = 0.8,
    processes: int = 1,
    forbidden_seeds: frozenset[int] = frozenset(),
    reps: int = 2000,
) -> dict:
    """Arithmetic, then pilot, then assurance at each seed count. One report."""
    floor = float(decision["minimum_incremental_detection"])
    ceiling = float(decision["maximum_overhead"])
    minimum_seeds = int(decision.get("minimum_seeds", 0))
    report: dict = {
        "schema": "harpy/feasibility/1",
        "evidence_kind": "pre-freeze pilot; not study evidence",
        "spec": spec.as_dict(),
        "decision": {"floor": floor, "ceiling": ceiling, "minimum_seeds": minimum_seeds},
        "target_assurance": target,
        "inputs": {
            k: v
            for k, v in capture_inputs(config, pricing.as_dict()).items()
            if k.endswith("sha256") or k == "experiment_fingerprint"
        },
    }
    report["arithmetic"] = math_block = arithmetic(config, pricing, spec, ceiling)
    if math_block["criteria_conflict"]:
        report["verdict"] = "CRITERIA_CONFLICT"
        report["reason"] = (
            "One detection's triage alone is at or above the cost ceiling under this review "
            "policy, so the detection floor and the cost ceiling cannot both be met. No pilot run."
        )
        report["report_sha256"] = digest(report)
        return report

    rows = run_pilot(config, pricing, spec, pilot_seeds, processes, forbidden_seeds)
    candidate = [int(r[1]["detection_rate"]) for r in rows]
    baseline = [int(r[2]["detection_rate"]) for r in rows]
    overheads = [float(r[1]["overhead_pct"]) for r in rows]
    n = len(rows)
    c, b = sum(candidate), sum(baseline)
    report["pilot"] = {
        "seeds": [min(pilot_seeds), max(pilot_seeds)],
        "n": n,
        "candidate_detections": c,
        "candidate_detection_ci95": wilson_interval(c, n),
        "baseline_detections": b,
        "baseline_detection_ci95": wilson_interval(b, n),
        "overhead_mean": fmean(overheads),
        "overhead_sd": pstdev(overheads),
        "overhead_max": max(overheads),
        "false_isolations_mean": fmean(r[1]["false_isolations"] for r in rows),
        "followup_audits_mean": fmean(r[1]["followup_audits_drawn"] for r in rows),
        "audits_on_faulty_agent_mean": fmean(r[1]["audits_on_faulty_agent"] for r in rows),
        "affordable_audits_per_tick": fmean(
            r[1]["affordable_audits_per_tick"] or 0.0 for r in rows
        ),
    }
    curve = []
    for n_study in sorted(seed_counts):
        if n_study < minimum_seeds:
            continue
        curve.append(
            {
                "n_seeds": n_study,
                "assurance": assurance(c, b, n, overheads, n_study, floor, ceiling, reps, n_study),
                "power_at_point_estimate": assurance(
                    c, b, n, overheads, n_study, floor, ceiling, reps, n_study,
                    propagate_pilot_uncertainty=False,
                ),
            }
        )
    report["curve"] = curve
    enough = [point["n_seeds"] for point in curve if point["assurance"] >= target]
    if enough:
        report["verdict"] = "FREEZABLE"
        report["recommended_seeds"] = min(enough)
        report["reason"] = f"assurance >= {target:g} at {min(enough)} seeds"
    else:
        report["verdict"] = "NOT_FREEZABLE"
        best = max(curve, key=lambda point: point["assurance"]) if curve else None
        report["reason"] = (
            f"assurance below {target:g} at every seed count tried"
            + (f" (best {best['assurance']:.2f} at {best['n_seeds']})" if best else "")
        )
    report["report_sha256"] = digest(report)
    return report
