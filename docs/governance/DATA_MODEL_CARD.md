# Data / model card

Owner: Melinda Emerson · Version: `0.2.0.dev0` · Reviewed: October 11, 2026

## Intended use and model identity

HARPY supports research on selective agent oversight. It helps identify which detector quality, audit price, review demand, and lineage reliability would justify a real-trace trial. It is not validated for autonomous production enforcement, high-stakes decisions, or claims of real-world savings.

| Component | Identity and evidence |
| --- | --- |
| Simulation sentinel | `MockSentinel`; configured weighted coin flips. It intentionally reads corruption labels as a simulation exception. |
| Replay reference | `MechanicalDetector`, `typed-checks/1`; deterministic structural/typed checks, measured on the included synthetic fixture. |
| Optional model adapter | `OpenAIResponsesDetector`; externally hosted `gpt-4.1-2025-04-14`; strict JSON decisions and observable evidence IDs. Adapter behavior is tested with an injected transport. No live-model accuracy has been measured. |
| Worker pricing identity | `gpt-4.1-mini-2025-04-14` in the v2 pricing file. Simulation workers do not call this model. |
| Training | No training, fine-tuning, learned parameters, or model weights are supplied by HARPY. Upstream model training is outside this project's evidence. |

## Data and rights

The frozen simulation uses 30 agents, 500 one-minute ticks, 24 synthetic subjects, and one faulty agent. OVERT faults are structural, MODERATE faults introduce typed conflicts, and SUBTLE faults perturb numeric values by 1–3%. Seeded generation and independent RNG streams support matched comparisons.

`examples/replay/synthetic` contains 12 authored development cases, with observations and seven positive/five negative labels in separate files. Synthetic data contains no human records; consent is not applicable. The corpus and source code are MIT-licensed. It has no training/validation/test split and was designed to exercise known mechanical behavior. It cannot establish held-out generalization.

Imported real traces must bring their own recording provenance, rights, consent, sanitization, labeling procedure, and held-out partition. Required metadata fields are declarations, not proof that those steps happened.

## Evaluation

V2 freezes 30 seeds and compares five arms across severity, budget, lineage fidelity, fleet scale, and two review-cost policies. Results include 1,800 distinct simulations and 7,200 recosted records; accounting variants are not independent observations. Matching includes source/input identity, seed, pricing tier, assumed detector quality, attention cap, scale, and review policy.

The primary HYBRID/SUBTLE cell targets a detection gain of at least 0.10 above matched `TIER0_ONLY`, with incremental overhead below 0.10. Conservative nominal 95% detection differences combine two 97.5% Wilson intervals; paired bootstrap effects and mean-cost intervals use 2,000 seeded resamples. A pass requires the gain's lower bound and cost's upper bound to clear both targets. `FAIL`, `INCONCLUSIVE`, and `INCOMPLETE` are distinct. Other cells are exploratory and have no simultaneous-coverage claim.

Replay records TP/FN/FP/TN, Wilson intervals, latency, measured token usage, estimated list-price cost, errors, and unattempted cases. Refusals and incomplete provider responses are failed cases, never clear verdicts. A cited evidence ID must exist in the input; that validates references, not the truth of a semantic judgment.

[Published study findings](../STUDY_V2.md) and [evidence packet](../../evidence/study-v2/) supply the source/input identities and seed-level data. The [replay guide](../REPLAY.md) describes the separate detector workflow.

## Limitations and review triggers

Synthetic malfunctions do not measure adaptive attackers, real task coverage, demographic fairness, or clinical/industrial safety. Fixed and linear review policies bracket accounting assumptions; neither models queueing, staffing economies, or response congestion. Provider-price verification is a metadata gate, not continuous price monitoring. Token-cost estimates exclude infrastructure, discounts, and invoices.

Ground-truth guards and rejected label fields catch accidental leakage but do not sandbox arbitrary Python or detect answers encoded in prose. Artifacts have SHA-256 hashes anchored to a trusted manifest, not digital signatures or external provenance attestations. Replay's prompt discourages following trace instructions; prompt-injection robustness is unmeasured.

Review this card when the corpus, model, prompt, schema, thresholds, cost policies, rights, workload, or evaluation changes. Freeze a new experiment before tuning against results.
