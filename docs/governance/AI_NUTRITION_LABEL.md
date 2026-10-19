# AI nutrition label

<p align="center"><img src="../assets/harpy-nutrition.svg" alt="AI Nutrition Facts for HARPY, a budgeted oversight simulation. Privacy ladder not applicable (synthetic data); runs offline; model type: simulation with a mock detector; base model: none, with optional gpt-4.1 replay. Trust ingredients: no training on customer data, no data shared with a vendor, human in the loop with capped alerts. Compliance: logging and auditing, guardrails, input/output consistency, budget enforcement, preregistered decisions and blame tracing are all yes, each backed by named requirements. Detector accuracy is assumed, not measured; only the blame rule has been checked on real traces, on 184 labeled failure logs. 37 requirements: 35 verified by tests or CI, 2 by inspection." width="460"></p>

Owner: Melinda Emerson · Covers: v2 (main) and the v3 draft · Reviewed: October 15, 2026

An AI nutrition label states what an AI system is, what it does with data, and which requirements it meets, in a fixed format that can be compared across systems. HARPY's label uses the standard fields:
- description and model,
- trust ingredients,
- compliance.

It adds the source-attribute categories that [ONC's HTI-1 rule](https://casrai.org/guides/hti-1-predictive-dsi-source-attributes) requires for predictive decision support:
- intended use and out-of-scope use,
- development details,
- validation,
- maintenance.

Every compliance row names the requirements in [REQUIREMENTS.md](../REQUIREMENTS.md) that back it, and each of those names the test that checks it. The label is the requirements at a glance; the requirements document is the detail.

## Model

| Field | HARPY |
| --- | --- |
| Description | Simulates budgeted oversight of multi-agent AI. It audits a sample of agents, traces each bad claim back to the agent that started it (v3 draft), and charges every cost against worker spend. |
| Privacy ladder level | Not applicable. The data is synthetic and contains no personal data. |
| Feature is optional | Yes. The simulation runs offline. Replay calls a model only if you supply your own key. |
| Model type | Simulation. `MockSentinel` flags audits with configured probabilities; with blame tracing on, it also names one evidence claim per flag (`sentinel.evidence_accuracy`, an assumption). |
| Base model | None in the simulation. Replay can call OpenAI `gpt-4.1-2025-04-14`, opt-in. Claude Haiku 4.5 and 5.5 and the gpt-6 tiers are priced, never called. |

## Trust ingredients

| Ingredient | HARPY | Requirement |
| --- | --- | --- |
| Base model trained with customer data | No. HARPY trains nothing and ships no weights, embeddings or retrieval corpus. | — |
| Customer data shared with model vendor | No. The simulation sends nothing. Replay sends observables to OpenAI only when you run it with a key, with `store: false`. | N-4 |
| Training data anonymized | Not applicable. There is no training. | — |
| Data deletion | Every output is a local file you control. | — |
| Human in the loop | Yes. Isolations raise human alerts, capped per active agent-hour independently of the dollar budget. | B-3 |
| Data retention | Local files only; nothing is retained elsewhere. | — |

## Compliance

| Control | HARPY | Requirements |
| --- | --- | --- |
| Logging and auditing | Yes. Full input snapshots, source and lockfile hashes, and a SHA-256 manifest on every evidence packet. | E-4, N-1 |
| Guardrails | Yes. HARPY's side cannot read ground truth; one audit can never isolate an agent; the controller has five scoped actions. | O-2, R-1, R-2 |
| Input/output consistency | Yes. Same inputs and seed give byte-identical output; published v2 results re-run exactly in CI. | N-1, E-5 |
| Budget enforcement | Yes. Audits are bought from a fixed share of worker spend, follow-ups buy nothing extra, alerts are capped, and unverified pricing is refused. | B-1 to B-5 |
| Preregistered decisions | Yes. A study writes its spec before any outcome, a spec that isn't frozen won't run, and a pre-freeze check rejects unpassable designs. | E-1 to E-3 |
| Blame traced to origin | Yes, on the v3 branch. Tattletale's rule, on observables only, over lossy lineage. | A-1 to A-5 |
| Replay safety | Yes. Labels stay separate, provider errors are never verdicts, and paid calls need verified prices and a cost reservation. | P-1 to P-3 |

## Source attributes (HTI-1 categories)

| Category | HARPY |
| --- | --- |
| Details and output | Output type: a detection, isolation and cost record per simulated run, plus study verdicts (PASS, FAIL, INCONCLUSIVE, INCOMPLETE). Developer: Melinda Emerson, MeLinResearch. |
| Purpose | Research: which oversight budgets and detector qualities would justify a trial on real agent traces. Users: researchers and engineers who run multi-agent systems. HARPY informs a decision; it doesn't make one. |
| Cautioned out-of-scope use | Production enforcement, adversaries adapting to the sampler, high-stakes decisions, and any claim of live-model accuracy or real-world savings. |
| Development details and input features | A seeded synthetic mesh: 30 agents, 500 one-minute ticks, typed claims, one planted fault per run. Oversight sees only observables: typed message fields, Tier 0 scores, audit results, recorded lineage. |
| Fairness process | Not applicable. No people or demographic data are modeled. The fairness that matters here is between arms, so each arm is compared on the same world with matched seeds. |
| External validation | Partial. The blame rule alone was checked on 184 hand-labeled real failure logs ([Who&When](../../experiments/who_and_when/README.md)). It beat blaming the final speaker on wrong-value failures and missed failures that start as actions. The full simulation has not been run on real traces. |
| Quantitative performance | Simulation only. Detector accuracy is an input: overt 95%, moderate 78%, subtle 42%, false positives 2%. The v2 study was INCONCLUSIVE. The v3 pilot is a design input, not study evidence. |
| Ongoing maintenance | CI on every push across Linux, macOS and Windows: lint, the full test suite, evidence verification, and a v2 reproduction check. |
| Update schedule | Each study version (v1, v2, v3) is frozen before data collection and published with its evidence, whatever the result. |

## Other details

| Field | HARPY |
| --- | --- |
| Recipe | Generate a seeded world, apply seven mechanical signals, buy affordable audits, optionally trace each flagged claim to its origin, accumulate suspicion, apply scoped responses, and score against ground truth. Replay predicts from observables first and joins labels afterward. |
| Cost ingredients | Sentinel, Tier 0, discarded work, reruns, triage and investigation. Token list prices are sourced (OpenAI gpt-4.1 and gpt-6 tiers, Anthropic Claude Haiku); staffing and engineering costs are assumptions. |
| Known limits | This is a conditional simulation with a small synthetic replay fixture, and live-model accuracy is unmeasured. Blame tracing assumes the detector names the bad claim and that the framework records which claim each claim came from. Real failures also include wrong actions and runs with no answer, which HARPY's faults don't model. Hashes are not signatures, and Python guards are not security sandboxes. |
| License and rights | Code and the authored synthetic fixture are MIT. Provider models, APIs and external sources keep their own terms. |

[Engineering requirements](../REQUIREMENTS.md) · [Data / model card](DATA_MODEL_CARD.md) · [DBOM](DBOM.md) · [V3 design notes](../STUDY_V3_DESIGN.md)

This is a project label, not a compliance certification.
