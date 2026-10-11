# HARPY-REPLAY

[Project](../README.md) · [Data / model card](governance/DATA_MODEL_CARD.md)

Replay measures a detector on recorded observable cases. It runs separately from HARPY-SIM, whose `MockSentinel` remains probabilistic. Replay results never overwrite the simulation's assumed detector characteristics.

## Run the included mechanical reference

```bash
uv run harpy replay --corpus examples/replay/synthetic \
  --detector mechanical --out results/replay-reference/
uv run harpy verify --manifest results/replay-reference/manifest.json
```

This corpus has 12 generated development cases: seven planted faults and five clean cases. Its numeric perturbation case illustrates a fault that the mechanical reference cannot infer from the supplied observations. It is a wiring check and a measured reference on this fixture, not a held-out benchmark or AI performance claim. Regenerate it with `uv run python scripts/create_replay_fixture.py`.

## Corpus contract

Use a directory containing the following files.

| File | Required content |
| --- | --- |
| `dataset.json` | Schema `harpy/replay-corpus/1`; dataset ID; `data_origin` (`synthetic_fixture` or `real_traces`); partition; source; license; consent; sanitization; SHA-256 hashes of the two JSONL files. |
| `observations.jsonl` | Unique neutral `case_id` values and observable event/message/claim fields. A claim uses typed payloads such as `subject_id`, `field_name`, `value_num`, and `schema_version`. |
| `labels.jsonl` | Exactly `case_id` and boolean `fault_present`, with the same case-ID set as observations. |

The loader verifies hashes and rejects duplicate, missing, or extra case IDs. Known answer fields such as `is_corrupt`, `ground_truth`, `fault_present`, and `severity` are forbidden even when nested inside observations. Labels are supplied only to the scorer. This structural check cannot detect answers hidden in arbitrary prose or identifiers; dataset review must address that leakage.

The mechanical reference implements structural failures, typed payload presence, numeric ranges, and large within-case conflicts. It is not the complete seven-signal simulation Tier 0, and it is not a semantic oracle. The model adapter can ingest free-text observables too; those must be treated as untrusted content and sanitized before uploading.

For a real-trace study, document the actual recording source, lawful rights and consent, secret/PII removal, annotation procedure, and a held-out partition. Do not infer those properties from a nonempty metadata field. Measure appropriate task labels, inter-annotator agreement, task coverage, and prompt-injection behavior in a separately frozen study.

## Optional real detector

Set `OPENAI_API_KEY` in your environment. Do not put the key in a config, command argument, trace, or result file.

```bash
uv run harpy replay --corpus path/to/approved-corpus \
  --detector openai --model gpt-4.1-2025-04-14 \
  --pricing configs/pricing.study.yaml \
  --max-dollars 0.25 --max-cases 20 \
  --out results/replay-model/
```

This command sends observables to OpenAI and can incur charges. The adapter uses [Structured Outputs in the Responses API](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses). The prompt, schema, request, and response are identified by hashes; no API key is included in evidence. `store: false` controls response storage, not every provider retention policy.

Replay requires verified, positive prices and an exact model-snapshot match. A conservative byte-based input bound plus output-token allowance is reserved before each call. It makes no automatic paid retries. Observed tokens are priced at uncached list rates, so dollar totals are estimates rather than invoices. Usage beyond the bound stops replay and is explicitly reported; the reservation is not a provider-enforced billing cap.

Incomplete, refused, malformed, or failed audits stop replay. They are reported as failed cases with unknown-charge reservations; they never become clear verdicts or false negatives. The report separates completed, failed, and unattempted cases, because scoring only completed cases can bias apparent accuracy. Interpret rates with those denominators and Wilson intervals.

Outputs are append-written `predictions.jsonl`, `report.json`, and `manifest.json`. Evidence states whether data is synthetic, whether a real HTTPS model call was attempted, and whether a transport was injected for tests. No live model was evaluated in the published v2 packet.
