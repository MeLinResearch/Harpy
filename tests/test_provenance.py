from __future__ import annotations

import copy
import json

import pytest

from harpy.provenance import capture_inputs, verify_inputs, verify_manifest, write_manifest
from harpy.simulation import RunSpec
from harpy.sweep import run_single
from harpy.types import Arm, Severity


def test_input_snapshot_is_complete_and_cannot_drift(config, test_pricing):
    before = capture_inputs(config, test_pricing.as_dict())
    assert before["config"] == config
    changed = copy.deepcopy(config)
    changed["telemetry"]["numeric_ranges"]["price_usd"][1] += 1
    after = capture_inputs(changed, test_pricing.as_dict())
    assert after["config_sha256"] != before["config_sha256"]
    assert after["experiment_fingerprint"] != before["experiment_fingerprint"]
    changed["worker"]["input_tokens_per_message"] = 0
    assert after["config"]["worker"]["input_tokens_per_message"] != 0
    assert "src/harpy/simulation.py" in before["source"]["files_sha256"]


def test_pricing_location_does_not_change_input_identity(config, test_pricing):
    first = test_pricing.as_dict()
    second = first | {"path": "/a/different/location.yaml"}
    assert capture_inputs(config, first) == capture_inputs(config, second)


def test_internal_input_hashes_cannot_drift(config, test_pricing):
    snapshot = capture_inputs(config, test_pricing.as_dict())
    assert verify_inputs(snapshot) == []
    snapshot["config"]["simulation"]["n_ticks"] = 1
    assert "inconsistent input hash: config_sha256" in verify_inputs(snapshot)


def test_parent_manifest_includes_nested_manifest(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    (child / "artifact.txt").write_text("evidence")
    nested = write_manifest(child)
    manifest = write_manifest(tmp_path)
    nested.write_text("{}")
    assert "changed artifact: child/manifest.json" in verify_manifest(manifest)


def test_manifest_detects_tampering_missing_and_unlisted_artifacts(tmp_path):
    artifact = tmp_path / "run.json"
    artifact.write_text('{"schema":"example"}')
    manifest = write_manifest(tmp_path)
    assert verify_manifest(manifest) == []
    artifact.write_text('{"schema":"changed"}')
    assert "changed artifact: run.json" in verify_manifest(manifest)
    artifact.unlink()
    assert "missing artifact: run.json" in verify_manifest(manifest)
    (tmp_path / "extra.json").write_text("{}")
    assert "unlisted artifact: extra.json" in verify_manifest(manifest)


def test_manifest_rejects_escape_paths(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "harpy/artifact-manifest/1",
                "artifacts": [{"path": "../outside", "sha256": "x", "size_bytes": 0}],
            }
        )
    )
    assert "unsafe artifact path: ../outside" in verify_manifest(path)


def test_run_cannot_silently_overwrite_a_different_experiment(tmp_path, config, test_pricing):
    spec = RunSpec(0, Severity.SUBTLE, Arm.NONE, 0.05, 1.0)
    run_single(config, spec, test_pricing, tmp_path)
    changed = copy.deepcopy(config)
    changed["simulation"]["n_ticks"] = 10
    with pytest.raises(ValueError, match="different inputs"):
        run_single(changed, spec, test_pricing, tmp_path)
