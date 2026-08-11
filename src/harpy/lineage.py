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
