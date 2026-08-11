"""Scoring. Every metric is per run and carries its severity with it.

Averaging across severity is the single easiest way to make this architecture
look better than it is: OVERT faults are cheap to catch and would drag a blended
number upwards while saying nothing about the SUBTLE case that motivates having
a sentinel at all. So severity is a key, never an axis to collapse.
"""

from __future__ import annotations

import statistics

from .simulation import RunRecord

SECONDS_PER_HOUR = 3600.0


def detection_rate(record: RunRecord) -> float:
    """0 or 1 for a single run; a rate once averaged over seeds within a cell."""
    return 1.0 if record.detected_tick is not None else 0.0


def false_isolations_per_agent_hour(record: RunRecord) -> float:
    """Isolations of agents that were not the fault source, per agent-hour.

    Note the definition: an agent that is merely relaying corrupt claims counts
    as a false isolation, because isolating it does not stop the fault and still
    costs a discard, a rerun, and a human review. That is the pessimistic
    reading for HARPY, which is the right direction for a metric this
    architecture is being judged on.
    """
    false_count = sum(1 for event in record.isolations if not event.was_faulty)
    agent_hours = record.agent_active_ticks * record.tick_seconds / SECONDS_PER_HOUR
    if agent_hours <= 0.0:
        return 0.0
    return false_count / agent_hours


def median_interactions_to_detection(record: RunRecord) -> float | None:
    """Messages the faulty agent got to send between onset and isolation.

    One run has at most one faulty agent, so the per-run median is that single
    observation; the median that matters is taken across seeds in
    :func:`aggregate`. ``None`` means the fault was never caught — deliberately
    not zero and not the run length, both of which would silently become data.
    """
    values = [
        event.interactions_since_onset for event in record.isolations if event.was_faulty
    ]
    if not values:
        return None
    return float(statistics.median(values))


def contaminated_claims_at_isolation(record: RunRecord) -> int | None:
    """Corrupt claims in existence when the fault was contained (ground truth)."""
    return record.contaminated_at_isolation


def overhead_pct(record: RunRecord) -> float:
    return float(record.ledger_dict.get("overhead_pct", 0.0))


def run_metrics(record: RunRecord) -> dict:
    """The full per-run metric block, with all six cost components broken out."""
    ledger = record.ledger_dict
    return {
        "run_id": record.spec.run_id(),
        **record.spec.as_dict(),
        "detection_rate": detection_rate(record),
        "detected_tick": record.detected_tick,
        "false_isolations": sum(1 for e in record.isolations if not e.was_faulty),
        "false_isolations_per_agent_hour": false_isolations_per_agent_hour(record),
        "median_interactions_to_detection": median_interactions_to_detection(record),
        "contaminated_claims_at_isolation": contaminated_claims_at_isolation(record),
        "corrupt_claims_total": record.corrupt_claims_total,
        "total_claims": record.total_claims,
        "claims_flagged_downstream": len(record.flagged_claim_ids),
        "audits_run": record.audits_run,
        "tier0_messages": record.tier0_messages,
        "isolations_total": len(record.isolations),
        "fault_onset_tick": record.fault_onset_tick,
        # Six cost components, never rolled into one number.
        "worker_dollars": ledger["worker_dollars"],
        "sentinel_dollars": ledger["sentinel_dollars"],
        "tier0_dollars": ledger["tier0_dollars"],
        "discarded_work_dollars": ledger["discarded_work_dollars"],
        "rerun_work_dollars": ledger["rerun_work_dollars"],
        "human_review_dollars": ledger["human_review_dollars"],
        "incremental_dollars": ledger["incremental_dollars"],
        "overhead_pct": ledger["overhead_pct"],
        # Echoed so a result can always be read against what produced it.
        "fault_distribution": dict(sorted(record.fault_distribution.items())),
        "lineage_fidelity": record.spec.lineage_fidelity,
        "pricing_verified": bool(record.pricing_dict.get("verified", False)),
    }


def result_document(record: RunRecord) -> dict:
    """The full results JSON for one run."""
    return {
        "schema": "harpy-sim/run/1",
        "spec": record.spec.as_dict(),
        "metrics": run_metrics(record),
        "ledger": record.ledger_dict,
        "pricing": record.pricing_dict,
        "world": {
            "n_agents": record.n_agents,
            "n_ticks": record.n_ticks,
            "tick_seconds": record.tick_seconds,
            "faulty_agent_id": record.faulty_agent_id,
            "fault_onset_tick": record.fault_onset_tick,
            "fault_distribution": dict(sorted(record.fault_distribution.items())),
        },
        "isolations": [
            {
                "tick": event.tick,
                "agent_id": event.agent_id,
                "was_faulty": event.was_faulty,
                "suspicion": event.suspicion,
                "contaminated_claims": event.contaminated_claims,
                "interactions_since_onset": event.interactions_since_onset,
            }
            for event in record.isolations
        ],
    }


def aggregate(rows: list[dict]) -> list[dict]:
    """Collapse seeds within each (arm, severity, budget, fidelity, reserve) cell.

    Seeds are the only axis collapsed here. Severity never is.
    """
    cells: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (
            row["arm"],
            row["severity"],
            row["budget_pct"],
            row["lineage_fidelity"],
            row["reserve_fraction"],
        )
        cells.setdefault(key, []).append(row)

    out: list[dict] = []
    for key, group in sorted(cells.items(), key=lambda item: tuple(str(p) for p in item[0])):
        arm, severity, budget, fidelity, reserve = key
        detections = [r["median_interactions_to_detection"] for r in group]
        detections = [d for d in detections if d is not None]
        contaminated = [
            r["contaminated_claims_at_isolation"]
            for r in group
            if r["contaminated_claims_at_isolation"] is not None
        ]
        out.append(
            {
                "arm": arm,
                "severity": severity,
                "budget_pct": budget,
                "lineage_fidelity": fidelity,
                "reserve_fraction": reserve,
                "n_seeds": len(group),
                "detection_rate": statistics.fmean(r["detection_rate"] for r in group),
                "overhead_pct": statistics.fmean(r["overhead_pct"] for r in group),
                "false_isolations_per_agent_hour": statistics.fmean(
                    r["false_isolations_per_agent_hour"] for r in group
                ),
                "median_interactions_to_detection": (
                    statistics.median(detections) if detections else None
                ),
                "median_contaminated_claims_at_isolation": (
                    statistics.median(contaminated) if contaminated else None
                ),
                "mean_sentinel_dollars": statistics.fmean(r["sentinel_dollars"] for r in group),
                "mean_tier0_dollars": statistics.fmean(r["tier0_dollars"] for r in group),
                "mean_discarded_work_dollars": statistics.fmean(
                    r["discarded_work_dollars"] for r in group
                ),
                "mean_rerun_work_dollars": statistics.fmean(
                    r["rerun_work_dollars"] for r in group
                ),
                "mean_human_review_dollars": statistics.fmean(
                    r["human_review_dollars"] for r in group
                ),
                "mean_worker_dollars": statistics.fmean(r["worker_dollars"] for r in group),
                "mean_audits_run": statistics.fmean(r["audits_run"] for r in group),
                "pricing_verified": all(r["pricing_verified"] for r in group),
            }
        )
    return out
