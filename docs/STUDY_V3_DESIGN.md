# V3 design: re-audit, exposure sampling, Tattletale blame tracing, and a check before freezing

[Project](../README.md) · [V2 study](STUDY_V2.md) · [V1 result](V1_RESULT.md) · [Pilot packet](../evidence/v3-feasibility/)

**Status: design draft. Nothing is frozen and no study has run.** The pilot below is a design input on seeds 100000 and up, never study evidence.

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

Every mechanism is off by default. With them off, the current tree reproduces 2,400 published v2 per-seed records metric-for-metric (seeds 0–9, every arm, severity, budget, fidelity, review policy and scale), excluding `experiment_fingerprint`, which hashes the source.

- **Targeted re-audit** (`followup_audits = k`).
  - A flagged audit queues `k` follow-up audits on the agent it is credited to.
  - Follow-ups are served before the arm draws, from the same pool, one per agent per tick.
  - They buy no extra audits; they redirect spending.
- **EXPOSURE arm.** Audits go to the agents with the most unchecked exposure: `ticks since last audit × (1 + observable fan-out of claims emitted since then)`.
  - This is the BRA-Audit placement rule (arXiv 2608.14668), on observables only.
- **Blame tracing** (`attribution`), [`src/harpy/attribution.py`](../src/harpy/attribution.py).
  - This is [Tattletale](https://github.com/MeLinResearch/tattletale)'s origin rule, ported from quoted text to typed claim values.
  - A flagged audit names one claim as its evidence. The walk follows that claim's lineage back:
    - An agent that carried it **unchanged** passes the blame upstream.
    - An agent whose claim **differs** from what it received owns it.
    - A missing parent stops the walk and blames the last known agent.
    - A cycle stops the walk at the claim-count cap.
  - The flag's suspicion is credited to the agent the walk lands on, not to the agent that was audited.
  - The walk is deterministic and reads observables only. The ground-truth guard covers it.
- **Provenance log** ([`ProvenanceLog`](../src/harpy/lineage.py)). Lineage as a store would record it, one edge at a time when each claim is emitted:
  - Each true edge is recorded with probability `lineage_fidelity`.
  - Spurious edges appear at the configured rate.
  - This is the same loss model as v2's projection, drawn incrementally from its own RNG stream.
- **Relay accounting.** False isolations are split into relays and pure false positives. A relay is a healthy agent whose audit window held corrupt claims it had derived from the fault source.
- **`harpy feasibility`.** A pre-freeze check for one candidate primary cell:
  1. Arithmetic, no simulation. Returns `CRITERIA_CONFLICT` when one detection's triage alone reaches the cost ceiling.
  2. A pilot on seeds ≥ 100000 runs the candidate and its matched TIER0_ONLY.
  3. The frozen decision rule is replayed on outcomes drawn from the pilot, carrying pilot uncertainty through Jeffreys draws. That gives the probability of PASS at each seed count.
- **Study guard.** A specification with `status` other than `frozen` cannot run. [`configs/study.v3.draft.yaml`](../configs/study.v3.draft.yaml) is the unfrozen draft.

### Two new assumptions

Both are stated in [`configs/v3.yaml`](../configs/v3.yaml) and stress-tested below.

1. **The sentinel names the bad claim.** `sentinel.evidence_accuracy` is the probability that a true detection names a corrupt claim; otherwise it names a random claim from the window. The default is 1.0. The replay detector's output already has an evidence-ID field, so replay can measure this for a real model.
2. **Lineage is recorded as claims are emitted.** This is realistic for a provenance store, but an agent framework has to actually record parent claims.

One inherited assumption now matters more than it did. The mock flags any audit window containing a corrupt claim with the same probability (0.42 for SUBTLE), whether the agent originated the claim or relayed it. With tracing, every relay that gets flagged becomes a witness against the source. Whether a real detector can spot a 1–3% perturbation in a relay's restatement is a replay question.

## What the pilot found

The pilot covered 6,200 simulations on 40 pilot seeds per cell: SUBTLE faults, 5% budget, fleet scale 100, fixed review unless stated. The rule replayed is v2's: at least 10 points over matched TIER0_ONLY and below 10% overhead. A cell is freezable only at 80% assurance. TIER0_ONLY caught 0 of 40 in every cell, so detection here is the incremental detection.

### Without tracing: no cell is freezable

| Workers | Sentinel | Audits/tick | Arm, k | Detected | False isolations/run | Overhead |
| --- | --- | --- | --- | --- | --- | --- |
| gpt-4.1-mini | gpt-4.1 | 0.08 | HYBRID, 0 | 0/40 | 0.00 | 4.7% |
| gpt-6.1-sol | Claude Haiku 4.5 | 0.87 | HYBRID, 0 | 7/40 | 1.57 | 13.8% |
| gpt-6.1-sol | Claude Haiku 4.5 | 0.87 | HYBRID, 4 | 24/40 | 3.42 | 20.7% |
| gpt-6.1-sol | Claude Haiku 5.5 | 8.6 | EXPOSURE, 4 | 40/40 | 2.80 | 16.4% |

- Relays were 95–100% of false isolations. This is the problem [v1's record](V1_RESULT.md) saw as "about 80% of isolations hit agents that were not the fault source."
- Re-audit raised detection, but every detecting cell breached the ceiling.

### With tracing: 46 of 66 fixed-review cells are freezable

Results at gpt-6.1-sol workers with a Claude Haiku 4.5 sentinel, every arm and follow-up depth:

| Arm, k | Detected | False isolations/run | Overhead | Blame traced to the fault source | Seeds needed |
| --- | --- | --- | --- | --- | --- |
| EXPOSURE, 0 | 40/40 | 0.00 | 8.1% | 96% | 30 |
| EXPOSURE, 4 | 40/40 | 0.03 | 8.1% | 97% | 30 |
| HYBRID, 0 | 40/40 | 0.07 | 8.4% | 94% | 30 |
| HYBRID, 4 | 40/40 | 0.10 | 8.4% | 94% | 30 |
| RANDOM, 0 | 40/40 | 0.12 | 8.8% | 93% | 50 |
| RANDOM, 4 | 40/40 | 0.12 | 8.5% | 92% | 30 |

"Blame traced to the fault source" is the share of walks starting from a truly corrupt claim that landed on the faulty agent.

- **Tracing turns relays into witnesses.** Detection rises because every relay carrying the bad claim now counts against the source.
- **Contamination shrinks.** Median corrupt claims per run fell from about 860–940 to about 120–135, and the fault was isolated a median of 36–38 ticks after onset. These figures are at k = 0.
- **Tracing alone does the work** at 0.46 audits per tick and above. Follow-ups add little there.
- **Follow-ups matter when audits are scarce.** At v2's own prices (gpt-4.1-mini workers, gpt-4.1 sentinel, 0.08 audits per tick), tracing gives 13–16 of 40 at k = 0 and 24–26 of 40 at k = 4, at 6.5–8.5% overhead. That clears the 10-point floor, with 30–100 seeds needed.
- **Cheap workers still fail.**
  - With gpt-6-luna workers and the gpt-4.1 or Haiku 4.5 sentinel, the budget buys too few audits: 0–7 of 40.
  - With gpt-6-luna workers and the Haiku 5.5 sentinel, detection is complete but overhead is about 12%. A human review is large next to such cheap worker spend.
- **Linear review never passes, at any price tested.** The best case is 11.6% overhead, with gpt-6-astra workers. One review per detection is the floor.

### Stress tests

Both stress tests ran at gpt-6.1-sol workers with a Haiku 4.5 sentinel.

| Stress | Effect on blame accuracy | Freezable cells | What breaks |
| --- | --- | --- | --- |
| Sentinel names the right claim only 50% of the time | Stays 93–96% on corrupt evidence | 6 of 6 (8.3–9.4% overhead, 30–100 seeds) | Nothing. Wrong evidence spreads blame thinly, and isolation needs repeated hits on one agent. |
| Lineage fidelity 0.5 | Falls to 29–39% | 2 of 6: EXPOSURE and HYBRID at k = 0 (8.6% and 9.0%) | Detection stays 40/40. Dropped edges make relays look like origins, so follow-ups chase them: every k = 4 cell reaches 12–13% overhead, at 1.1–1.25 false isolations per run. |

**Provenance quality is now the binding constraint.** With poor lineage, skip follow-ups and let tracing accumulate evidence from many audits.

## The candidate primary cell (not frozen)

This is the cell the pilot points to:

- EXPOSURE arm, tracing on, no follow-ups.
- gpt-6.1-sol workers, Claude Haiku 4.5 sentinel. This sentinel is cross-family, as in the August decision.
- Fixed review, lineage fidelity 1.0, SUBTLE faults, 5% budget, fleet scale 100.

It is freezable at 30 seeds, and at lineage fidelity 0.5 it stays freezable at 30 seeds. A cell chosen because it looked best on a pilot is at risk of a winner's curse. Fresh study seeds address that, as long as the cell, the seed count and the criteria are frozen before any study outcome exists.

## Decisions for the owner

1. **Review policy for the primary cell.** Each policy implies a fault rate:
   - Fixed review implies one fault per ~25,000 agent-hours.
   - Linear review implies one per ~250 agent-hours, and no cell passes under it.

   Choosing fixed should be stated as a change from v2, with that reason, and linear reported alongside.
2. **Primary cell.** Freeze the candidate above, or choose another freezable cell from `evidence/v3-feasibility/grid.csv`.
3. **The August branch.** Its v1 record is now on this branch as [V1_RESULT.md](V1_RESULT.md). The branch itself can be deleted.

## What this cannot show

- The sentinel is still `MockSentinel`. Its detection rate, its ability to flag relays, and its evidence naming are all assumptions; only replay on real traces can measure them.
- A cheaper sentinel buys more audits here only because detection is held fixed across sentinels.
- Discard cost is the isolated agent's spend since its last restart, times (1 + rerun multiplier). This is v2's pessimistic accounting, unchanged.
- Blame tracing needs a framework that records which claim each claim was derived from. Without that, nothing here applies.
