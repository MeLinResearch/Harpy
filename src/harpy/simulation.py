"""One run: a mesh, a budget, an arm, and the bookkeeping that scores them.

This module is the seam between the world and HARPY. It is allowed to see
ground truth because it has to score the run, but everything it hands to a
HARPY-side component is an observable: messages, Tier 0 scores, audit results,
and the lossy lineage projection.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from .faults import sample_severity
from .ledger import CostAccountant, Pricing
from .lineage import make_pair
from .mesh import Mesh
from .response import ResponseController
from .sampler import Sampler, SamplerConfig
from .sentinel import MockSentinel
from .suspicion import SuspicionConfig, SuspicionTracker
from .telemetry import Tier0Config, Tier0Telemetry
from .types import Arm, LineagePair, Severity

# Independent RNG streams so that changing, say, sampler behaviour does not
# reshuffle the world the arms are being compared on.
_STREAM_MESH = 11
_STREAM_SENTINEL = 23
_STREAM_SAMPLER = 37
_STREAM_LINEAGE = 53


def _stream_seed(seed: int, stream: int) -> int:
    return (seed * 1_000_003) + stream


@dataclass(frozen=True)
class RunSpec:
    seed: int
    severity: Severity
    arm: Arm
    budget_pct: float
    lineage_fidelity: float
    reserve_fraction: float = 0.0

    def as_dict(self) -> dict:
        return {
            "seed": self.seed,
            "severity": self.severity.value,
            "arm": self.arm.value,
            "budget_pct": self.budget_pct,
            "lineage_fidelity": self.lineage_fidelity,
            "reserve_fraction": self.reserve_fraction,
        }

    def run_id(self) -> str:
        return (
            f"seed{self.seed:03d}_{self.arm.value}_{self.severity.value}"
            f"_b{self.budget_pct:g}_f{self.lineage_fidelity:g}_r{self.reserve_fraction:g}"
        )


@dataclass
class IsolationEvent:
    tick: int
    agent_id: str
    was_faulty: bool
    suspicion: float
    contaminated_claims: int
    interactions_since_onset: int


@dataclass
class RunRecord:
    """Everything :mod:`harpy.metrics` needs, and nothing it has to guess at."""

    spec: RunSpec
    n_agents: int
    n_ticks: int
    tick_seconds: int
    fault_distribution: dict[str, float]
    faulty_agent_id: str
    fault_onset_tick: int
    ledger_dict: dict
    isolations: list[IsolationEvent] = field(default_factory=list)
    audits_run: int = 0
    tier0_messages: int = 0
    total_claims: int = 0
    corrupt_claims_total: int = 0
    agent_active_ticks: int = 0
    flagged_claim_ids: frozenset[str] = frozenset()
    detected_tick: int | None = None
    interactions_to_detection: int | None = None
    contaminated_at_isolation: int | None = None
    pricing_dict: dict = field(default_factory=dict)


def run_simulation(config: dict, spec: RunSpec, pricing: Pricing) -> RunRecord:
    sim_cfg = config["simulation"]
    mesh = Mesh(config, spec.severity, spec.seed)

    accountant = CostAccountant(
        pricing,
        rerun_multiplier=float(config["response"]["rerun_multiplier"]),
        human_review_dollars=float(config["response"]["human_review_dollars"]),
    )
    tier0 = Tier0Telemetry(Tier0Config.from_config(config, pricing.tier0_cost_per_message_dollars))
    suspicion = SuspicionTracker(SuspicionConfig.from_config(config))
    sentinel = MockSentinel(
        detection_prob=config["sentinel"]["detection_prob"],
        false_positive_rate=float(config["sentinel"]["false_positive_rate"]),
        input_tokens=int(config["sentinel"]["input_tokens"]),
        output_tokens=int(config["sentinel"]["output_tokens"]),
        rng=random.Random(_stream_seed(spec.seed, _STREAM_SENTINEL)),
        severity=spec.severity,
    )
    sampler = Sampler(
        SamplerConfig(
            arm=spec.arm,
            budget_pct=spec.budget_pct,
            reserve_fraction=spec.reserve_fraction,
            carryover_cap_ticks=float(config["sampler"]["carryover_cap_ticks"]),
        ),
        audit_cost_dollars=accountant.audit_cost(sentinel.input_tokens, sentinel.output_tokens),
        rng=random.Random(_stream_seed(spec.seed, _STREAM_SAMPLER)),
    )

    lineage_seed = _stream_seed(spec.seed, _STREAM_LINEAGE)
    spurious_rate = float(config["lineage"]["spurious_rate"])

    projection_cache: dict[str, object] = {"n_edges": -1, "pair": None}

    def lineage_provider() -> LineagePair:
        # Rebuilt from a freshly seeded stream so the projection depends on the
        # edge set alone, never on how many times it has been asked for. Cached
        # by edge count because isolation walks the graph once per claim.
        if projection_cache["n_edges"] != len(mesh.lineage_edges):
            projection_cache["n_edges"] = len(mesh.lineage_edges)
            projection_cache["pair"] = make_pair(
                frozenset(mesh.lineage_edges),
                spec.lineage_fidelity,
                spurious_rate,
                random.Random(lineage_seed),
            )
        return projection_cache["pair"]  # type: ignore[return-value]

    response = ResponseController(
        mesh,
        accountant,
        lineage_provider,
        alert_on_isolation=bool(config["response"]["alert_on_isolation"]),
    )

    window_messages = int(config["sentinel"]["window_messages"])
    recent: dict[str, deque] = {
        agent_id: deque(maxlen=window_messages) for agent_id in mesh.agents
    }

    record = RunRecord(
        spec=spec,
        n_agents=len(mesh.agents),
        n_ticks=mesh.n_ticks,
        tick_seconds=int(sim_cfg["tick_seconds"]),
        fault_distribution={str(k): float(v) for k, v in config["fault_distribution"].items()},
        faulty_agent_id=mesh.faulty_agent_id,
        fault_onset_tick=mesh.fault_onset_tick,
        ledger_dict={},
        pricing_dict=pricing.as_dict(),
    )

    # NONE is the unsupervised control: no Tier 0, no audits, no isolations, and
    # therefore no incremental cost. It is the baseline every other arm's
    # overhead is measured against, so running Tier 0 under it would quietly
    # give the control an oversight layer.
    oversight = spec.arm is not Arm.NONE

    for tick in range(mesh.n_ticks):
        emissions = mesh.step(tick)
        worker_this_tick = 0.0
        tier0_this_tick = 0.0
        for emission in emissions:
            message = emission.message
            worker_this_tick += accountant.charge_worker(
                message.sender_id, emission.input_tokens, emission.output_tokens
            )
            if oversight:
                tier0_this_tick += accountant.charge_tier0(
                    tier0.observe(message, emission.rejected_claim_ids)
                )
            recent[message.sender_id].append(message)

        active = mesh.active_agents()
        record.agent_active_ticks += len(active)
        if not oversight:
            continue
        tier0_scores = tier0.scores(active)
        suspicion.tick(tier0_scores)

        sampler.accrue(worker_this_tick, tier0_this_tick)
        for agent_id in sampler.select(active, tier0_scores):
            result = sentinel.audit(agent_id, tuple(recent[agent_id]))
            accountant.charge_audit(result)
            suspicion.apply_audit(result)

        for agent_id in suspicion.isolation_candidates(active):
            _isolate(mesh, response, suspicion, tier0, record, recent, agent_id, tick)

    record.ledger_dict = accountant.ledger.as_dict()
    record.audits_run = accountant.audits_charged
    record.tier0_messages = tier0.messages_observed
    record.total_claims = mesh.total_claims
    record.corrupt_claims_total = len(mesh.corrupt_claim_ticks)
    record.flagged_claim_ids = frozenset(response.flagged_claim_ids)
    return record


def _isolate(mesh, response, suspicion, tier0, record, recent, agent_id, tick) -> None:
    """Isolate one agent and score the consequence.

    Whether the agent was really faulty is a ground-truth question, decided here
    (the world) and never inside HARPY. The modelling assumption is that the
    human paged by ``alert_human`` adjudicates correctly: a wrongly isolated
    agent is restarted clean and rejoins the mesh, at the cost of its discarded
    work, its rerun, and the review itself.
    """
    was_faulty = mesh.is_faulty_now(agent_id)
    contaminated = sum(1 for t in mesh.corrupt_claim_ticks if t <= tick)

    response.isolate(agent_id)
    if response.alert_on_isolation:
        response.alert_human(agent_id)
    for message in recent[agent_id]:
        for claim in message.claims:
            response.flag_downstream(claim.claim_id)

    interactions = mesh.faulty_messages_after_onset if was_faulty else 0
    record.isolations.append(
        IsolationEvent(
            tick=tick,
            agent_id=agent_id,
            was_faulty=was_faulty,
            suspicion=suspicion.score(agent_id),
            contaminated_claims=contaminated,
            interactions_since_onset=interactions,
        )
    )

    if was_faulty:
        if record.detected_tick is None:
            record.detected_tick = tick
            record.interactions_to_detection = interactions
            record.contaminated_at_isolation = contaminated
        suspicion.reset(agent_id)
        # The fault is contained; the agent stays out for the rest of the run.
        return

    # A clean agent: cleared by review, restarted, and back to work.
    response.restart_clean(agent_id)
    suspicion.reset(agent_id)
    tier0.reset_agent(agent_id)
    recent[agent_id].clear()


def resolve_severity(config: dict, spec_severity: Severity | str | None, seed: int) -> Severity:
    """Pin the run's severity, or draw one from the preregistered distribution."""
    if spec_severity is not None:
        return Severity(spec_severity)
    return sample_severity(
        config["fault_distribution"], random.Random(_stream_seed(seed, _STREAM_MESH))
    )
