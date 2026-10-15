"""Ground-truth lineage and the lossy projection HARPY is allowed to see.

Real deployments do not get a perfect provenance graph: some edges are never
recorded, and some recorded edges are wrong. ``lineage_fidelity`` is swept
precisely because how much oversight is worth buying depends on how good the
provenance plumbing already is.
"""

from __future__ import annotations

import random

from .types import LineageEdge, LineagePair


def _sort_key(edge: LineageEdge) -> tuple[str, str]:
    return (edge.parent_claim_id, edge.child_claim_id)


def build_observable(
    ground_truth: frozenset[LineageEdge],
    fidelity: float,
    spurious_rate: float,
    rng: random.Random,
) -> frozenset[LineageEdge]:
    """Project ground-truth lineage into what the platform actually records.

    Each true edge survives with probability ``fidelity``. Spurious edges are
    then added at a count of ``round(spurious_rate * len(ground_truth))``,
    drawn from (parent, child) pairs that do not exist in ground truth.
    """
    if not 0.0 <= fidelity <= 1.0:
        raise ValueError(f"fidelity must be in [0, 1], got {fidelity}")
    if spurious_rate < 0.0:
        raise ValueError(f"spurious_rate must be >= 0, got {spurious_rate}")

    true_edges = sorted(ground_truth, key=_sort_key)
    observable: set[LineageEdge] = {e for e in true_edges if rng.random() < fidelity}

    claim_ids = sorted(
        {e.parent_claim_id for e in true_edges} | {e.child_claim_id for e in true_edges}
    )
    wanted = int(round(spurious_rate * len(true_edges)))
    if wanted <= 0 or len(claim_ids) < 2:
        return frozenset(observable)

    # Cannot invent more distinct fake edges than the id universe allows.
    capacity = len(claim_ids) * (len(claim_ids) - 1) - len(true_edges)
    wanted = min(wanted, max(capacity, 0))

    added = 0
    attempts = 0
    attempt_cap = 200 * max(wanted, 1)
    while added < wanted and attempts < attempt_cap:
        attempts += 1
        parent = claim_ids[rng.randrange(len(claim_ids))]
        child = claim_ids[rng.randrange(len(claim_ids))]
        if parent == child:
            continue
        edge = LineageEdge(parent, child)
        if edge in ground_truth or edge in observable:
            continue
        observable.add(edge)
        added += 1

    return frozenset(observable)


def make_pair(
    ground_truth: frozenset[LineageEdge],
    fidelity: float,
    spurious_rate: float,
    rng: random.Random,
) -> LineagePair:
    """Bundle ground truth with its projection. Only the projection is readable."""
    return LineagePair(
        ground_truth=ground_truth,
        observable=build_observable(ground_truth, fidelity, spurious_rate, rng),
    )


def children_map(observable: frozenset[LineageEdge]) -> dict[str, list[str]]:
    """Adjacency for the observable graph. Callers cache it; walks are frequent."""
    children: dict[str, list[str]] = {}
    for edge in sorted(observable, key=_sort_key):
        children.setdefault(edge.parent_claim_id, []).append(edge.child_claim_id)
    return children


def descendants(
    observable: frozenset[LineageEdge],
    claim_id: str,
    children: dict[str, list[str]] | None = None,
) -> frozenset[str]:
    """Transitive children of ``claim_id`` in the OBSERVABLE graph.

    Used by :mod:`harpy.response` to decide what to flag downstream. Because the
    graph is lossy this both misses real descendants and flags innocent ones —
    that is the point of the fidelity sweep.
    """
    if children is None:
        children = children_map(observable)

    seen: set[str] = set()
    stack = list(children.get(claim_id, ()))
    while stack:
        node = stack.pop()
        if node in seen or node == claim_id:
            continue
        seen.add(node)
        stack.extend(children.get(node, ()))
    return frozenset(seen)


class ProvenanceLog:
    """v3: the lossy lineage a provenance store writes as claims are emitted.

    Same loss model as :func:`build_observable`: each true edge is recorded with
    probability ``fidelity``, and spurious edges appear at ``spurious_rate`` per
    true edge between claims that are not actually related. The difference is
    timing. ``build_observable`` redraws the whole projection from the full edge
    set, which is what downstream flagging uses and what v1/v2 evidence was
    computed with; this log decides each edge once, when it is created, from its
    own RNG stream. That is how a real store behaves, and it lets attribution
    walk lineage every tick without rebuilding the graph every tick.

    Only :meth:`parents_of` is meant for HARPY-side code: it returns observable
    parents and nothing about which edges were real.
    """

    def __init__(self, fidelity: float, spurious_rate: float, rng: random.Random) -> None:
        if not 0.0 <= fidelity <= 1.0:
            raise ValueError(f"fidelity must be in [0, 1], got {fidelity}")
        if spurious_rate < 0.0:
            raise ValueError(f"spurious_rate must be >= 0, got {spurious_rate}")
        self.fidelity = float(fidelity)
        self.spurious_rate = float(spurious_rate)
        self.rng = rng
        self._true: set[LineageEdge] = set()
        self._ids: list[str] = []
        self._known: set[str] = set()
        self._parents: dict[str, list[str]] = {}
        self.recorded_true = 0
        self.recorded_spurious = 0

    def _remember(self, claim_id: str) -> None:
        if claim_id not in self._known:
            self._known.add(claim_id)
            self._ids.append(claim_id)

    def _record(self, edge: LineageEdge) -> None:
        self._parents.setdefault(edge.child_claim_id, []).append(edge.parent_claim_id)

    def extend(self, edges) -> None:
        """Record newly created true edges, in creation order."""
        for edge in edges:
            self._true.add(edge)
            self._remember(edge.parent_claim_id)
            self._remember(edge.child_claim_id)
            if self.rng.random() < self.fidelity:
                self._record(edge)
                self.recorded_true += 1
            if self.rng.random() < self.spurious_rate and len(self._ids) >= 2:
                for _ in range(200):
                    parent = self._ids[self.rng.randrange(len(self._ids))]
                    child = self._ids[self.rng.randrange(len(self._ids))]
                    fake = LineageEdge(parent, child)
                    if parent != child and fake not in self._true:
                        self._record(fake)
                        self.recorded_spurious += 1
                        break

    def parents_of(self, claim_id: str) -> tuple[str, ...]:
        return tuple(sorted(self._parents.get(claim_id, ())))
