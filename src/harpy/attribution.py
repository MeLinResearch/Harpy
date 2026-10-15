"""Who started it: Tattletale's origin walk, on HARPY's observable lineage.

A content-level sentinel can say a claim is bad. It cannot say who made it bad:
a healthy agent that restates a corrupt claim it received looks exactly like
the agent that corrupted it, so v3's follow-up audits kept confirming relays.

`Tattletale <https://github.com/MeLinResearch/tattletale>`_ answers that
question deterministically for quoted text. Its rule, ported here unchanged in
meaning (Tattletale ARCHITECTURE.md §6):

* Walk the claim's lineage backward.
* An agent that carried a claim forward **unchanged** passes the blame upstream.
* An agent whose claim **differs** from what it received owns it: a modified
  claim is a new claim.
* **Parent not found:** report ``BROKEN_LINEAGE`` and name the last known
  agent. Never silently attribute to the submitter.
* **Cycle:** cap the walk at the number of recorded claims; on overrun, report
  the chain as broken.

Two things differ from Tattletale, both because HARPY's claims are typed
values rather than quotes of a source document:

* "Unchanged" means the same subject, field and value. Tattletale compares
  normalized text; schema version is excluded here because every agent restamps
  claims with the schema it speaks.
* The verdict that a claim is bad does not come from an exact source match.
  It comes from the sentinel naming the claim (``AuditResult.evidence_claim_ids``),
  which is a model output, not a deterministic check. The walk itself is
  deterministic: models propose, code decides who to blame.

Lineage here is lossy (:class:`harpy.lineage.ProvenanceLog`), so a dropped
edge makes a relay look like an origin, and a spurious edge can route blame to
the wrong parent. The lineage-fidelity sweep measures how much that costs. This
module is guarded: it reads messages and observable parents only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .types import ClaimPayload, Message

TRACED = "TRACED"
BROKEN_LINEAGE = "BROKEN_LINEAGE"


def content_key(payload: ClaimPayload) -> tuple:
    """What "carried unchanged" compares. Schema version is deliberately absent."""
    return (
        payload.subject_id,
        payload.field_name,
        payload.value_num,
        payload.value_date,
        payload.value_enum,
    )


@dataclass(frozen=True)
class Attribution:
    status: str  # TRACED | BROKEN_LINEAGE
    origin_agent_id: str
    origin_claim_id: str
    hops: int  # how many unchanged carries were walked through


class Attributor:
    """Records every emitted claim, then walks blame back on request."""

    def __init__(self) -> None:
        self._claims: dict[str, tuple[str, tuple]] = {}
        self.walks = 0
        self.redirected = 0
        self.broken = 0

    def observe(self, message: Message) -> None:
        for claim in message.claims:
            self._claims[claim.claim_id] = (claim.origin_agent_id, content_key(claim.payload))

    def submitter(self, claim_id: str) -> str | None:
        entry = self._claims.get(claim_id)
        return entry[0] if entry else None

    def trace(
        self, claim_id: str, parents_of: Callable[[str], tuple[str, ...]]
    ) -> Attribution | None:
        """Origin of ``claim_id``, or ``None`` if the claim was never observed.

        ``parents_of`` returns a claim's observable parents, sorted. A claim with
        several (a spurious edge beside the real one) follows the first parent
        it was carried unchanged from.
        """
        if claim_id not in self._claims:
            return None
        self.walks += 1
        current = claim_id
        hops = 0
        for _ in range(len(self._claims)):
            agent, content = self._claims[current]
            parent_ids = parents_of(current)
            if not parent_ids:
                return self._done(TRACED, agent, current, hops, claim_id)
            missing = [p for p in parent_ids if p not in self._claims]
            if missing:
                # Tattletale §6 case 1: name the last known agent.
                return self._done(BROKEN_LINEAGE, agent, current, hops, claim_id)
            carried = [p for p in parent_ids if self._claims[p][1] == content]
            if not carried:
                # Tattletale §6 case 3: it differs from what it received, so
                # this agent changed it and owns it.
                return self._done(TRACED, agent, current, hops, claim_id)
            current = carried[0]
            hops += 1
        # Tattletale §6 case 2: more hops than claims means a cycle.
        agent = self._claims[current][0]
        return self._done(BROKEN_LINEAGE, agent, current, hops, claim_id)

    def _done(self, status: str, agent: str, origin_claim: str, hops: int, start: str):
        if status == BROKEN_LINEAGE:
            self.broken += 1
        if agent != self._claims[start][0]:
            self.redirected += 1
        return Attribution(status, agent, origin_claim, hops)
