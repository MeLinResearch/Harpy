"""Six arms, one budget. Audit count is derived from dollars, never configured.

The whole experiment is a comparison between ways of spending a fixed oversight
allowance, so no arm is allowed to buy audits it cannot afford. Each tick the
allowance accrues as ``budget_pct`` of that tick's worker spend, Tier 0's bill
comes off the top, and audits are drawn until the pool cannot cover another one.

v3 adds two things, both off by default so v1 and v2 reproduce unchanged:

* **Targeted re-audit.** ``followup_audits = k`` queues ``k`` follow-up audits
  on an agent the moment one of its audits comes back flagged. Follow-ups are
  served before the arm draws, from the same pool, at most one per agent per
  tick. They buy no extra money; they spend the money the arm would otherwise
  have spent on a fresh draw. v2 failed because an agent needs roughly three
  flagged audits close together to cross the isolation gate and a uniform draw
  almost never returns to the same agent in time. This is the one change aimed
  at that bottleneck.
* **The EXPOSURE arm.** Audits go to the agents with the most unchecked
  downstream exposure: ticks since last audit times (1 + observable fan-out of
  the claims emitted since then). The score is computed by
  :class:`ExposureTracker` from observable messages and the lossy lineage graph,
  never from ground truth.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass

from .types import Arm, LineageEdge, Message


@dataclass(frozen=True)
class SamplerConfig:
    arm: Arm
    budget_pct: float
    reserve_fraction: float = 0.0
    carryover_cap_ticks: float = 5.0
    #: Follow-up audits queued on an agent after each flagged audit. 0 is v2.
    followup_audits: int = 0

    def __post_init__(self) -> None:
        if not 0.0 <= self.reserve_fraction <= 1.0:
            raise ValueError(f"reserve_fraction must be in [0, 1], got {self.reserve_fraction}")
        if self.budget_pct < 0.0:
            raise ValueError(f"budget_pct must be >= 0, got {self.budget_pct}")
        if int(self.followup_audits) != self.followup_audits or self.followup_audits < 0:
            raise ValueError(
                f"followup_audits must be a non-negative integer, got {self.followup_audits}"
            )


class Sampler:
    """Turns dollars into a list of agents to audit this tick."""

    def __init__(
        self, config: SamplerConfig, audit_cost_dollars: float, rng: random.Random
    ) -> None:
        self.config = config
        self.audit_cost = float(audit_cost_dollars)
        self.rng = rng
        self.pool_dollars = 0.0
        self.audits_drawn = 0
        self.dollars_committed = 0.0
        #: Follow-ups still owed, oldest flag first. Insertion order is the
        #: service order, so it is deterministic without consulting the RNG.
        self._pending: dict[str, int] = {}
        self.followup_audits_drawn = 0

    # -- targeted re-audit -------------------------------------------------

    def record_result(self, agent_id: str, flagged: bool) -> None:
        """Queue follow-ups after a flagged audit. A clean audit queues nothing.

        A new flag refills the queue to ``k`` rather than adding to it, so an
        agent that keeps flagging keeps being audited and one that stops runs
        out after ``k``. A clean follow-up does not cancel the rest: it already
        buys the agent credit back through the suspicion tracker, and cancelling
        on one clean result would discard most of a detector with a 42% hit
        rate's chances to confirm.
        """
        k = int(self.config.followup_audits)
        if not flagged or k <= 0:
            return
        if self._pending.get(agent_id, 0) < k:
            self._pending.pop(agent_id, None)
            self._pending[agent_id] = k

    def forget(self, agent_id: str) -> None:
        """Drop any follow-ups owed on an agent that was isolated or restarted."""
        self._pending.pop(agent_id, None)

    def pending_followups(self) -> dict[str, int]:
        return dict(self._pending)

    def _serve_followups(self, active: set[str]) -> list[str]:
        picks: list[str] = []
        for agent_id in list(self._pending):
            if agent_id not in active:
                del self._pending[agent_id]
                continue
            if self.audit_cost > 0.0 and self.pool_dollars < self.audit_cost:
                break
            picks.append(agent_id)
            self.pool_dollars -= self.audit_cost
            self.dollars_committed += self.audit_cost
            self.audits_drawn += 1
            self.followup_audits_drawn += 1
            self._pending[agent_id] -= 1
            if self._pending[agent_id] <= 0:
                del self._pending[agent_id]
        return picks

    # -- budget ------------------------------------------------------------

    def accrue(self, worker_dollars_this_tick: float, tier0_dollars_this_tick: float) -> float:
        """Add this tick's allowance to the pool, net of Tier 0's bill.

        Tier 0 is charged against the same allowance because it is part of the
        incremental oversight cost the budget is a budget *of*. Unspent dollars
        roll forward but are capped, so a quiet opening phase cannot bankroll an
        implausible audit burst later — with a floor of one audit, because a
        budget that cannot save up for the smallest purchasable unit is not a
        small budget, it is no budget.
        """
        allowance = self.config.budget_pct * worker_dollars_this_tick
        self.pool_dollars += allowance - tier0_dollars_this_tick
        # The floor at one audit matters: an audit is indivisible, so a cap below
        # its price would silently turn every small budget into the NONE arm and
        # the low end of the sweep would measure the cap instead of the budget.
        cap = max(self.config.carryover_cap_ticks * max(allowance, 0.0), self.audit_cost)
        if self.pool_dollars > cap:
            self.pool_dollars = cap
        if self.pool_dollars < 0.0:
            self.pool_dollars = 0.0
        return self.pool_dollars

    def _affordable(self, budget: float, remaining_agents: int) -> int:
        """How many audits ``budget`` buys, capped at one per agent per tick.

        The per-agent cap also keeps a zero-cost price list (placeholder pricing)
        from drawing an unbounded number of audits.
        """
        if remaining_agents <= 0:
            return 0
        if self.audit_cost <= 0.0:
            return remaining_agents
        return min(int(budget // self.audit_cost), remaining_agents)

    # -- arms --------------------------------------------------------------

    def select(
        self,
        active_agents: tuple[str, ...],
        tier0_scores: dict[str, float],
        exposure: dict[str, float] | None = None,
    ) -> tuple[str, ...]:
        """Agents to audit this tick, sampled without replacement.

        Owed follow-ups are served first; the arm then draws from whoever is
        left with whatever is left. With no follow-ups owed this is exactly the
        v2 code path, RNG calls included. ``exposure`` is read by the EXPOSURE
        arm only.
        """
        arm = self.config.arm
        if arm in (Arm.NONE, Arm.TIER0_ONLY) or not active_agents:
            return ()

        followups = self._serve_followups(set(active_agents)) if self._pending else []
        taken = set(followups)
        candidates = sorted(a for a in active_agents if a not in taken)
        if arm == Arm.RANDOM:
            picks = self._draw(candidates, tier0_scores, self.pool_dollars, directed=False)
        elif arm == Arm.DIRECTED:
            picks = self._draw(candidates, tier0_scores, self.pool_dollars, directed=True)
        elif arm == Arm.HYBRID:
            reserve = self.pool_dollars * self.config.reserve_fraction
            picks = self._draw(candidates, tier0_scores, reserve, directed=False)
            remaining = [a for a in candidates if a not in set(picks)]
            picks += self._draw(remaining, tier0_scores, self.pool_dollars, directed=True)
        elif arm == Arm.EXPOSURE:
            picks = self._draw_exposure(candidates, exposure or {})
        else:  # pragma: no cover - Arm is exhaustive
            raise ValueError(f"unknown arm {arm!r}")
        return tuple(followups + picks)

    def _draw_exposure(self, candidates: list[str], exposure: dict[str, float]) -> list[str]:
        """Greedy: the highest unchecked exposure first, ties broken at random.

        BRA-Audit picks audit points by marginal exposure reduction. Auditing an
        agent zeroes its own exposure and no one else's here, so the greedy
        choice is simply the top of the ranking. The shuffle before the stable
        sort is the tie-break, and it is the only RNG use in this arm.
        """
        pool = list(candidates)
        n = self._affordable(self.pool_dollars, len(pool))
        if n <= 0:
            return []
        self.rng.shuffle(pool)
        pool.sort(key=lambda agent_id: -max(exposure.get(agent_id, 0.0), 0.0))
        picks: list[str] = []
        for agent_id in pool[:n]:
            if self.audit_cost > 0.0 and self.pool_dollars < self.audit_cost:
                break
            picks.append(agent_id)
            self.pool_dollars -= self.audit_cost
            self.dollars_committed += self.audit_cost
            self.audits_drawn += 1
        return picks

    def _draw(
        self,
        candidates: list[str],
        tier0_scores: dict[str, float],
        budget: float,
        *,
        directed: bool,
    ) -> list[str]:
        pool = list(candidates)
        n = self._affordable(min(budget, self.pool_dollars), len(pool))
        picks: list[str] = []
        for _ in range(n):
            if not pool or self.pool_dollars < self.audit_cost:
                break
            index = (
                self._pick_index(pool, tier0_scores)
                if directed
                else self.rng.randrange(len(pool))
            )
            picks.append(pool.pop(index))
            self.pool_dollars -= self.audit_cost
            self.dollars_committed += self.audit_cost
            self.audits_drawn += 1
        return picks

    def _pick_index(self, pool: list[str], tier0_scores: dict[str, float]) -> int:
        """Index sampled with probability proportional to Tier 0 score.

        Before any agent has accumulated signal every weight is zero and the
        proportional draw is undefined; the fallback is uniform, which is also
        the honest behaviour — with no evidence, directed sampling *is* random.
        """
        weights = [max(tier0_scores.get(agent_id, 0.0), 0.0) for agent_id in pool]
        total = sum(weights)
        if total <= 0.0:
            return self.rng.randrange(len(pool))
        target = self.rng.random() * total
        cumulative = 0.0
        for index, weight in enumerate(weights):
            cumulative += weight
            if target < cumulative:
                return index
        return len(pool) - 1


class ExposureTracker:
    """Unchecked downstream exposure per agent, from observables only.

    ``exposure = gap * (1 + fanout)``, where ``gap`` is ticks since the agent
    was last audited (or reviewed) and ``fanout`` is how many observable lineage
    edges hang off the claims it emitted since then. BRA-Audit scores a node by
    audit gap times topological impact; one-hop fan-out stands in for full
    reachability here because in this well-mixed mesh the transitive closure of
    a claim is dominated by its age, which ``gap`` already counts, and walking it
    for every agent every tick costs more than the rest of the run.

    The observable graph is refreshed every ``refresh_ticks`` ticks rather than
    every tick, as a provenance store would be polled. Between refreshes newly
    emitted claims count toward ``gap`` but have no fan-out yet.
    """

    def __init__(self, refresh_ticks: int = 10) -> None:
        if int(refresh_ticks) < 1:
            raise ValueError(f"refresh_ticks must be >= 1, got {refresh_ticks}")
        self.refresh_ticks = int(refresh_ticks)
        self._unaudited: dict[str, list[str]] = {}
        self._last_audit: dict[str, int] = {}
        self._child_count: dict[str, int] = {}
        self._last_refresh: int | None = None
        self.refreshes = 0

    def observe(self, message: Message) -> None:
        bucket = self._unaudited.setdefault(message.sender_id, [])
        bucket.extend(claim.claim_id for claim in message.claims)

    def audited(self, agent_id: str, tick: int) -> None:
        """An audit or a human review: the agent's exposure is checked."""
        self._last_audit[agent_id] = int(tick)
        self._unaudited[agent_id] = []

    def refresh_due(self, tick: int) -> bool:
        return self._last_refresh is None or tick - self._last_refresh >= self.refresh_ticks

    def refresh(self, observable: Iterable[LineageEdge], tick: int) -> None:
        counts: dict[str, int] = {}
        for edge in observable:
            counts[edge.parent_claim_id] = counts.get(edge.parent_claim_id, 0) + 1
        self._child_count = counts
        self._last_refresh = int(tick)
        self.refreshes += 1

    def scores(self, agents: Iterable[str], tick: int) -> dict[str, float]:
        out: dict[str, float] = {}
        for agent_id in agents:
            gap = int(tick) - self._last_audit.get(agent_id, -1)
            fanout = sum(self._child_count.get(c, 0) for c in self._unaudited.get(agent_id, ()))
            out[agent_id] = float(gap * (1 + fanout))
        return out
