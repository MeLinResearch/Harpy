# Methodology

[Project](../README.md) · [Architecture](ARCHITECTURE.md) · [Run and inspect](RUNNING.md)

HARPY-SIM asks which detector characteristics and costs would make selective agent oversight worth testing. HARPY-REPLAY separately measures a detector on recorded observable cases. The two workflows do not exchange accuracy assumptions automatically.

## Simulation design

A seeded mesh of 30 agents exchanges typed claims for 500 one-minute ticks. One agent develops a planted malfunction. Fault severities are OVERT structural failure, MODERATE typed conflict, and SUBTLE numeric perturbation. True lineage propagates corrupted claims; oversight receives a lossy projected graph.

Seven mechanical Tier 0 checks feed decaying suspicion. A dollar allowance tied to worker spend buys selective mock audits under RANDOM, DIRECTED, or HYBRID sampling. NONE and TIER0_ONLY are controls. Isolation needs accumulated evidence; an independent human-attention budget caps alerts and can adapt the gate. The controller exposes five scoped actions; the default loop uses a subset.

`MockSentinel` reads corruption labels and returns a weighted coin flip. Its default audit probabilities (95%, 78%, 42% by severity; 2% false-positive probability) are assumptions. Detection rates downstream of that mock are conditional simulation outcomes, never measured AI accuracy.

Independent RNG streams keep matched worlds stable across arms. `configs/default.yaml`, including its fault distribution, is preserved from [the v1 source revision](https://github.com/MeLinResearch/Harpy/tree/babf4b12d71830840787e0784326a68a5bd7cc87). Changing the workload, fault distribution, or thresholds requires a new versioned experiment.

## Six-component cost model

Costs are sentinel audits, Tier 0 checks, discarded work, reruns, triage, and investigation, divided by worker-fleet spend. Discard and rerun accounting separately attributes true and false isolations. Token usage is simulated in HARPY-SIM and measured by the provider adapter in replay.

Human review uses minutes and an analyst hourly rate: triage plus probabilistically escalated investigation, with a separate RNG stream and per-agent batching window. The default $75/hour, 3-minute triage, 45-minute investigation, 25% escalation, and 20-tick batching are modeling assumptions. They are not observed analyst behavior or a imported wage dataset.

Let W be slice worker cost, A the sum of four per-agent oversight costs, R review cost, and s fleet scale. V2 makes the review policy explicit:

| Policy | Fleet overhead fraction | Interpretation |
| --- | --- | --- |
| fixed | (sA + R) / (sW) | Legacy assumption: one fixed review pool, with no demonstrated staffing capacity. Review amortises as the fleet grows. |
| linear | (sA + sR) / (sW) | Review demand grows with agents; the overhead fraction is independent of fleet scale. |

Scale and policy re-cost the same simulation. They change no RNG stream, threshold, alert count, or detection decision. Recosted records are sensitivity scenarios, not independent observations. Neither policy models queueing, staffing economies, capacity limits, or correlated fleet failures.

`min_fleet_dollars_per_hour_for_viability` solves the fixed-policy 10% cost boundary. Under linear scaling, it is zero when every positive scale clears the cost ceiling, and null when no scale can clear it. It does not forecast minimum operational staffing.

## Frozen v2 decision

[configs/study.v2.yaml](../configs/study.v2.yaml) freezes the study before collection: 30 seeds, five arms, three severities, two budgets, two lineage fidelities, two fleet scales, and fixed/linear review. There are 1,800 distinct simulations and 7,200 recosted records. The primary cell is HYBRID/SUBTLE, 5% audit budget, lineage fidelity 1, 50% random reserve, fleet scale 100, linear review, 0.05 alerts per active agent-hour, and the gpt-4.1-mini worker pricing tier.

The target is at least 0.10 added detection probability over matched TIER0_ONLY, with mean total incremental cost below 0.10 of worker-fleet cost. Matching includes source/input fingerprint, seed, severity, audit budget, lineage fidelity, fleet scale, attention cap, detector assumptions, worker tier, and review policy. HYBRID's reserve fraction is absent from the baseline key because Tier 0 does not use that knob.

| Verdict | Prespecified interpretation |
| --- | --- |
| PASS | At least 30 paired seeds; lower detection-gain bound at least 0.10 and upper mean-overhead bound strictly below 0.10. |
| FAIL | At least 30 paired seeds; upper detection-gain bound below 0.10 or lower overhead bound at least 0.10. |
| INCONCLUSIVE | Sufficient paired seeds but intervals do not establish a pass or fail. |
| INCOMPLETE | Too few seeds or missing matched baseline. Missing data never becomes zero-cost or zero-baseline uplift. |

Detection differences combine marginal 97.5% Wilson intervals using a Bonferroni construction, giving a conservative nominal 95% difference interval. This retains nonzero uncertainty at zero successes. Paired bootstrap detection effects and mean-cost intervals use 2,000 deterministic resamples. Wilson and bootstrap intervals are approximations; cost intervals may be weak for rare escalation or heavy-tailed workloads. Only the designated primary cell is confirmatory. Exploratory intervals have no simultaneous-coverage claim.

See [the study report](STUDY_V2.md) for the observed verdict. No threshold or primary cell is tuned after reading that result.

## Attention and scope

Dollar and attention limits bind independently. The attention denominator is active agent-hours, with suppressed alerts and lost detections scored on the world's side of the ground-truth guard. Fleet-scale recosting does not create a measured fleet attention model.

Faults are malfunctions, not adaptive adversaries. Simulations establish no demographic fairness, industrial safety, real task coverage, or production savings. Claim and lineage guards catch accidental leakage by inspecting call stacks; they are not security sandboxes. The replay loader rejects known answer fields but cannot identify every answer hidden in prose.

## Pricing and evidence integrity

`configs/pricing.yaml` remains an unverified placeholder, and `configs/pricing.smoke.yaml` remains synthetic. Normal runs reject unverified prices; a deliberate smoke override stamps results false and watermarks plots. `configs/pricing.study.yaml` records official uncached text-token rates, exact snapshots, source URLs, and an access date. The gate checks metadata; it does not continuously verify provider prices.

V2 JSON schema `harpy-sim/run/2` embeds the entire parsed configuration and pricing snapshot. SHA-256 identifies those inputs, executing source, `pyproject.toml`, and `uv.lock`. An experiment fingerprint combines those identities. The published build separately records actual Python and package versions; a lock hash alone does not prove the installed environment followed it. Results and manifests retain parent relationships; `harpy verify` checks artifact bytes, file completeness, input consistency, and parents against a trusted manifest. CI compares the published source identity with the checkout. Hashes are not signatures or external attestations. Old `run/1` records remain readable without retroactively claiming v2 provenance.

Plotting names its fleet scale and review policy and refuses to blend different experiment identities, worker tiers, or assumed detector settings. The main HYBRID curve averages reserve settings if several are present; a companion shows reserve-specific results. The v2 study uses one frozen reserve value.

## Replay evidence

[HARPY-REPLAY](REPLAY.md) keeps observations and labels separate, validates corpus hashes, and reports completed, failed, and unattempted cases. The optional OpenAI adapter uses strict structured decisions with existing observable evidence IDs. Refusals and incomplete responses are errors; unknown charges are reserved and replay stops without automatic paid retries.

The published reference is a 12-case synthetic development fixture. No live-model or real-trace accuracy is established. A separately frozen, permissioned, sanitized, held-out real-trace evaluation is required for that claim.
