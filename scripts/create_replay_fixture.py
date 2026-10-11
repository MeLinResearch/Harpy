"""Create explicit synthetic development cases, never a real-trace benchmark."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def main() -> None:
    directory = Path(__file__).resolve().parent.parent / "examples/replay/synthetic"
    directory.mkdir(parents=True, exist_ok=True)
    observations, labels = [], []
    for index in range(12):
        payload = {
            "subject_id": "s00",
            "field_name": "price_usd",
            "value_num": 100.0,
            "value_date": None,
            "value_enum": None,
            "schema_version": 3,
        }
        if index == 1:
            payload.update(field_name="review_date", value_num=None, value_date="2026-01-01")
        if index == 2:
            payload.update(field_name="status", value_num=None, value_enum="active")
        if index == 5:
            payload["schema_version"] = 9
        if index == 6:
            payload["value_num"] = None
        if index == 7:
            payload["value_num"] = 12000.0
        claims = [{"claim_id": f"c{index:02d}a", "payload": payload}]
        if index in (8, 9, 10, 11):
            changed = dict(payload)
            changed["value_num"] = {8: 200.0, 9: 102.0, 10: 105.0, 11: 101.0}[index]
            claims.append({"claim_id": f"c{index:02d}b", "payload": changed})
        observation = {
            "case_id": f"case{index:02d}",
            "agent_id": "a00",
            "window": [
                {
                    "message_id": f"m{index:02d}",
                    "tick": index,
                    "malformed": index == 3,
                    "tool_error": index == 4,
                    "claims": claims,
                }
            ],
        }
        observations.append(observation)
        labels.append({"case_id": observation["case_id"], "fault_present": index in range(3, 10)})
    for name, values in (("observations", observations), ("labels", labels)):
        (directory / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in values),
            encoding="utf-8",
            newline="\n",
        )
    metadata = {
        "schema": "harpy/replay-corpus/1",
        "dataset_id": "harpy-synthetic-development/1",
        "data_origin": "synthetic_fixture",
        "partition": "development",
        "source": "Hand-authored typed/structural fixtures; scripts/create_replay_fixture.py",
        "license": "MIT",
        "consent": "not_applicable_synthetic",
        "sanitization": "Synthetic identifiers and values; no human records",
        "files_sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("observations.jsonl", "labels.jsonl")
        },
        "limitation": "The slight numeric error is labelled by construction; no model sees "
        "that label or has enough evidence to infer its truth reliably.",
    }
    (directory / "dataset.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
