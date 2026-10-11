# AI nutrition label / recipe card

Owner: Melinda Emerson · Version: `0.2.0.dev0` · Reviewed: October 11, 2026

| Field | HARPY |
| --- | --- |
| Purpose | Test which oversight budgets, detector characteristics, and review costs warrant further experiments on agent meshes. |
| Serving | A local simulation run, frozen sensitivity study, or separate recorded-case replay. No production fleet is operated. |
| AI ingredients | Simulation: `MockSentinel` with assumed probabilities. Replay: deterministic mechanical reference or optional `OpenAIResponsesDetector`, default snapshot `gpt-4.1-2025-04-14`. |
| Data ingredients | Generated typed claims/messages, planted malfunctions, observable lineage, configurations, sourced token prices, and modeled human-review effort. The included replay data is a synthetic development fixture. |
| Recipe | Generate a seeded world; apply seven mechanical signals; select affordable audits; accumulate suspicion; apply scoped responses; score against ground truth. Replay separately predicts from observables and then joins labels for scoring. |
| Training, fine-tuning, RAG | None. HARPY distributes no trained weights, embeddings, or retrieval corpus. The optional provider model is externally hosted. |
| Assumed accuracy | Simulation audit probabilities: OVERT 95%, MODERATE 78%, SUBTLE 42%; false-positive probability 2%. These are inputs, not measured model accuracy. |
| Cost ingredients | Sentinel, Tier 0, discarded work, reruns, triage, investigation. V2 compares fixed review cost with review that grows linearly with the fleet. Token list prices are sourced; staffing and engineering costs are assumptions. |
| Human oversight | Dollar and alert-rate budgets are independent. Review duration, escalation, and batching are modeled. The simulator does not send messages to people. |
| Outputs | Full run/input snapshots, JSON/Parquet summaries, uncertainty intervals, plots, replay predictions, and SHA-256 artifact manifests. |
| Quality target | At least 10 percentage points of incremental detection over matched Tier 0, with overhead below 10%; the frozen primary cell and intervals determine the v2 verdict. |
| Provenance controls | Complete configuration/pricing snapshots; source-content and lockfile hashes; experiment identity; corpus hashes; explicit artifact parents; integrity verification. |
| Known limits | Conditional simulation, small synthetic replay fixture, no measured live-model accuracy, no real-trace benchmark, no adversarial or demographic fairness conclusion. Hashes are not signatures; Python guards are not security sandboxes. |
| License and rights | Repository code and authored synthetic fixture: MIT. Provider model/API and external source material retain their own terms. |

[Study and result](../STUDY_V2.md) · [Data / model card](DATA_MODEL_CARD.md) · [DBOM](DBOM.md) · [Replay guide](../REPLAY.md)

This is a project-specific documentation example, not Siemens approval or a compliance certification. The supporting fields are grounded in the implementation and its published evidence.
