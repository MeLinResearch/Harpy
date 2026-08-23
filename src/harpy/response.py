"""The five permitted responses. Nothing else is allowed to touch the mesh.

A supervisory layer with an open-ended action space is not a supervisory layer,
it is a second control plane with its own failure modes. HARPY may do exactly
five things, each scoped to one named agent, and downstream flagging runs on the
OBSERVABLE lineage graph only — the same lossy one operators actually have.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .lineage import children_map, descendants
from .types import LineagePair


@dataclass(frozen=True)
class ActionRecord:
    tick: int
    action: str
    target: str
    detail: dict


class ResponseController:
    def __init__(
        self,
        mesh,
        accountant,
        lineage_provider: Callable[[], LineagePair],
        alert_budget,
        alert_on_isolation: bool = True,
    ) -> None:
        self._mesh = mesh
        self._accountant = accountant
        self._lineage_provider = lineage_provider
        self._alert_budget = alert_budget
        self.alert_on_isolation = bool(alert_on_isolation)
        self.actions: list[ActionRecord] = []
        self.flagged_claim_ids: set[str] = set()
        # Isolation flags every claim in the agent's recent window, so the same
        # graph gets walked dozens of times in a row. Cache it by size.
        self._graph_cache: tuple[int, dict[str, list[str]]] | None = None

    @property
    def accountant(self):
        """The cost accountant, for the world to file attributions against.

        Exposed read-only so :mod:`harpy.simulation` can split an isolation's
        already-charged bill into true and false without this module ever
        learning which it was.
        """
        return self._accountant

    # -- the five actions --------------------------------------------------

    def isolate(self, agent_id: str) -> ActionRecord:
        """Take one agent out of the mesh and write off its unfinished work."""
        self._mesh.deactivate(agent_id)
        discarded, rerun = self._accountant.charge_isolation(agent_id)
        self._accountant.reset_agent_spend(agent_id)
        return self._record(
            "isolate", agent_id, {"discarded_dollars": discarded, "rerun_dollars": rerun}
        )

    def halt_outbound(self, agent_id: str) -> ActionRecord:
        """Stop one agent from emitting without tearing down its state."""
        self._mesh.halt_outbound(agent_id)
        return self._record("halt_outbound", agent_id, {})

    def restart_clean(self, agent_id: str) -> ActionRecord:
        """Bring one agent back with empty state. Resets its discard clock."""
        self._mesh.restart_clean(agent_id)
        self._accountant.reset_agent_spend(agent_id)
        return self._record("restart_clean", agent_id, {})

    def flag_downstream(self, claim_id: str) -> ActionRecord:
        """Flag everything reachable from a claim in the OBSERVABLE lineage graph.

        Never ground truth: at low ``lineage_fidelity`` this both misses real
        descendants and flags claims that were never derived from anything. That
        gap is the quantity the fidelity sweep is trying to price.
        """
        observable = self._lineage_provider().observable
        if self._graph_cache is None or self._graph_cache[0] != len(observable):
            self._graph_cache = (len(observable), children_map(observable))
        reachable = descendants(observable, claim_id, self._graph_cache[1])
        self.flagged_claim_ids.update(reachable)
        self.flagged_claim_ids.add(claim_id)
        return self._record("flag_downstream", claim_id, {"n_flagged": len(reachable) + 1})

    def alert_human(self, agent_id: str) -> ActionRecord:
        """Put one agent in front of a person, if there is attention left to spend.

        The alert budget is consulted first and is not advisory: at the cap the
        alert does not fire, and the refusal is recorded as ``alert_suppressed``
        so the run can count what it did not do. Suppression is not a sixth
        permitted action — it is the absence of the fifth, logged.
        """
        if not self._alert_budget.allows():
            self._alert_budget.record_suppression()
            return self._record("alert_suppressed", agent_id, {"reason": "alert_budget"})

        cost, opened_review = self._accountant.charge_human_review(
            agent_id, self._mesh.current_tick
        )
        self._alert_budget.record_alert()
        return self._record(
            "alert_human",
            agent_id,
            {"review_dollars": cost, "opened_review": opened_review},
        )

    # -- bookkeeping -------------------------------------------------------

    def _record(self, action: str, target: str, detail: dict) -> ActionRecord:
        record = ActionRecord(
            tick=self._mesh.current_tick, action=action, target=target, detail=detail
        )
        self.actions.append(record)
        return record
