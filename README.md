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
- **Cost is six components, never one number.** Worker, sentinel, Tier 0,
  discarded work, rerun, human review. Discarded work is an agent's spend since
  its last clean restart — a growing quantity, not a flat constant, so late
  isolations correctly cost more than early ones.
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
  ledger.py      pricing (from YAML only) and the six cost components
  suspicion.py   decaying per-agent score and the isolation gate
  response.py    the five permitted actions
  simulation.py  one run: world + oversight + scoring
  sweep.py       the grid, across processes
  metrics.py     per-run metrics, never averaged across severity
  plot.py        detection vs overhead
  cli.py         harpy run / sweep / plot
```

## Sweep grid

50 seeds x 5 arms x 3 severities x 5 budget fractions x 5 lineage fidelities,
plus 5 reserve fractions for HYBRID: 33,750 runs. One JSON per run in
`results/`, plus `results/summary.parquet` (one row per run; aggregation happens
at plot time).
