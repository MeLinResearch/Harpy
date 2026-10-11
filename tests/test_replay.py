from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from harpy.detectors import (
    Detection,
    DetectorError,
    MechanicalDetector,
    OpenAIResponsesDetector,
    check_observation,
)
from harpy.ledger import ModelPrice, Pricing
from harpy.provenance import file_digest, verify_manifest
from harpy.replay import load_corpus, run_replay
from harpy.sentinel import RealSentinel

from conftest import make_claim, make_message

FIXTURE = Path(__file__).resolve().parents[1] / "examples/replay/synthetic"
MODEL = "gpt-4.1-2025-04-14"


def response(flagged=False, evidence_ids=()):
    return {
        "id": "fixture-response",
        "status": "completed",
        "model": MODEL,
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "flagged": flagged,
                                "reason": "Fixture verdict",
                                "evidence_ids": list(evidence_ids),
                            }
                        ),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


@pytest.fixture
def replay_pricing(test_pricing):
    return Pricing(
        worker_models=test_pricing.worker_models,
        sentinel_model=ModelPrice(2, 8, "test fixture", "n/a", model=MODEL),
        tier0_cost_per_message_dollars=0,
        verified=True,
        path="<fixture>",
    )


@pytest.mark.parametrize(
    "key",
    [
        "is_corrupt",
        "ground_truth",
        "labels",
        "fault_present",
        "expected",
        "severity",
        "faulty_agent_id",
    ],
)
def test_nested_answer_fields_are_rejected_before_transport(key):
    calls = []
    detector = OpenAIResponsesDetector("never-saved", transport=lambda p: calls.append(p))
    with pytest.raises(ValueError, match="ground-truth"):
        detector.detect({"window": [{"nested": {key: True}}]})
    assert calls == []


def test_real_sentinel_serializes_observables_without_corrupt_flag():
    seen = []

    class Spy:
        def detect(self, observation):
            check_observation(observation)
            seen.append(observation)
            return Detection(False, "No observable fault", input_tokens=12, output_tokens=3)

    sentinel = RealSentinel(Spy())
    claim = make_claim(is_corrupt=True)
    result = sentinel.audit("agent-00", (make_message(claims=(claim,)),))
    assert result.input_tokens == 12
    assert "is_corrupt" not in json.dumps(seen)
    assert seen[0]["window"][0]["claims"][0]["payload"] == claim.payload.as_dict()


def test_structured_request_and_usage_include_no_key_or_tools():
    observation = load_corpus(FIXTURE)[1][3]
    calls = []

    def transport(payload):
        calls.append(payload)
        return response(True, [observation["window"][0]["message_id"]])

    detector = OpenAIResponsesDetector("never-saved", transport=transport)
    result = detector.detect(observation)
    assert (result.input_tokens, result.output_tokens) == (100, 20)
    assert calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["store"] is False
    assert "tools" not in calls[0]
    assert "never-saved" not in json.dumps(calls + [detector.metadata(), result.provider])
    assert detector.metadata()["transport"] == "injected"


@pytest.mark.parametrize(
    "change, message",
    [
        ({"status": "incomplete"}, "incomplete"),
        ({"model": "different-model"}, "snapshot"),
        ({"usage": {"input_tokens": True, "output_tokens": 1}}, "usage"),
        ({"usage": {}}, "usage"),
        ({"output": [{"type": "message", "content": [{"type": "refusal"}]}]}, "refused"),
    ],
)
def test_invalid_provider_outcomes_are_errors(change, message):
    detector = OpenAIResponsesDetector("fixture", transport=lambda _: response() | change)
    with pytest.raises(DetectorError, match=message):
        detector.detect(load_corpus(FIXTURE)[1][0])


@pytest.mark.parametrize("ids", [[], ["invented-id"]])
def test_flag_needs_evidence_present_in_observations(ids):
    detector = OpenAIResponsesDetector("fixture", transport=lambda _: response(True, ids))
    with pytest.raises(DetectorError):
        detector.detect(load_corpus(FIXTURE)[1][0])


def test_unicode_preflight_bound_covers_serialized_input():
    observation = {"text": "\u2603" * 10000}
    detector = OpenAIResponsesDetector("fixture")
    bound, _ = detector.token_bound(observation)
    assert bound >= len(json.dumps(observation, sort_keys=True).encode())


def test_hash_mismatch_and_incomplete_labels_are_rejected(tmp_path):
    for path in FIXTURE.iterdir():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    labels = tmp_path / "labels.jsonl"
    labels.write_text(labels.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_corpus(tmp_path)
    metadata = json.loads((tmp_path / "dataset.json").read_text())
    metadata["files_sha256"]["labels.jsonl"] = file_digest(labels)
    (tmp_path / "dataset.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="identical ID sets"):
        load_corpus(tmp_path)


def test_synthetic_mechanical_replay_measures_known_reference(tmp_path):
    report_path = run_replay(FIXTURE, MechanicalDetector(), tmp_path)
    report = json.loads(report_path.read_text())
    assert report["metrics"]["true_positive"] == 6
    assert report["metrics"]["false_negative"] == 1
    assert report["metrics"]["false_positive"] == 0
    assert report["metrics"]["true_negative"] == 5
    assert report["evidence_kind"] == "synthetic_fixture"
    assert report["real_model_called"] is False
    assert report["estimated_uncached_dollars"] == 0
    assert verify_manifest(tmp_path / "manifest.json") == []


def test_cost_preflight_prevents_a_paid_call(tmp_path, replay_pricing):
    calls = []
    detector = OpenAIResponsesDetector("fixture", transport=lambda p: calls.append(p))
    path = run_replay(FIXTURE, detector, tmp_path, replay_pricing, max_dollars=1e-9)
    report = json.loads(path.read_text())
    assert calls == []
    assert report["stop_reason"] == "cost_cap"
    assert report["metrics"]["completed_cases"] == 0
    assert report["metrics"]["detection_rate"] is None
    assert report["real_model_called"] is False


def test_error_is_not_a_clear_verdict_and_reserves_unknown_charge(tmp_path, replay_pricing):
    detector = OpenAIResponsesDetector(
        "fixture", transport=lambda _: response() | {"status": "incomplete"}
    )
    path = run_replay(FIXTURE, detector, tmp_path, replay_pricing)
    report = json.loads(path.read_text())
    assert report["metrics"]["failed_cases"] == 1
    assert report["metrics"]["completed_cases"] == 0
    assert report["metrics"]["unattempted_cases"] == 11
    assert report["metrics"]["false_negative"] == 0
    assert report["unknown_charge_reserved_dollars"] > 0
    assert report["estimated_plus_reserved_dollars"] <= report["max_dollars"]


def test_exact_model_pricing_is_required_before_replay(tmp_path, replay_pricing):
    changed = copy.deepcopy(replay_pricing)
    changed = Pricing(
        worker_models=changed.worker_models,
        sentinel_model=ModelPrice(2, 8, "fixture", "n/a", model="other"),
        tier0_cost_per_message_dollars=0,
        verified=True,
        path="<fixture>",
    )
    detector = OpenAIResponsesDetector("fixture", transport=lambda _: response())
    with pytest.raises(ValueError, match="exact replay model"):
        run_replay(FIXTURE, detector, tmp_path, changed)
