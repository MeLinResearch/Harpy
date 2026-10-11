# Data Bill of Materials

Owner: Melinda Emerson · Version: `0.2.0.dev0` · Reviewed: October 11, 2026

This inventory describes the data and assumptions used by HARPY. [dbom.json](dbom.json) provides a machine-readable counterpart. It is a project-specific DBOM, not a certified CycloneDX/SPDX software bill of materials.

| ID | Asset and origin | Transformation / use | Rights, integrity, limits |
| --- | --- | --- | --- |
| D01 | Synthetic claims/messages from `mesh.py` | Seeded typed generation, fault propagation, and worker-cost accounting | Authored generator: MIT; no personal records. Standard simulation results do not export the complete message trace. |
| D02 | Planted fault labels and true lineage | Scoring and the explicit mock-sentinel exception | Generated research labels, not independent human annotations. Guarded against accidental detection-path access. |
| D03 | Observable lineage from `lineage.py` | Lossy projection of true edges with missing/spurious links; downstream flagging | Fidelity is an assumption. Observable and true graphs remain separate. |
| D04 | Frozen `configs/default.yaml` | Workload, fault distribution, detector assumptions, thresholds, attention and review parameters | V1 file preserved. Complete parsed configuration and hash embedded in every v2 run. |
| D05 | `configs/pricing.study.yaml` | Official OpenAI text-token list prices, accessed October 10, 2026; exact model snapshots | URLs/date and parsed pricing hash retained. External provider pages retain their terms. Prices do not verify hypothetical detector accuracy. |
| D06 | Placeholder and smoke pricing | `pricing.yaml` is rejected normally; `pricing.smoke.yaml` exercises wiring with an explicit override | Authored assumptions; synthetic prices are marked unverified and figures watermarked. |
| D07 | Human review and engineering assumptions | $75/hour, 3-minute triage, 45-minute investigation, 25% escalation, 20-tick batching; fixed/linear review recosting | Modeled behavior, not measured staffing data. Tier 0 unit cost is an engineering assumption. No imported BLS dataset is used. |
| D08 | `configs/study.v2.yaml` | Frozen grid, primary cell, numeric criteria, uncertainty procedure | Authored MIT specification, committed before study collection and copied to evidence with its raw-file hash. |
| D09 | Synthetic replay corpus | Authored generator writes observable cases and labels into separate JSONL files | MIT, development-only, no human consent needed; explicit origin/partition and SHA-256 hashes. |
| D10 | Detector prompt/schema/model identity | Observable-only request; strict JSON; provider decision and usage | Prompt/schema/request/response hashes; externally hosted model not distributed. No live model result is in this release's evidence. |
| D11 | Run JSON, Parquet, compressed seed metrics, reports, plots | Scoring and matched statistical analysis | Conditional simulation outputs; full input/source hashes and file manifests. Published authored outputs: MIT. |
| D12 | Replay predictions and report | Predict first, join separate labels for confusion matrix and intervals | Corpus and prediction hashes, exact detector identity, cost/error accounting. Fixture results do not imply real-trace performance. |
| D13 | Executing Python source and dependency lock | Source tree digest plus `pyproject.toml` / `uv.lock` hashes | Git commit and content identity complement each other. Dependency versions are reproducible; dependency license/security certification is not claimed. |

## Data flow and lineage

Simulation generation (D01/D02) depends on D04. Observables and projected lineage (D01/D03) feed oversight; D02 feeds scoring and the intentionally simulated mock. D05/D07 price the six cost components. D08 groups and scores D11. Every run links its input hashes to the same experiment fingerprint, while run specifications distinguish seeds and policies.

Replay reads D09 observations into D10. D09 labels stay with the scorer and are joined only after D12 predictions are recorded. File hashes connect the corpus metadata, predictions, report, and artifact manifest. Both paths identify D13, so a change in executing source or locked dependencies changes experiment identity.

`harpy verify --manifest ...` detects changed, missing, unlisted, unsafe-path, and inconsistent-parent artifacts against a trusted manifest. It also checks the run's internal input hashes. A manifest replaced together with its files cannot establish authenticity; signatures and external attestations are not implemented.

There is no DVC, OpenLineage, Atlas, or OpenMetadata integration, and no persistent exported claim-lineage catalog. These omissions are documented rather than implied by the runtime artifact manifest. Real traces require a separate provenance and permissions record.

[AI label](AI_NUTRITION_LABEL.md) · [Data / model card](DATA_MODEL_CARD.md) · [Study evidence](../../evidence/study-v2/)
