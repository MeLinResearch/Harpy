# V3 design: re-audit, exposure sampling, and a check before freezing

[Project](../README.md) · [V2 study](STUDY_V2.md) · [Pilot packet](../evidence/v3-feasibility/)

**Status: design draft. Nothing is frozen and no study has run.** The four owner decisions below come before a primary cell is chosen.

## Why v2 could not pass

V2's primary test was INCONCLUSIVE at 0 of 30. Two problems made a pass impossible regardless of the detector, and both were knowable before freezing.

1. **The two success criteria contradicted each other.** The primary cell used linear review scaling.
   - One detection opens one human review. A 3-minute triage is about 20% of gpt-4.1-mini worker spend in the 30-agent, 500-tick slice.
   - So detecting the fault guarantees breaching the 10% ceiling.
   - In the published v2 data, 488 linear-review runs had a detection, and none came in under 10% overhead. The lowest was 22.5%.
2. **The budget could not buy enough audits.**
   - One gpt-4.1 audit costs about 18.5 gpt-4.1-mini worker messages, so 5% of worker spend affords about 0.08 audits per tick across 30 agents.
   - The isolation gate needs about three flagged audits on one agent within a few dozen ticks.

## What v3 adds

Both mechanisms are off by default. With them off, the current tree reproduces 2,400 published v2 per-seed records metric-for-metric (seeds 0–9, every arm, severity, budget, fidelity, review policy and scale), excluding `experiment_fingerprint`, which hashes the source.

- **Targeted re-audit** (`followup_audits = k`). A flagged audit queues `k` follow-up audits on that agent. They are served before the arm draws, from the same pool, one per agent per tick. A new flag refills the queue. Follow-ups buy no extra audits; they redirect spending.
- **EXPOSURE arm.** Audits go to the agents with the most unchecked exposure: `ticks since last audit × (1 + observable fan-out of claims emitted since then)`.
  - This is the BRA-Audit placement rule (arXiv 2608.14668).
  - One-hop fan-out stands in for full reachability.
  - The lineage graph is re-read every 10 ticks.
  - Observables only. The ground-truth guard covers it.
- **Relay accounting.** False isolations are now split into relays and pure false positives. A relay is a healthy agent whose audit window held corrupt claims it derived from the fault source.
- **`harpy feasibility`.** A pre-freeze check for one candidate primary cell:
  1. Arithmetic, no simulation. It computes the affordable audit rate and declares `CRITERIA_CONFLICT` when one detection's triage alone reaches the cost ceiling.
  2. A pilot on seeds ≥ 100000 runs the candidate and its matched TIER0_ONLY.
  3. The frozen decision rule is replayed on outcomes drawn from the pilot, carrying pilot uncertainty through Jeffreys draws. That gives the probability of PASS at each seed count.
- **Study guard.** A specification with `status` other than `frozen` cannot run. [`configs/study.v3.draft.yaml`](../configs/study.v3.draft.yaml) is the unfrozen draft. Its primary cell, pricing and worker tier are empty until the decisions below are made.

## What the pilot found

132 cells, 40 pilot seeds each, SUBTLE faults, 5% budget, fleet scale 100. **No cell is freezable under v2's criteria.**

Under linear review, 36 cells are criteria conflicts by arithmetic alone: every gpt-6-luna and gpt-4.1-mini cell. The table below uses fixed review.

| Workers | Sentinel | Audits/tick | Arm, k | Detected | False isolations/run | Overhead | Without false-isolation cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-4.1-mini | gpt-4.1 | 0.08 | HYBRID, 0 | 0/40 | 0.00 | 4.7% | 4.7% |
| gpt-4.1-mini | gpt-4.1 | 0.08 | EXPOSURE, 4 | 4/40 | 0.40 | 7.5% | 5.6% |
| gpt-6.1-sol | gpt-4.1 | 0.46 | HYBRID, 4 | 11/40 | 3.38 | 20.7% | 6.5% |
| gpt-6.1-sol | Claude Haiku 4.5 | 0.87 | HYBRID, 4 | 24/40 | 3.42 | 20.7% | 7.7% |
| gpt-6-astra | Claude Haiku 4.5 | 4.3 | EXPOSURE, 4 | 36/40 | 3.45 | 18.6% | 7.5% |
| gpt-6.1-sol | Claude Haiku 5.5 | 8.6 | EXPOSURE, 4 | 40/40 | 2.80 | 16.4% | 8.0% |

TIER0_ONLY detected 0 of 40 in every cell.

The pilot supports five points:

1. **The price ratio decides detection.** Audits per tick spans 0.02 to 8.7 across these prices. Below about 0.2, nothing detects SUBTLE faults.
2. **Re-audit converts hits into isolations.** At 0.46–0.87 audits per tick, follow-ups raise detection several-fold. Examples: 1 → 11 of 40, and 7 → 24 of 40.
3. **EXPOSURE is a coverage policy.** It is competitive with RANDOM and HYBRID, but not decisively better in this well-mixed mesh, where every agent's reach is statistically the same.
4. **Relays are now the binding constraint.** In spot checks of four cells, 95–100% of false isolations were relays. The sentinel flags contaminated content, not who originated it, so re-auditing a relay confirms it. Each relay isolation discards and reruns a healthy agent's work.
5. **Every detecting cell breaches the ceiling.** Without the false-isolation cost, the top cells sit at 7.5–8.5% overhead with 60–100% detection.

## Decisions for the owner

1. **Relays.** Choose one:
   - Build upstream tracing: follow a confirmed flag's observable lineage to the claim's origin before isolating.
   - Halt outbound instead of isolating on first confirmation, as in the August ladder.
   - Keep v2's scoring and report v3 as a negative result.
2. **Review policy for the primary cell.** Fixed, or linear. Linear is only arithmetically possible with flagship-priced workers.
3. **Operating point.** Which worker tier and sentinel price the primary cell assumes. HARPY-SIM changes only the price when the sentinel changes. Detection probability stays the configured assumption.
4. **The August branch.** Port `v1-result.md` onto main as the record that v1 failed, or close the branch.

## What this cannot show

- The sentinel is still `MockSentinel`. A cheaper sentinel buys more audits here because detection is held fixed. Whether Claude Haiku 5.5 detects as well as gpt-4.1 is a replay question.
- Discard cost is the isolated agent's spend since its last restart, times (1 + rerun multiplier). This is v2's pessimistic accounting, unchanged.
- The pilot estimates are design inputs. A study must freeze its primary cell, seed count and criteria before collecting outcomes on seeds 0 and up.
