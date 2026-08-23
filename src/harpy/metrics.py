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

#: The preregistered kill ceiling: incremental oversight cost as a fraction of
#: worker-fleet cost. Lives here because it is scoring, not configuration — a
#: kill condition you can edit in YAML is not a kill condition.
KILL_CEILING_PCT = 0.10


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


def audit_coverage_of_faulty_agent(record: RunRecord) -> float | None:
    """Share of exposure-window audits that were spent on the actual fault source.

    The denominator is every audit inside the fault's exposure window — from
    onset until the faulty agent is isolated, or to the end of the run if it
    never is. Audits outside that window are excluded in both directions and for
    the same reason: they could not have found the fault. Before onset there was
    nothing to find; after isolation the agent is out of the mesh, so every
    remaining audit lands on a clean agent by construction. See
    :func:`~harpy.simulation.audit_counts_toward_exposure`.

    A denominator of "all audits after onset" would penalise exactly the arms
    that work — the faster an arm contains the fault, the more post-isolation
    audits inflate its denominator — so effective targeting would be reported as
    bad targeting.

    ``None`` when no audit was drawn inside the window — a run that never looked
    has no coverage, which is a different statement from covering 0% of what it
    looked at. This is the number that separates the two failure modes:
    near-zero coverage with a perfect detector is a targeting failure, while high
    coverage with no detections is a detection failure.
    """
    if record.audits_during_fault_exposure <= 0:
        return None
    return record.audits_on_faulty_agent / record.audits_during_fault_exposure


def ticks_faulty_before_first_audit(record: RunRecord) -> int | None:
    """Ticks the fault ran before the sentinel first looked at its source.

    ``None`` means it was never audited while faulty. Not zero, and not the run
    length: both would quietly turn "never looked" into a measurement.
    """
    first = record.first_audit_tick_on_faulty_agent
    if first is None:
        return None
    return first - record.fault_onset_tick


def false_isolation_count(record: RunRecord) -> int:
    """Isolations of agents that were not the fault source. See the note in
    :func:`false_isolations_per_agent_hour` on why relays count as false."""
    return sum(1 for event in record.isolations if not event.was_faulty)


def overhead_pct(record: RunRecord) -> float:
    return record.ledger.overhead_pct()


def run_hours(record: RunRecord) -> float:
    return record.n_ticks * record.tick_seconds / SECONDS_PER_HOUR


def agent_hours(record: RunRecord) -> float:
    return record.agent_active_ticks * record.tick_seconds / SECONDS_PER_HOUR


def alerts_per_agent_hour(record: RunRecord) -> float:
    hours = agent_hours(record)
    if hours <= 0.0:
        return 0.0
    return record.alerts_fired / hours


def min_fleet_dollars_per_hour_for_viability(record: RunRecord) -> float | None:
    """Smallest fleet spend, in worker dollars per hour, that clears the ceiling.

    At fleet scale ``s`` the overhead ratio is ``(s*A + R) / (s*W)``, where ``A``
    is the slice's per-agent oversight cost, ``R`` its review cost and ``W`` its
    worker spend. Review cost does not scale, so the ratio falls monotonically in
    ``s`` towards ``A/W`` — the smallest viable ``s`` is therefore
    ``R / (ceiling*W - A)`` whenever ``ceiling*W > A``, and there is no viable
    scale at all otherwise. Reported as fleet worker dollars per hour rather than
    as the scale itself, because the scale is only meaningful against the size of
    the slice that produced it.

    ``None`` means no fleet clears the ceiling: the run's scale-invariant
    oversight cost is already at or over it, and buying more agents cannot help.
    ``0.0`` means the run had no review cost to amortise and clears at any scale.
    """
    ledger = record.ledger
    worker = ledger.worker_dollars
    hours = run_hours(record)
    if worker <= 0.0 or hours <= 0.0:
        return None
    headroom = KILL_CEILING_PCT * worker - ledger.per_agent_overhead_dollars()
    if headroom <= 0.0:
        return None
    min_scale = ledger.review_dollars() / headroom
    return min_scale * worker / hours


def run_metrics(record: RunRecord) -> dict:
    """The full per-run metric block, with all six cost components broken out."""
    ledger = record.ledger.as_dict()
    return {
        "run_id": record.spec.run_id(),
        **record.spec.as_dict(),
        "detection_rate": detection_rate(record),
        "detected_tick": record.detected_tick,
        "false_isolations": false_isolation_count(record),
        "false_isolation_count": false_isolation_count(record),
        "false_isolations_per_agent_hour": false_isolations_per_agent_hour(record),
        # -- audit targeting ------------------------------------------------
        "audits_on_faulty_agent": record.audits_on_faulty_agent,
        "audits_on_faulty_agent_all_ticks": record.audits_on_faulty_agent_all_ticks,
        "audits_during_fault_exposure": record.audits_during_fault_exposure,
        "audit_coverage_of_faulty_agent": audit_coverage_of_faulty_agent(record),
        "ticks_faulty_before_first_audit": ticks_faulty_before_first_audit(record),
        "median_interactions_to_detection": median_interactions_to_detection(record),
        "contaminated_claims_at_isolation": contaminated_claims_at_isolation(record),
        "corrupt_claims_total": record.corrupt_claims_total,
        "total_claims": record.total_claims,
        "claims_flagged_downstream": len(record.flagged_claim_ids),
        "audits_run": record.audits_run,
        "tier0_messages": record.tier0_messages,
        "isolations_total": len(record.isolations),
        "fault_onset_tick": record.fault_onset_tick,
        # Six oversight cost components, never rolled into one number, plus the
        # worker spend they are a ratio of. All at fleet scale.
        "worker_dollars": ledger["worker_dollars"],
        "sentinel_dollars": ledger["sentinel_dollars"],
        "tier0_dollars": ledger["tier0_dollars"],
        "discarded_work_dollars": ledger["discarded_work_dollars"],
        "rerun_work_dollars": ledger["rerun_work_dollars"],
        # The same two components split by whether the isolation was of the
        # actual fault source. Parts of the totals above, never added to them.
        "discarded_work_dollars_from_false_isolations": ledger[
            "discarded_work_dollars_from_false_isolations"
        ],
        "rerun_work_dollars_from_false_isolations": ledger[
            "rerun_work_dollars_from_false_isolations"
        ],
        "discarded_work_dollars_from_true_isolations": ledger[
            "discarded_work_dollars_from_true_isolations"
        ],
        "rerun_work_dollars_from_true_isolations": ledger[
            "rerun_work_dollars_from_true_isolations"
        ],
        "discard_rerun_dollars_from_false_isolations": ledger[
            "discard_rerun_dollars_from_false_isolations"
        ],
        "discard_rerun_dollars_from_true_isolations": ledger[
            "discard_rerun_dollars_from_true_isolations"
        ],
        "triage_dollars": ledger["triage_dollars"],
        "investigation_dollars": ledger["investigation_dollars"],
        "incremental_dollars": ledger["incremental_dollars"],
        "overhead_pct": ledger["overhead_pct"],
        # Emitted in every results JSON: at scale 1 this is most of the
        # overhead, and at scale 1000 it is a rounding error.
        "review_cost_share_of_overhead": ledger["review_cost_share_of_overhead"],
        "min_fleet_dollars_per_hour_for_viability": (
            min_fleet_dollars_per_hour_for_viability(record)
        ),
        # -- the second budget ---------------------------------------------
        "alerts_fired": record.alerts_fired,
        "alerts_per_agent_hour": alerts_per_agent_hour(record),
        "suppressed_alerts": record.suppressed_alerts,
        "detections_lost_to_alert_cap": record.detections_lost_to_alert_cap,
        "effective_isolation_threshold": record.effective_isolation_threshold,
        "base_isolation_threshold": record.base_isolation_threshold,
        "reviews_opened": record.reviews_opened,
        "alerts_batched_into_open_reviews": record.alerts_batched,
        "escalated_reviews": record.escalated_reviews,
        # -- detector characteristics and price, as actually run -------------
        # The spec fields may be null ("use the shipped operating point"); these
        # are what the run consumed, so a row is readable without the config.
        "effective_detection_prob": record.effective_detection_prob,
        "effective_false_positive_rate": record.effective_false_positive_rate,
        "worker_model": record.worker_model_name,
        "worker_model_input_per_mtok": record.worker_model_price.get("input_per_mtok"),
        "worker_model_output_per_mtok": record.worker_model_price.get("output_per_mtok"),
        # Cross-run quantity: a single run cannot know its own baseline. Filled
        # by attach_incremental_detection_over_tier0 once the matched TIER0_ONLY
        # run exists; null on a run JSON read in isolation. See that function.
        "incremental_detection_over_tier0_only": None,
        # Echoed so a result can always be read against what produced it.
        "fault_distribution": dict(sorted(record.fault_distribution.items())),
        "lineage_fidelity": record.spec.lineage_fidelity,
        "pricing_verified": bool(record.pricing_dict.get("verified", False)),
    }


#: Everything that must match for two runs to be the same world under a
#: different arm. ``reserve_fraction`` is deliberately absent: it is a
#: HYBRID-only knob, so TIER0_ONLY exists only at 0.0 and keying on it would
#: leave every HYBRID cell without a baseline to subtract.
BASELINE_MATCH_KEYS: tuple[str, ...] = (
    "seed",
    "severity",
    "budget_pct",
    "lineage_fidelity",
    "fleet_scale",
    "max_alerts_per_agent_hour",
    "effective_detection_prob",
    "effective_false_positive_rate",
    "worker_model",
)

BASELINE_ARM = "TIER0_ONLY"


def _baseline_key(row: dict) -> tuple:
    return tuple(row.get(name) for name in BASELINE_MATCH_KEYS)


def max_false_positive_rate_for_viability(
    rows: list[dict], ceiling_pct: float = KILL_CEILING_PCT
) -> dict[str, dict]:
    """Largest detector FP rate at which some arm still earns its keep, per severity.

    "Earns its keep" is both halves of the preregistered condition at once:
    strictly positive incremental detection over the matched TIER0_ONLY run, and
    incremental overhead at or under ``ceiling_pct``. An arm that detects more
    only by isolating half the mesh fails the second; an arm that is cheap
    because it never audits fails the first.

    Severity is a key and never an axis to collapse — a rate that is survivable
    for OVERT faults says nothing about the SUBTLE case that motivates having a
    sentinel at all.

    Returns ``{severity: {"max_false_positive_rate": float | None, ...}}``.
    ``None`` means no swept FP rate clears the condition for that severity,
    which is a finding rather than a gap: it says the architecture has no viable
    operating point at any false-positive rate that was tried.

    Cells are compared at their aggregated (seed-collapsed) values, so this
    consumes :func:`aggregate` output, not raw run rows.
    """
    out: dict[str, dict] = {}
    for severity in sorted({row["severity"] for row in rows}):
        rates_seen: set[float] = set()
        viable: dict[float, list[dict]] = {}
        for row in rows:
            if row["severity"] != severity or row["arm"] == BASELINE_ARM:
                continue
            rate = row.get("effective_false_positive_rate")
            if rate is None:
                continue
            rates_seen.add(rate)
            uplift = row.get("incremental_detection_over_tier0_only")
            # A null baseline is not a zero one: a cell we could not compare is
            # dropped rather than counted as showing no uplift.
            if uplift is None or uplift <= 0.0:
                continue
            if row["overhead_pct"] > ceiling_pct:
                continue
            viable.setdefault(rate, []).append(row)

        best = max(viable) if viable else None
        out[severity] = {
            "max_false_positive_rate": best,
            "ceiling_pct": ceiling_pct,
            "false_positive_rates_swept": sorted(rates_seen),
            # Which arms cleared it, so the number is auditable rather than
            # just asserted.
            "arms_clearing": (
                sorted({row["arm"] for row in viable[best]}) if best is not None else []
            ),
            "n_cells_clearing": len(viable[best]) if best is not None else 0,
        }
    return out


def attach_incremental_detection_over_tier0(rows: list[dict]) -> list[dict]:
    """Fill ``incremental_detection_over_tier0_only`` in place, and return rows.

    Detection over and above what mechanical Tier 0 telemetry already gets for
    free is the only detection number worth paying a sentinel for, so it is
    scored against the matched TIER0_ONLY run rather than against zero.

    This cannot be computed inside a run: the baseline is a different run. It is
    therefore a post-pass over a set of rows, applied by the sweep before it
    writes its summary and by :func:`~harpy.sweep.load_result_rows` when results
    are read back. A row whose baseline is not in the set keeps ``None`` —
    absent baselines are never treated as zero detection, which would turn a
    narrowed grid into a free uplift.
    """
    baseline: dict[tuple, list[float]] = {}
    for row in rows:
        if row.get("arm") == BASELINE_ARM:
            baseline.setdefault(_baseline_key(row), []).append(row["detection_rate"])

    for row in rows:
        rates = baseline.get(_baseline_key(row))
        if rates is None:
            row["incremental_detection_over_tier0_only"] = None
        else:
            row["incremental_detection_over_tier0_only"] = row["detection_rate"] - statistics.fmean(
                rates
            )
    return rows


def result_document(record: RunRecord) -> dict:
    """The full results JSON for one run."""
    return {
        "schema": "harpy-sim/run/1",
        "spec": record.spec.as_dict(),
        "metrics": run_metrics(record),
        "ledger": record.ledger.as_dict(),
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
    """Collapse seeds within each cell.

    Seeds are the only axis collapsed here. Severity never is, and neither is
    fleet_scale or the alert cap: both change the answer, which is the point of
    having swept them.
    """
    cells: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (
            row["arm"],
            row["severity"],
            row["budget_pct"],
            row["lineage_fidelity"],
            row["reserve_fraction"],
            row.get("fleet_scale", 1.0),
            row.get("max_alerts_per_agent_hour", float("inf")),
            # The three new axes key cells like every other axis that changes
            # the answer. Collapsing detector quality into a blended detection
            # rate is the same mistake as blending severities.
            row.get("effective_detection_prob"),
            row.get("effective_false_positive_rate"),
            row.get("worker_model"),
        )
        cells.setdefault(key, []).append(row)

    out: list[dict] = []
    for key, group in sorted(cells.items(), key=lambda item: tuple(str(p) for p in item[0])):
        (
            arm,
            severity,
            budget,
            fidelity,
            reserve,
            fleet_scale,
            alert_cap,
            detection_prob,
            false_positive_rate,
            worker_model,
        ) = key
        viability = [
            r["min_fleet_dollars_per_hour_for_viability"]
            for r in group
            if r.get("min_fleet_dollars_per_hour_for_viability") is not None
        ]
        detections = [r["median_interactions_to_detection"] for r in group]
        detections = [d for d in detections if d is not None]
        contaminated = [
            r["contaminated_claims_at_isolation"]
            for r in group
            if r["contaminated_claims_at_isolation"] is not None
        ]
        coverage = [
            r["audit_coverage_of_faulty_agent"]
            for r in group
            if r.get("audit_coverage_of_faulty_agent") is not None
        ]
        first_audit = [
            r["ticks_faulty_before_first_audit"]
            for r in group
            if r.get("ticks_faulty_before_first_audit") is not None
        ]
        incremental = [
            r["incremental_detection_over_tier0_only"]
            for r in group
            if r.get("incremental_detection_over_tier0_only") is not None
        ]
        out.append(
            {
                "arm": arm,
                "severity": severity,
                "budget_pct": budget,
                "lineage_fidelity": fidelity,
                "reserve_fraction": reserve,
                "fleet_scale": fleet_scale,
                "max_alerts_per_agent_hour": alert_cap,
                "effective_detection_prob": detection_prob,
                "effective_false_positive_rate": false_positive_rate,
                "worker_model": worker_model,
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
                "mean_triage_dollars": statistics.fmean(r["triage_dollars"] for r in group),
                "mean_investigation_dollars": statistics.fmean(
                    r["investigation_dollars"] for r in group
                ),
                "mean_worker_dollars": statistics.fmean(r["worker_dollars"] for r in group),
                "mean_audits_run": statistics.fmean(r["audits_run"] for r in group),
                "mean_review_cost_share_of_overhead": statistics.fmean(
                    r["review_cost_share_of_overhead"] for r in group
                ),
                # None means "no fleet size clears the ceiling"; those runs are
                # dropped from the median rather than counted as zero, and the
                # count of them is reported alongside so the drop is visible.
                "median_min_fleet_dollars_per_hour_for_viability": (
                    statistics.median(viability) if viability else None
                ),
                "n_seeds_never_viable": len(group) - len(viability),
                "mean_alerts_per_agent_hour": statistics.fmean(
                    r["alerts_per_agent_hour"] for r in group
                ),
                "mean_suppressed_alerts": statistics.fmean(
                    r["suppressed_alerts"] for r in group
                ),
                "mean_detections_lost_to_alert_cap": statistics.fmean(
                    r["detections_lost_to_alert_cap"] for r in group
                ),
                "mean_effective_isolation_threshold": statistics.fmean(
                    r["effective_isolation_threshold"] for r in group
                ),
                # -- audit targeting --------------------------------------
                "mean_audits_on_faulty_agent": statistics.fmean(
                    r["audits_on_faulty_agent"] for r in group
                ),
                "mean_audits_during_fault_exposure": statistics.fmean(
                    r["audits_during_fault_exposure"] for r in group
                ),
                # Coverage and time-to-first-audit are None on runs that never
                # audited after onset / never audited the faulty agent. Those
                # runs are dropped from the mean rather than counted as zero,
                # and the count of them is reported so the drop stays visible.
                "mean_audit_coverage_of_faulty_agent": (
                    statistics.fmean(coverage) if coverage else None
                ),
                "n_seeds_no_audits_after_onset": len(group) - len(coverage),
                "median_ticks_faulty_before_first_audit": (
                    statistics.median(first_audit) if first_audit else None
                ),
                "n_seeds_faulty_never_audited": len(group) - len(first_audit),
                # -- false-positive cost ----------------------------------
                "mean_false_isolation_count": statistics.fmean(
                    r["false_isolation_count"] for r in group
                ),
                "mean_discard_rerun_dollars_from_false_isolations": statistics.fmean(
                    r["discard_rerun_dollars_from_false_isolations"] for r in group
                ),
                "mean_discard_rerun_dollars_from_true_isolations": statistics.fmean(
                    r["discard_rerun_dollars_from_true_isolations"] for r in group
                ),
                "mean_incremental_detection_over_tier0_only": (
                    statistics.fmean(incremental) if incremental else None
                ),
                "pricing_verified": all(r["pricing_verified"] for r in group),
            }
        )
    return out
