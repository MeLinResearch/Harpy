"""Five arms, one budget. Audit count is derived from dollars, never configured.

The whole experiment is a comparison between ways of spending a fixed oversight
allowance, so no arm is allowed to buy audits it cannot afford. Each tick the
allowance accrues as ``budget_pct`` of that tick's worker spend, Tier 0's bill
comes off the top, and audits are drawn until the pool cannot cover another one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .types import Arm


@dataclass(frozen=True)
class SamplerConfig:
    arm: Arm
    budget_pct: float
    reserve_fraction: float = 0.0
    carryover_cap_ticks: float = 5.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.reserve_fraction <= 1.0:
            raise ValueError(f"reserve_fraction must be in [0, 1], got {self.reserve_fraction}")
        if self.budget_pct < 0.0:
            raise ValueError(f"budget_pct must be >= 0, got {self.budget_pct}")


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
        self, active_agents: tuple[str, ...], tier0_scores: dict[str, float]
    ) -> tuple[str, ...]:
        """Agents to audit this tick, sampled without replacement."""
        arm = self.config.arm
        if arm in (Arm.NONE, Arm.TIER0_ONLY) or not active_agents:
            return ()

        candidates = sorted(active_agents)
        if arm == Arm.RANDOM:
            picks = self._draw(candidates, tier0_scores, self.pool_dollars, directed=False)
        elif arm == Arm.DIRECTED:
            picks = self._draw(candidates, tier0_scores, self.pool_dollars, directed=True)
        elif arm == Arm.HYBRID:
            reserve = self.pool_dollars * self.config.reserve_fraction
            picks = self._draw(candidates, tier0_scores, reserve, directed=False)
            remaining = [a for a in candidates if a not in set(picks)]
            picks += self._draw(remaining, tier0_scores, self.pool_dollars, directed=True)
        else:  # pragma: no cover - Arm is exhaustive
            raise ValueError(f"unknown arm {arm!r}")
        return tuple(picks)

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
