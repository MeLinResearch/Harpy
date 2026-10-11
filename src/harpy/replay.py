"""Replay labelled corpora without exposing the answer key to a detector."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from statistics import fmean

from .detectors import Detector, DetectorError, check_observation
from .ledger import Pricing, require_verified_pricing
from .provenance import digest, file_digest, source_identity, write_manifest
from .statistics import wilson_interval


def read_jsonl(path: Path) -> list[dict]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("JSONL rows must be objects")
    return rows


def load_corpus(directory: str | Path) -> tuple[dict, list[dict], dict[str, bool]]:
    directory = Path(directory)
    metadata = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
    if metadata.get("schema") != "harpy/replay-corpus/1":
        raise ValueError("unsupported replay corpus")
    for field in (
        "dataset_id",
        "data_origin",
        "partition",
        "source",
        "license",
        "consent",
        "sanitization",
    ):
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            raise ValueError(f"dataset metadata needs {field}")
    if metadata["data_origin"] not in ("real_traces", "synthetic_fixture"):
        raise ValueError("data_origin must declare real_traces or synthetic_fixture")
    for filename in ("observations.jsonl", "labels.jsonl"):
        if file_digest(directory / filename) != metadata.get("files_sha256", {}).get(filename):
            raise ValueError(f"corpus hash mismatch: {filename}")
    observations = read_jsonl(directory / "observations.jsonl")
    labels = read_jsonl(directory / "labels.jsonl")
    identifiers = []
    for observation in observations:
        check_observation(observation)
        if not isinstance(observation.get("case_id"), str) or not observation["case_id"]:
            raise ValueError("each observation needs a case_id")
        identifiers.append(observation["case_id"])
    if not identifiers or len(set(identifiers)) != len(identifiers):
        raise ValueError("corpus must be nonempty and observation IDs unique")
    label_map = {}
    for label in labels:
        if set(label) != {"case_id", "fault_present"} or type(label["fault_present"]) is not bool:
            raise ValueError("labels need only case_id and a boolean fault_present")
        if label["case_id"] in label_map:
            raise ValueError("duplicate label ID")
        label_map[label["case_id"]] = label["fault_present"]
    if set(identifiers) != set(label_map):
        raise ValueError("observations and labels must have identical ID sets")
    return metadata, observations, label_map


def score_predictions(predictions: list[dict], labels: dict[str, bool]) -> dict:
    completed = [row for row in predictions if row["status"] == "completed"]
    tp = sum(labels[r["case_id"]] and r["detection"]["flagged"] for r in completed)
    fn = sum(labels[r["case_id"]] and not r["detection"]["flagged"] for r in completed)
    fp = sum(not labels[r["case_id"]] and r["detection"]["flagged"] for r in completed)
    tn = sum(not labels[r["case_id"]] and not r["detection"]["flagged"] for r in completed)
    return {
        "true_positive": tp,
        "false_negative": fn,
        "false_positive": fp,
        "true_negative": tn,
        "detection_rate": tp / (tp + fn) if tp + fn else None,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        "detection_ci95": wilson_interval(tp, tp + fn),
        "false_positive_ci95": wilson_interval(fp, fp + tn),
        "completed_cases": len(completed),
        "failed_cases": len(predictions) - len(completed),
        "corpus_cases": len(labels),
        "unattempted_cases": len(labels) - len(predictions),
        "mean_duration_seconds": fmean(r["detection"]["duration_seconds"] for r in completed)
        if completed
        else None,
    }


def run_replay(
    corpus_dir: str | Path,
    detector: Detector,
    out_dir: str | Path,
    pricing: Pricing | None = None,
    max_dollars: float = 0.25,
    max_cases: int = 20,
) -> Path:
    if not math.isfinite(max_dollars) or max_dollars <= 0 or max_cases <= 0:
        raise ValueError("replay limits must be finite and positive")
    metadata, observations, labels = load_corpus(corpus_dir)
    detector_meta = detector.metadata()
    paid = detector_meta["kind"] != "mechanical"
    if paid:
        if pricing is None:
            raise ValueError("model replay requires sourced pricing")
        require_verified_pricing(pricing, context="replay")
        if pricing.sentinel_model.model != detector_meta.get("model"):
            raise ValueError("sentinel pricing must name the exact replay model snapshot")
        if any(
            not math.isfinite(v) or v <= 0
            for v in (pricing.sentinel_model.input_per_mtok, pricing.sentinel_model.output_per_mtok)
        ):
            raise ValueError("paid replay requires positive finite sentinel prices")
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("use an empty replay output directory")
    out_dir.mkdir(parents=True, exist_ok=True)
    spent, unknown_reserved, predictions, stop_reason = 0.0, 0.0, [], None
    predictions_path = out_dir / "predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8") as stream:
        for observation in observations[:max_cases]:
            bound = detector.token_bound(observation)
            reservation = pricing.sentinel_model.cost(*bound) if paid else 0.0
            if spent + reservation > max_dollars:
                stop_reason = "cost_cap"
                break
            try:
                result = detector.detect(observation)
            except (DetectorError, ValueError) as exc:
                unknown_reserved += reservation
                predictions.append(
                    {
                        "case_id": observation["case_id"],
                        "status": "error",
                        "error": str(exc),
                        "reserved_dollars": reservation,
                    }
                )
                stream.write(json.dumps(predictions[-1], sort_keys=True) + "\n")
                stream.flush()
                # Incomplete/refused responses may still be charged. Stop rather
                # than undercount unknown spending or silently score errors clear.
                stop_reason = "detector_error_charge_unknown" if paid else "detector_error"
                break
            cost = (
                pricing.sentinel_model.cost(result.input_tokens, result.output_tokens)
                if paid
                else 0.0
            )
            spent += cost
            entry = {
                "case_id": observation["case_id"],
                "status": "completed",
                "observation_sha256": digest(observation),
                "detection": asdict(result),
                "estimated_uncached_dollars": cost,
            }
            predictions.append(entry)
            stream.write(json.dumps(entry, sort_keys=True) + "\n")
            stream.flush()
            if cost > reservation:
                stop_reason = "usage_exceeded_preflight_bound"
                break
    if stop_reason is None and len(observations) > max_cases:
        stop_reason = "case_cap"
    report = {
        "schema": "harpy/replay-report/1",
        "dataset": metadata,
        "dataset_metadata_sha256": file_digest(Path(corpus_dir) / "dataset.json"),
        "source": source_identity(),
        "detector": detector_meta,
        "pricing": pricing.as_dict() if pricing else None,
        "evidence_kind": metadata["data_origin"],
        "metrics": score_predictions(predictions, labels),
        "estimated_uncached_dollars": spent,
        "max_dollars": max_dollars,
        "unknown_charge_reserved_dollars": unknown_reserved,
        "estimated_plus_reserved_dollars": spent + unknown_reserved,
        "max_cases": max_cases,
        "stop_reason": stop_reason,
        "real_model_called": bool(predictions)
        and detector_meta["kind"] == "openai_responses"
        and detector_meta.get("transport") == "https",
        "cost_note": "Measured token usage priced at uncached list rates; not an invoice. "
        "An error may have unknown usage/charges.",
        "predictions_sha256": file_digest(predictions_path),
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_manifest(out_dir)
    return path
