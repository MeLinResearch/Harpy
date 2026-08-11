"""The observable projection: what survives, what is invented, what HARPY sees."""

from __future__ import annotations

import random

import pytest

from harpy.lineage import build_observable, descendants, make_pair
from harpy.types import LineageEdge


def ground_truth(n: int = 200) -> frozenset[LineageEdge]:
    return frozenset(LineageEdge(f"claim-{i}", f"claim-{i + 1}") for i in range(n))


def test_fidelity_one_reproduces_ground_truth():
    truth = ground_truth()
    observable = build_observable(truth, fidelity=1.0, spurious_rate=0.0, rng=random.Random(0))
    assert observable == truth


def test_fidelity_zero_yields_no_true_edges():
    truth = ground_truth()
    observable = build_observable(truth, fidelity=0.0, spurious_rate=0.0, rng=random.Random(0))
    assert observable == frozenset()


def test_fidelity_zero_still_admits_spurious_edges():
    truth = ground_truth()
    observable = build_observable(truth, fidelity=0.0, spurious_rate=0.25, rng=random.Random(0))
    assert len(observable) == 50
    assert observable.isdisjoint(truth)


@pytest.mark.parametrize("spurious_rate", [0.0, 0.1, 0.25, 0.5, 1.0])
def test_spurious_rate_produces_the_expected_count(spurious_rate):
    truth = ground_truth()
    observable = build_observable(
        truth, fidelity=1.0, spurious_rate=spurious_rate, rng=random.Random(11)
    )
    invented = observable - truth
    assert len(invented) == round(spurious_rate * len(truth))
    assert len(observable) == len(truth) + len(invented)


@pytest.mark.parametrize("fidelity", [0.25, 0.5, 0.75])
def test_partial_fidelity_retains_roughly_that_share(fidelity):
    truth = ground_truth(4000)
    observable = build_observable(
        truth, fidelity=fidelity, spurious_rate=0.0, rng=random.Random(5)
    )
    assert len(observable) / len(truth) == pytest.approx(fidelity, abs=0.03)


def test_projection_is_deterministic_for_a_given_seed():
    truth = ground_truth()
    first = build_observable(truth, 0.5, 0.2, random.Random(99))
    second = build_observable(truth, 0.5, 0.2, random.Random(99))
    assert first == second


def test_make_pair_keeps_ground_truth_and_projection_separate():
    truth = ground_truth(50)
    pair = make_pair(truth, fidelity=0.5, spurious_rate=0.0, rng=random.Random(2))
    assert pair.observable <= truth
    assert len(pair.observable) < len(truth)
    # Reading ground truth from a scoring context (this test) is allowed.
    assert pair.ground_truth == truth


def test_descendants_walks_the_observable_chain_only():
    truth = frozenset(
        {
            LineageEdge("a", "b"),
            LineageEdge("b", "c"),
            LineageEdge("c", "d"),
            LineageEdge("x", "y"),
        }
    )
    assert descendants(truth, "a") == frozenset({"b", "c", "d"})
    assert descendants(truth, "y") == frozenset()

    broken = frozenset({LineageEdge("a", "b"), LineageEdge("c", "d")})
    assert descendants(broken, "a") == frozenset({"b"})


def test_descendants_survives_a_cycle_in_a_spurious_graph():
    cyclic = frozenset({LineageEdge("a", "b"), LineageEdge("b", "a")})
    assert descendants(cyclic, "a") == frozenset({"b"})


def test_invalid_fidelity_is_rejected():
    with pytest.raises(ValueError):
        build_observable(ground_truth(2), fidelity=1.5, spurious_rate=0.0, rng=random.Random(0))
