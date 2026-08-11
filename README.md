# HARPY-SIM

Budgeted oversight for multi-agent meshes, simulated.

```bash
uv sync
uv run harpy run   --config configs/default.yaml --seed 0
uv run harpy sweep --config configs/default.yaml --out results/
uv run harpy plot  --results results/ --out results/detection_vs_overhead.png
```

## What this is

Phase 1 of two. HARPY-SIM answers: **under what detector characteristics and
costs is this architecture economically viable?**

A mesh of agents passes typed claims to each other. One of them goes faulty at a
random point, at one of three severities, and corruption spreads through claim
derivation. Watching them is a two-tier oversight layer: Tier 0 is seven
mechanical checks on typed fields, effectively free; the sentinel is a
model-backed auditor that costs real money and is therefore rationed. A sampler
decides who gets audited within a dollar budget, a decaying suspicion score
decides when to act, and five permitted responses are all the layer can do.

The output is a cost curve: detection probability against total incremental
overhead as a percentage of worker-fleet cost, one curve per sampling arm,
faceted by fault severity and by how good the lineage plumbing is.

## What this CANNOT show

**MockSentinel returns its configured detection probability.** It reads ground
truth and flips a weighted coin. It is the one component allowed to see the
answer key, precisely because it is not detecting anything — it is standing in
for a detector whose accuracy is an input to the model.

So: a phase 1 result of 80% detection is arithmetic. It is the number you get by
assuming a detector with those characteristics, sampling it under a budget, and
counting. It is **not** evidence that any model detects anything at that rate,
at that price, or at all.

Only phase 2 (**HARPY-REPLAY**: recorded traces, real candidate sentinels)
answers whether a detector with the required characteristics exists at an
acceptable price. Phase 1's job is to tell phase 2 what to go looking for — what
accuracy, at what audit price, would have to hold for any of this to pay for
itself.

Everything downstream of that assumption inherits it. The severity facets are
not empirical difficulty levels; they are three assumed detection probabilities
attached to three synthetic fault behaviours.

## Preregistered kill condition

HARPY fails if it cannot show **substantial incremental detection over the
TIER0_ONLY arm while total incremental cost stays under 10% of worker-fleet
cost**.

TIER0_ONLY is the arm to beat because Tier 0 is nearly free: if paying for a
sentinel does not buy detection that mechanical checks were not already getting,
there is no architecture here worth building.

No detection floor is preregistered until the fault distribution is fixed. The
distribution in `configs/default.yaml` is frozen before the sweep and **must not
be edited afterwards**; it is echoed into every results JSON so any figure can be
checked against the distribution it was scored under.

### The ceiling is only meaningful at a stated fleet scale

The 10% ceiling is a **ratio**, and one of its terms does not scale with the
fleet. Four of the six oversight components — sentinel audits, Tier 0, discarded
work and rerun work — are per-agent, so they grow with the fleet exactly as
worker spend does and their contribution to the ratio is scale-invariant. The
other two — triage and investigation — do not. A person reviews the alerts HARPY
actually raised, and the alert count is a property of the simulated slice, not
of how many agents that slice stands in for.

So the ratio falls monotonically as the fleet grows, and any statement of the
form "HARPY costs X% overhead" is incomplete until it names a fleet scale. Every
results JSON therefore carries `fleet_scale`, `review_cost_share_of_overhead`,
and `min_fleet_dollars_per_hour_for_viability` — the smallest fleet spend at
which that run clears the ceiling, or `null` if the run's scale-invariant
oversight cost is already at or over 10% and no fleet size can help.

**At `fleet_scale` 1 the ceiling is unreachable by construction, and that is a
scale artifact rather than a result about HARPY.** The simulated slice is 30
agents for 500 ticks, about $156 of worker spend. A single alert that escalates
costs $60 of analyst time, which is 38% of that bill on its own. There is no
sampling policy, budget fraction or detector accuracy that fixes this: it is
arithmetic about the size of the sample, and the previous flat
`human_review_dollars: 25.0` constant hard-coded a mild version of the same
mistake into every figure the simulator produced.

### Minimum fleet scale at which each arm clears 10%

Measured on the seeds 0..2 sweep at the **shipped operating point** —
`budget_pct` 0.05, lineage fidelity 1.0, alert budget 0.05 alerts/agent-hour —
averaged over seeds and, for HYBRID, over reserve fractions. Deliberately not
the best cell per arm: a best-of would make the table a claim about the sweep
rather than about the configuration anyone would actually run. Numbers come from
`configs/pricing.smoke.yaml` and are therefore **unverified** — the shape is
real, the prices are arbitrary.

"min fleet spend" is the continuous threshold, in worker dollars per hour, at
which that arm's overhead crosses below 10%; the four scale columns are the
sweep's discrete samples of the same curve. Read the `vs TIER0_ONLY` column
first: **clearing the ceiling while detecting nothing is not clearing
anything**, and most of the rows that clear at scale 1 clear it that way.

| arm | severity | detection | vs TIER0_ONLY | @1 | @10 | @100 | @1000 | min scale under 10% | min fleet spend |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| NONE | OVERT | 0.00 | -1.00 | 0.0% | 0.0% | 0.0% | 0.0% | 1 | $0/h |
| TIER0_ONLY | OVERT | 1.00 | — | 6.9% | 4.8% | 4.5% | 4.5% | 1 | $8/h |
| RANDOM | OVERT | 1.00 | +0.00 | 11.8% | 9.7% | 9.5% | 9.4% | 10 | $80/h |
| DIRECTED | OVERT | 1.00 | +0.00 | 11.5% | 9.3% | 9.1% | 9.1% | 10 | $50/h |
| HYBRID | OVERT | 1.00 | +0.00 | 11.5% | 9.4% | 9.2% | 9.1% | 10 | $50/h |
| NONE | MODERATE | 0.00 | +0.00 | 0.0% | 0.0% | 0.0% | 0.0% | 1 | $0/h |
| TIER0_ONLY | MODERATE | 0.00 | — | 0.0% | 0.0% | 0.0% | 0.0% | 1 | $0/h |
| RANDOM | MODERATE | 0.00 | +0.00 | 7.7% | 6.9% | 6.9% | 6.9% | 1 | $0/h (1 of 3 seeds never) |
| DIRECTED | MODERATE | 0.00 | +0.00 | 5.0% | 5.0% | 5.0% | 5.0% | 1 | $0/h |
| HYBRID | MODERATE | 0.00 | +0.00 | 5.5% | 5.4% | 5.4% | 5.4% | 1 | $0/h (1 of 15 seeds never) |
| NONE | SUBTLE | 0.00 | +0.00 | 0.0% | 0.0% | 0.0% | 0.0% | 1 | $0/h |
| TIER0_ONLY | SUBTLE | 0.00 | — | 0.0% | 0.0% | 0.0% | 0.0% | 1 | $0/h |
| RANDOM | SUBTLE | 0.00 | +0.00 | 5.0% | 5.0% | 5.0% | 5.0% | 1 | $0/h |
| DIRECTED | SUBTLE | 0.00 | +0.00 | 5.0% | 5.0% | 5.0% | 5.0% | 1 | $0/h |
| HYBRID | SUBTLE | 0.00 | +0.00 | 5.0% | 5.0% | 5.0% | 5.0% | 1 | $0/h |

Every sentinel arm crosses under 10% by `fleet_scale` 10 at this operating
point, and none of them crosses at `fleet_scale` 1. TIER0_ONLY clears at scale
1 because it raises so few alerts that its review bill stays small.

The cells where a sentinel arm actually buys detection TIER0_ONLY was not
already getting are at `budget_pct` 0.20, and none of them clears the ceiling
at any fleet scale:

| arm | severity | detection | vs TIER0_ONLY | @1 | @10 | @100 | @1000 | min scale under 10% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RANDOM | MODERATE | 0.33 | +0.33 | 44.6% | 37.4% | 36.7% | 36.7% | never |
| DIRECTED | MODERATE | 0.67 | +0.67 | 59.1% | 41.1% | 39.4% | 39.2% | never |
| HYBRID | MODERATE | 0.53 | +0.53 | 48.8% | 37.9% | 36.8% | 36.7% | never |

So on this sweep, **no arm clears 10% at any fleet scale while showing
incremental detection over TIER0_ONLY.** Fleet scale removes the review-cost
artifact — the overhead falls by 8 to 18 points between scale 1 and scale
1000 — and what is left at scale 1000 is still three to four times the ceiling.
That residue is discarded and rerun work from false isolations, which is
per-agent and does not amortise. SUBTLE is never detected by any arm at any
budget.

These numbers are from three seeds and unverified prices; they say what the
simulator does, not what a real detector would.

`results/overhead_composition.png` is the figure this table is read off:
stacked bars of the six components by arm and severity, faceted by fleet scale.
The two review bands are two thirds of the MODERATE/RANDOM bar at scale 1 and
invisible at scale 1000.

### Human attention is a second budget

Clearing the dollar ceiling by paging a person every other minute is not
clearing anything, so the alert rate is capped independently of the dollar
budget. `alert_budget.max_alerts_per_agent_hour` is a hard cap that
`response.alert_human()` consults before firing; at the cap, either the
suspicion `isolation_threshold` rises by a configured step until the rate falls
back under it (and decays back down when there is headroom), or the alert is
suppressed and counted. Both branches are real trades and both are scored:
`alerts_per_agent_hour`, `suppressed_alerts`, `effective_isolation_threshold`,
and `detections_lost_to_alert_cap` — the last scored from ground truth, on the
world's side of the isolation guard, because whether a suppressed alert cost a
real detection is exactly the question HARPY is not allowed to ask.

`results/detection_vs_alert_budget.png` sweeps the cap from 0.01/agent-hour to
uncapped. On the seeds 0..2 sweep the cap binds hard at 0.01 — no alert fires at
all, the gate walks up to its ceiling, and OVERT detection falls from 1.00 to
0.22-0.33 — and is slack from 0.05 upwards, where the arms raise only
0.0013-0.0018 alerts per agent-hour and the curve is flat. The interesting
region is therefore between 0.01 and 0.05, which is where a real team's capacity
plausibly sits.

One artifact worth knowing about: the run-to-date half of the cap starts its
clock at zero, so at tick 0 there are no accrued agent-hours and therefore no
alert budget. With adaptation on, the gate climbs for the first ~40 ticks and
decays back over the next ~40. That is why `effective_isolation_threshold`
reads 3.21 rather than 2.60 even at a cap the run never actually strains. Fault
onset is drawn from 10-60% of the run, so almost all faults begin after the
transient has passed, and detection at cap 0.05 matches the uncapped arm
exactly — but the transient is real and would matter for a shorter run.

## Out of scope v1

Adversarial agents that model the sampler. Every fault here is a malfunction,
not a strategy: nothing in the mesh reasons about when it is being watched,
holds back to keep its Tier 0 score down, or times its corruption around audits.
A sampler that is predictable to the thing it is sampling is a different — and
harder — problem, and pretending otherwise would make the budget curves
optimistic in a way this simulator cannot quantify.

## Pricing

`configs/pricing.yaml` ships with **placeholders**. Every price is 0.0 and every
`source` is empty. No price is written in Python source anywhere in this repo.

Before any reported run, replace them with figures read directly off provider
pricing pages, paste the URL and the access date next to each entry, and set
`verified: true`. `harpy sweep` refuses to start and exits nonzero until then.

`--allow-unverified-pricing` exists for smoke runs and CI only. It stamps
`pricing_verified: false` into every results JSON and watermarks the plot.
`configs/pricing.smoke.yaml` holds round synthetic numbers for exactly that
purpose and is not provider pricing.

## Design constraints

These are load-bearing; changing one changes what the results mean.

- **Ground truth is unreadable from the detection path.** `Claim.is_corrupt` and
  `LineagePair.ground_truth` are guarded by property accessors that inspect the
  call stack and raise `GroundTruthLeakError` for any access originating in
  `telemetry`, `sentinel`, `sampler`, `suspicion`, or `response`. `MockSentinel`
  is whitelisted by name. `tests/test_isolation_guard.py` proves this for every
  module, including that the guard cannot be laundered through a helper.
- **Tier 0 is mechanical.** Seven checks, typed fields only, no natural-language
  contradiction detection. A claim payload has no free-text field to read.
  Everything semantic belongs to the sentinel, which is why it costs money.
- **Audit count is derived from dollars.** Nothing configures how many audits to
  run. The budget accrues per tick as a fraction of worker spend, Tier 0's bill
  comes off the top, and audits are drawn until the pool cannot afford another.
- **Isolation never fires on one audit.** `audit_weight < isolation_threshold`
  is enforced at construction, not left to configuration discipline.
- **HARPY sees lineage only through the lossy projection.** `lineage_fidelity`
  is swept from 0.0 to 1.0 because how much oversight is worth buying depends on
  provenance plumbing you may not have.
- **Cost is six components, never one number.** Sentinel, Tier 0, discarded
  work, rerun, triage and investigation, against worker spend as the
  denominator. Discarded work is an agent's spend since its last clean restart —
  a growing quantity, not a flat constant, so late isolations correctly cost
  more than early ones.
- **Human review is priced in minutes, not in dollars per alert.** A flat
  per-alert constant is a number about a fleet size rather than about an alert.
  Cost is `(triage_minutes + escalation × investigation_minutes) / 60 ×
  analyst_cost_per_hour` in expectation, with escalation drawn per review from
  its own RNG stream rather than averaged in, and alerts about the same agent
  inside `batch_window_ticks` collapsing into one review.
- **Fleet scale is swept, not assumed.** The 30-agent mesh is a sample. Costs
  are reported at `fleet_scale` ∈ {1, 10, 100, 1000}; four components scale with
  it and the two review components do not.
- **The alert budget binds independently of the dollar budget.** Human attention
  is finite in a way dollars are not, and a cap that can be exceeded is not a
  cap — the reported `alerts_per_agent_hour` is provably at or under
  `max_alerts_per_agent_hour` for every run.
- **Runs are deterministic.** Same seed, byte-identical JSON. RNG streams are
  separated per component so changing the sampler does not reshuffle the world
  the arms are compared on.

## Layout

```
src/harpy/
  types.py       data model + the ground-truth isolation guard
  mesh.py        the simulated agent mesh (the world)
  faults.py      what OVERT / MODERATE / SUBTLE mean
  lineage.py     ground-truth edges and the lossy observable projection
  telemetry.py   Tier 0: the seven mechanical checks
  sentinel.py    Sentinel protocol, MockSentinel, RealSentinel stub (phase 2)
  sampler.py     the five arms, spending a dollar budget
  ledger.py      pricing (from YAML only), review cost, the six components
  alert_budget.py  the alert-rate cap and the threshold it adapts
  suspicion.py   decaying per-agent score and the isolation gate
  response.py    the five permitted actions
  simulation.py  one run: world + oversight + scoring
  sweep.py       the grid, across processes
  metrics.py     per-run metrics, never averaged across severity
  plot.py        the three figures
  cli.py         harpy run / sweep / plot
```

## Sweep grid

50 seeds x 5 arms x 3 severities x 5 budget fractions x 5 lineage fidelities x
5 alert caps x 4 fleet scales, plus 5 reserve fractions for HYBRID: 675,000
cells. One JSON per cell in `results/`, plus `results/summary.parquet` (one row
per cell; aggregation happens at plot time).

`fleet_scale` multiplies four cost components after the fact and touches no RNG
stream, no threshold and no decision, so the sweep simulates each cell once and
re-costs it at each scale — 168,750 simulations for 675,000 cells.
`tests/test_determinism.py` pins that the re-costed document is byte-identical
to the separately simulated one, so this is a scheduling detail and not a
modelling shortcut.

## Figures

```
results/detection_vs_overhead.png      primary: detection vs overhead, per arm.
                                       Drawn at one stated fleet_scale and one
                                       stated alert cap — the overhead on its x
                                       axis is not scale-free.
results/overhead_composition.png       stacked bars of the six components by arm
                                       and severity, faceted by fleet_scale.
results/detection_vs_alert_budget.png  detection vs max_alerts_per_agent_hour,
                                       per arm, faceted by severity.
```
