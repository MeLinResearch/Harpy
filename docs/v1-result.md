# HARPY v1: result

**Verdict: v1 fails its own preregistered economic test. The architecture is not viable at any swept operating point.**

This document is the record of that result. It is not a work item. The v1 code,
its kill condition, its preregistered configuration and its pricing are frozen
as they were when these numbers were produced, so the result stays reproducible
and so v2 has something to be measured against.

---

## The kill condition

Incremental oversight cost — sentinel + Tier 0 + discarded work + rerun work +
triage + investigation — must stay at or under **10% of worker-fleet cost**,
while showing **strictly positive detection uplift over the TIER0_ONLY
baseline**. Both halves, or the arm has not earned its place.

## What was run

| sweep | simulations | rows | wall clock |
|---|---|---|---|
| A — detector quality | 1,500 | 1,500 | 461 s |
| B — false-positive rate | 3,375 | 6,750 | 958 s |

Five seeds (0–4). Three worker tiers (`gpt-5.6-luna`, `gpt-5.6-terra`,
`gpt-5.6-sol`) against a fixed `claude-haiku-4-5` sentinel, at verified pricing.
Seed-collapsed summaries are preserved at `results/v1/`.

Five seeds rather than the preregistered fifty. The decision to stop there was
deliberate: the null is not marginal, no cell came close in any seed, and the
disqualifying costs are structural rather than stochastic. Fifty seeds would
have measured a nonviable design more precisely.

## The result

**No viable operating point exists.**

| filter | rows |
|---|---|
| non-baseline | 5,400 |
| under the 10% ceiling | 2,227 |
| positive uplift over TIER0_ONLY | 1,162 |
| **both** | **27 (0.5%)** |

And all 27 collapse under replication:

| viable in… | cells |
|---|---|
| exactly 1 of 5 seeds | 27 |
| ≥3 of 5 seeds | **0** |
| 5 of 5 seeds | **0** |

Averaged across all five seeds, those same cells run at **18–41% overhead** —
two to four times the ceiling. Each was one lucky seed, not a viable
configuration.

`max_false_positive_rate_for_viability` is **null for MODERATE, OVERT and
SUBTLE**.

## Why it fails

### 1. The sentinel alone consumes almost the entire allowance

Mean cost as a percentage of worker spend:

| component | sol | terra | luna |
|---|---|---|---|
| **sentinel** | **8.71** | **8.71** | **8.27** |
| discarded work | 4.72 | 4.31 | 0.76 |
| rerun work | 4.72 | 4.31 | 0.76 |
| Tier 0 | 0.00 | 0.00 | 0.01 |
| triage @ scale 1 | 3.75 | 6.28 | 12.71 |
| investigation @ scale 1 | 2.04 | 2.57 | 0.00 |

The sentinel takes **8.7 of the 10 available points** before a single isolation,
rerun or human review is paid for. Discard and rerun together add another 9.4.
The design is over budget before false positives are considered at all.

The mechanism is a design error, not a parameter choice: **`budget_pct` bounds
sentinel spend only.** The other five components are downstream consequences
that no budget governs. Setting `budget_pct = 0.20` therefore *guarantees*
breaching a 10% total ceiling — and indeed every one of the 27 momentarily
viable rows sits at `budget_pct = 0.05`, with **zero at 0.10 or 0.20**.

### 2. Isolation is destructive and usually wrong

| tier | false isolations/run | mean false-isolation $ | mean true-isolation $ |
|---|---|---|---|
| sol | 2.20–2.53 | $1,011–1,064 | $275–287 |
| terra | 1.86–2.43 | $461–621 | $135–174 |
| luna | 0.14–0.20 | $3.6–5.7 | $7.3–7.9 |

Roughly **80% of isolations are of agents that were not the fault source**, and
each one discards that agent's entire work-in-progress and pays to redo it.
False isolations cost **3.5–4.6× more than true ones** at the two tiers that
detect anything.

False-isolation cost alone exceeds the whole 10% ceiling in **1,078 of 5,400
rows (20%)** — and in **35–46% of audit-buying runs** at sol and terra. The
median run is fine on this measure (~2%); the distribution has a long right tail
rather than a high centre. That tail is enough to disqualify the design.

### 3. Directed sampling buys no targeting advantage for SUBTLE faults

Sweep A, coverage of the faulty agent against a uniform-random baseline of
1/30 = 3.33%:

| arm | mean | median | ×chance |
|---|---|---|---|
| DIRECTED | 3.14% | 3.47% | 0.94 |
| HYBRID | 4.06% | 3.25% | 1.22 |
| RANDOM | 4.78% | 3.12% | 1.43 |

RANDOM leads on the mean, DIRECTED leads on the median, and RANDOM beats
DIRECTED in only 2 of 5 seeds. There is no effect: **Tier 0 telemetry gives no
usable targeting signal for SUBTLE faults.** Directed sampling is neither better
nor worse than choosing at random.

An earlier 1-seed run appeared to show DIRECTED performing *worse* than random.
That did not replicate, and it was partly an artifact of a since-fixed bug in
the coverage denominator (see `audit_counts_toward_exposure`). The claim is
withdrawn; it should not be cited.

### 4. The budget tier cannot be rescued by spending more

`gpt-5.6-luna` is affordable (61% of its rows are under the ceiling) but shows
positive uplift in only **2.8%** of them, and is viable in none.

| budget | audits | detections | overhead |
|---|---|---|---|
| 0.05 | 45 | 50 | 11.2% |
| 0.10 | 84 | 50 | 16.7% |
| 0.20 | 194 | 50–70 | 36.4% |

Quadrupling the budget quadruples audits, moves detection from 50 to 70, and
triples overhead. TIER0_ONLY at luna also detects 50 — so almost all of luna's
detections are OVERT faults that Tier 0 catches for free. Overhead rises faster
than uplift. This is structural, not variance.

## Caveats that constrain how this result may be cited

- **Five seeds, not fifty.** Cost figures were stable across seeds (overhead
  0.17–0.28, false-isolation cost 4.8–8.9% at sol); *detection* was not (SUBTLE
  0.124–0.289). Any claim about differences **between arms in detection** is
  underpowered. The cost-side conclusions and the null are not.
- **OVERT uplift is definitionally zero.** TIER0_ONLY detects OVERT at 1.0, so
  no arm can exceed it. The 0% OVERT uplift rate is an artifact of the baseline,
  not evidence against the architecture.
- **TIER0_ONLY detects OVERT at 1.0 and MODERATE at 0.0.** A perfect-to-nothing
  cliff between adjacent severities is sharper than the fault model obviously
  justifies and is worth examining before Tier 0 is reused unchanged in v2.
- **Detection is a mock.** `MockSentinel` *is* its configured detection
  probability. No number here is evidence that any real model detects anything;
  that is phase 2 (HARPY-REPLAY).
- **Pricing provenance.** Figures were transcribed from provider pricing pages
  supplied as screenshots on 2026-08-23; the agent could not reach those pages
  directly. Short-context Standard columns, no prompt caching modelled. See the
  header of `configs/pricing.yaml`.

## What this licenses

That **this** architecture — a percentage-of-worker-spend audit budget feeding a
suspicion score that triggers destructive isolation — does not pay for itself at
these prices, for these fault severities, at any swept operating point.

It does **not** license a claim that budgeted oversight of multi-agent meshes is
economically hopeless in general. Two specific design decisions produced the
failure, and both are replaceable. That is what v2 is for; see
`docs/v2-preregistration.md`.
