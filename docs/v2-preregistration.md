# HARPY v2: preregistration

**Status: proposed design. Nothing here has been implemented or run.**

v1 failed its economic test for two identifiable reasons (`docs/v1-result.md`).
v2 changes exactly those two things and holds everything else fixed, so that any
difference in outcome is attributable rather than merely observed.

This document is written before implementation on purpose. It states the design,
the hypotheses, and the conditions under which v2 should be declared a failure —
so that those conditions cannot be renegotiated once numbers exist.

---

## What is NOT changing

Frozen, so v1 and v2 are directly comparable:

- **The kill condition.** Incremental oversight cost ≤ **10%** of worker-fleet
  cost, with strictly positive detection uplift over TIER0_ONLY. Both halves.
- **The world.** Mesh size, tick count, fault model, fault distribution, onset
  sampling, severity definitions, claim and lineage structure.
- **Pricing.** `configs/pricing.yaml` unchanged, same three worker tiers, same
  fixed sentinel.
- **The sentinel.** Still `MockSentinel`; detection probability remains an input,
  not a measurement. v2 is still phase 1.
- **Scoring.** Detection rate, uplift over TIER0_ONLY, overhead, coverage,
  false-isolation counts, and the ground-truth isolation guard.
- **v1 itself.** v2 lands as an additive, selectable arm/controller. v1 remains
  the default so `docs/v1-result.md` stays reproducible from the same tree.

Changing the ceiling, the fault distribution, the thresholds or the prices to
make v2 look better is out of bounds. If v2 needs a different ceiling to pass,
that is a finding about v2, not a licence to move the line.

---

## Change 1 — One global oversight budget

### The v1 defect

`budget_pct` capped **sentinel audit spend only**. Discarded work, rerun work,
triage and investigation were unbounded downstream consequences. The kill
condition measured a total that no component of the system was accountable for,
so the architecture could — and did — breach it while every internal budget was
satisfied.

### The v2 design

A single `OversightBudget` governs **total incremental cost**: sentinel + Tier 0
+ discard + rerun + triage + investigation. Two properties do the work:

**1. Actions are priced before they are taken, not after.**
Every response is quoted against the budget and refused if unaffordable. The
quote is exact where it can be and an expectation where it cannot:

| action | ex-ante price |
|---|---|
| re-audit | one sentinel call at configured token counts |
| halt outbound | 0 (no work destroyed, no human paged) |
| quarantine + review | `triage + escalation_rate × investigation` |
| isolate | agent's spend since restart × (1 + rerun_multiplier), **plus** the review it triggers |

Discard cost is *knowable at decision time* — it is the agent's accumulated
spend, which the accountant already tracks. This is the pivot the whole design
turns on: in v1 the most expensive action was taken blind and its cost
discovered afterwards. In v2 an isolation that would breach the budget cannot be
issued.

**2. The budget accrues once and is spent by everyone.**
One pool, accruing at `oversight_budget_pct` of worker spend per tick, drawn
down by every component. When the pool cannot cover an action, the controller
takes the most expensive rung it *can* afford — which is frequently
`halt_outbound` at zero cost, and that is the point.

### Consequence to expect

Overhead becomes **bounded by construction rather than by luck**. The
interesting question stops being "did it breach 10%" and becomes "what detection
can be bought inside 10%". A design that cannot breach the ceiling can still
fail — by detecting nothing.

---

## Change 2 — A graduated response ladder

### The v1 defect

One threshold crossing jumped straight to `isolate`: destroy the agent's
work-in-progress, pay to redo it, page a human. That action was **wrong ~80% of
the time**, and being wrong cost 3.5–4.6× more than being right. v1 had
`halt_outbound` in its permitted action set and never used it.

### The v2 design

Four rungs, cheapest first, each with its own evidence threshold. Suspicion no
longer selects an action; it selects a *rung*, and the budget decides whether
that rung is affordable.

| rung | action | cost | reversible | evidence needed |
|---|---|---|---|---|
| 1 | **halt outbound** | ~0 | yes | low |
| 2 | **targeted re-audit** | 1 sentinel call | yes | low–medium |
| 3 | **quarantine + human review** | analyst time; work preserved | yes | medium |
| 4 | **isolate** (destructive) | discard + rerun + review | **no** | high |

The properties that matter:

- **Rungs 1–3 are reversible.** An agent halted or quarantined on evidence that
  does not hold up is released, having lost throughput but not work. v1 had no
  cheap way to be wrong; v2's first three rungs are all cheap ways to be wrong.
- **Rung 1 stops the bleeding immediately at zero cost.** Contamination spreads
  through emitted claims. Halting outbound arrests spread *while evidence
  accumulates*, decoupling containment from adjudication — in v1 those were the
  same act, which is why adjudicating early was so expensive.
- **Rung 2 attacks the coverage problem directly.** v1's audits were allocated
  by a sampler that, for SUBTLE faults, performed at chance (3.14% vs 3.33%).
  A targeted re-audit spends the next audit on the agent already under suspicion
  instead of redrawing from the pool. This is the one mechanism aimed at v1's
  targeting finding, and it is deliberately a *response*, not a sampler change —
  the sampler stays as it is so the two changes remain separable.
- **Rung 4 requires strictly stronger evidence than v1's single threshold**, and
  is refused outright when the budget cannot absorb its full quoted price.

### Ground-truth discipline is unchanged

The ladder must run on observables. `was_faulty` remains ground truth, readable
only on the world's side of the seam. The existing guard in `harpy.types` covers
`response.py` and must cover the new controller; escalation and de-escalation
decisions may use suspicion, audit results and Tier 0 scores only.

---

## Hypotheses

Stated before implementation, in the order they should be tested.

- **H1 (containment).** v2 keeps total incremental overhead ≤10% in
  substantially more cells than v1's 41% under-ceiling rate — approaching all
  cells, since the budget is now enforced ex ante. *Falsified if v2 breaches the
  ceiling at a comparable rate; that would mean the pricing of actions is wrong.*
- **H2 (cheaper mistakes).** Discard/rerun dollars attributable to false
  positives fall by at least an order of magnitude, because most wrong decisions
  now terminate at rungs 1–3. *Falsified if false-positive cost remains a
  material share of overhead.*
- **H3 (viability).** At least one operating point shows positive uplift over
  TIER0_ONLY at ≤10% overhead, **robust across seeds** — viable in ≥4 of 5 pilot
  seeds, not 1 of 5 as in v1. *This is the headline test.*
- **H4 (no detection collapse).** v2's detection rate is not materially worse
  than v1's at matched cells. The obvious failure mode of a graduated ladder is
  hesitating a fault to death — containment achieved by never acting.
  *Falsified if detection falls while overhead falls.*
- **H5 (SUBTLE targeting).** Targeted re-audit raises coverage of the faulty
  agent above the 1/30 chance baseline for SUBTLE faults. *This is the weakest
  hypothesis and the one most likely to fail: re-audit can only help once
  suspicion has landed on the right agent at all, and v1 showed it usually does
  not.*

## Kill condition for v2

**v2 is declared a failure if H3 fails** — no operating point shows positive
uplift under 10% overhead, robust across seeds — **or if H4 fails**, i.e.
viability was bought by not detecting anything.

A v2 that stays under budget by declining to act is not a success. It is v1's
NONE arm with extra machinery.

## New metrics required

Beyond v1's, per run:

- `rung_distribution` — count of decisions terminating at each rung
- `reversals` — agents halted or quarantined and subsequently released
- `ticks_under_halt` — throughput cost of the cheap rungs, which is real and
  otherwise invisible
- `actions_refused_for_budget` — how often the global budget bound an action.
  This is the direct evidence that change 1 is doing something.
- `false_positive_cost_by_rung` — the cost of being wrong, per rung; H2's
  measurement
- `escalation_latency` — ticks from first suspicion to rung 4, the price paid
  for graduated caution

## Staging

The workflow that caught v1's real problems, unchanged: **1-seed smoke → 5-seed
pilot → inspect → decide whether 20/50 seeds are justified.** No large run
before the pilot has been read.

An honest expectation to record now: **v2 may also fail.** H5 in particular is
doubtful, and if SUBTLE targeting is genuinely at chance, no response ladder
fixes it — that would point at Tier 0's telemetry being the wrong observable,
which is a v3 question. Recording that in advance is the point of writing this
before the numbers exist.

---

## Open questions for review — flagged, not decided

1. **Rung 3 vs rung 4 thresholds.** Both need values. They should be
   preregistered rather than tuned, but nothing in v1 tells us where to put
   them. Proposal: derive from v1's suspicion distribution at matched cells and
   fix them before the first run.
2. **Does `oversight_budget_pct` keep v1's sweep values {0.05, 0.10, 0.20}?**
   They now mean something different — total, not sentinel-only — so the same
   numbers are not the same experiment. Sweeping the same values keeps the axis
   legible; sweeping different ones may be more informative.
3. **Should halt-outbound have a nonzero cost?** It is free in dollars but costs
   throughput, and a zero price makes rung 1 unconditionally affordable. That
   may be correct, or it may be the loophole that lets v2 pass by halting
   everything.
4. **Tier 0's OVERT/MODERATE cliff** (1.0 / 0.0) should be understood before
   Tier 0 is carried into v2 unchanged.
