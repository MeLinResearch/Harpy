"""v3 attribution: Tattletale's origin walk on HARPY's observable lineage."""

from __future__ import annotations

import random

import pytest

from harpy.attribution import BROKEN_LINEAGE, TRACED, Attributor, content_key
from harpy.lineage import ProvenanceLog
from harpy.metrics import run_metrics
from harpy.sentinel import MockSentinel
from harpy.simulation import RunSpec, run_simulation
from harpy.types import Arm, LineageEdge, Severity

from conftest import make_claim, make_message, make_payload


def emit(attributor, agent, claim_id, value=100.0, schema=3, **payload):
    claim = make_claim(claim_id, agent, payload=make_payload(value_num=value,
                                                            schema_version=schema, **payload))
    attributor.observe(make_message(agent, claims=(claim,)))
    return claim


def parents(mapping):
    return lambda claim_id: tuple(sorted(mapping.get(claim_id, ())))


# -- the walk: Tattletale ARCHITECTURE.md §6 -----------------------------------


def test_unchanged_carries_pass_blame_upstream():
    """researcher -> editor -> summarizer, carried verbatim: researcher owns it."""
    a = Attributor()
    emit(a, "researcher", "c1", 24.0)
    emit(a, "editor", "c2", 24.0)
    emit(a, "summarizer", "c3", 24.0)
    trace = a.trace("c3", parents({"c3": ["c2"], "c2": ["c1"]}))
    assert (trace.status, trace.origin_agent_id, trace.origin_claim_id, trace.hops) == (
        TRACED, "researcher", "c1", 2)


def test_an_agent_that_changes_a_claim_owns_it():
    """The case that earns the name: clean upstream, altered in the middle."""
    a = Attributor()
    emit(a, "researcher", "c1", 12.0)
    emit(a, "editor", "c2", 12.36)  # a 3% perturbation of what it received
    emit(a, "summarizer", "c3", 12.36)
    trace = a.trace("c3", parents({"c3": ["c2"], "c2": ["c1"]}))
    assert (trace.origin_agent_id, trace.origin_claim_id) == ("editor", "c2")


def test_a_root_claim_belongs_to_its_submitter():
    a = Attributor()
    emit(a, "researcher", "c1")
    assert a.trace("c1", parents({})).origin_agent_id == "researcher"
    assert a.redirected == 0


def test_a_missing_parent_is_broken_lineage_naming_the_last_known_agent():
    a = Attributor()
    emit(a, "editor", "c2")
    emit(a, "summarizer", "c3")
    trace = a.trace("c3", parents({"c3": ["c2"], "c2": ["never-seen"]}))
    assert (trace.status, trace.origin_agent_id) == (BROKEN_LINEAGE, "editor")


def test_a_cycle_is_broken_lineage_not_an_infinite_walk():
    a = Attributor()
    emit(a, "x", "c1")
    emit(a, "y", "c2")
    trace = a.trace("c1", parents({"c1": ["c2"], "c2": ["c1"]}))
    assert trace.status == BROKEN_LINEAGE


def test_schema_restamping_is_not_a_change():
    a = Attributor()
    emit(a, "researcher", "c1", 5.0, schema=3)
    emit(a, "editor", "c2", 5.0, schema=4)
    assert a.trace("c2", parents({"c2": ["c1"]})).origin_agent_id == "researcher"
    old, new = make_payload(schema_version=3), make_payload(schema_version=9)
    assert content_key(old) == content_key(new)


def test_a_spurious_parent_does_not_capture_blame_from_the_real_one():
    a = Attributor()
    emit(a, "origin", "c1", 7.0)
    emit(a, "bystander", "c0", 99.0)
    emit(a, "relay", "c2", 7.0)
    trace = a.trace("c2", parents({"c2": ["c0", "c1"]}))
    assert trace.origin_agent_id == "origin"


def test_an_unobserved_claim_cannot_be_traced():
    assert Attributor().trace("nope", parents({})) is None


# -- the provenance log ------------------------------------------------------


def edges(n):
    return [LineageEdge(f"p{i}", f"c{i}") for i in range(n)]


def test_full_fidelity_records_every_true_edge_and_zero_records_none():
    full = ProvenanceLog(1.0, 0.0, random.Random(0))
    full.extend(edges(50))
    assert all(full.parents_of(f"c{i}") == (f"p{i}",) for i in range(50))
    empty = ProvenanceLog(0.0, 0.0, random.Random(0))
    empty.extend(edges(50))
    assert all(empty.parents_of(f"c{i}") == () for i in range(50))


def test_spurious_edges_are_never_true_edges_and_the_log_is_deterministic():
    def build():
        log = ProvenanceLog(0.5, 0.3, random.Random(4))
        log.extend(edges(300))
        return log

    a, b = build(), build()
    assert a._parents == b._parents
    true = set(edges(300))
    recorded = {LineageEdge(p, c) for c, ps in a._parents.items() for p in ps}
    assert a.recorded_spurious > 0
    assert len(recorded - true) == a.recorded_spurious
    assert 100 < a.recorded_true < 200


# -- the sentinel names evidence only when asked -------------------------------


def sentinel(evidence_accuracy=None, seed=0):
    return MockSentinel(
        detection_prob={Severity.SUBTLE: 1.0}, false_positive_rate=0.0, input_tokens=9000,
        output_tokens=600, rng=random.Random(seed), severity=Severity.SUBTLE,
        evidence_accuracy=evidence_accuracy,
    )


def window():
    claims = tuple(make_claim(f"c{i}", is_corrupt=(i == 3)) for i in range(6))
    return (make_message(claims=claims),)


def test_no_evidence_and_no_extra_draws_when_attribution_is_off():
    off = sentinel(None, seed=5)
    v2 = sentinel(None, seed=5)
    assert off.audit("a", window()).evidence_claim_ids == ()
    assert off.rng.random() == (v2.audit("a", window()), v2.rng.random())[1]


def test_a_true_detection_names_the_corrupt_claim_at_full_accuracy():
    s = sentinel(1.0)
    assert all(s.audit("a", window()).evidence_claim_ids == ("c3",) for _ in range(20))


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_evidence_accuracy_is_a_probability(bad):
    with pytest.raises(ValueError):
        sentinel(bad)


# -- through the simulation ---------------------------------------------------


def spec(traced, seed=0, k=4):
    return RunSpec(seed, Severity.SUBTLE, Arm.HYBRID, 0.2, 1.0, 0.5,
                   followup_audits=k, attribution=traced)


def test_run_id_marks_attribution_only_when_on():
    assert "_t" not in spec(False).run_id()
    assert spec(True).run_id().endswith("_k4_t")
    assert spec(True).as_dict()["attribution"] is True


def test_attribution_stops_isolating_relays(config, test_pricing):
    totals = {False: [0, 0], True: [0, 0]}
    for seed in range(4):
        for traced in totals:
            row = run_metrics(run_simulation(config, spec(traced, seed), test_pricing))
            totals[traced][0] += row["relay_isolations"]
            totals[traced][1] += row["detection_rate"]
    assert totals[True][0] < totals[False][0]
    assert totals[True][1] >= totals[False][1]


def test_attribution_runs_are_deterministic_and_scored(config, test_pricing):
    a = run_metrics(run_simulation(config, spec(True, 2), test_pricing))
    b = run_metrics(run_simulation(config, spec(True, 2), test_pricing))
    assert a == b
    assert a["attribution_walks"] > 0
    assert a["attribution_to_fault_source"] <= a["attribution_on_corrupt"] <= a["attribution_walks"]
