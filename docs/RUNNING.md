# Run and inspect

[← Project overview](../README.md) · [Architecture](ARCHITECTURE.md) · [Methodology](METHODOLOGY.md)

## Setup

Install [uv](https://docs.astral.sh/uv/) and use Python 3.11 or newer.

```bash
git clone https://github.com/MeLinResearch/Harpy.git
cd Harpy
uv sync --extra dev
uv run ruff check .
uv run pytest -q
```

The simulator needs no model credentials. Its audit outcomes and token usage are simulated.

## One smoke run

```bash
uv run harpy run --seed 0 \
  --pricing configs/pricing.smoke.yaml \
  --allow-unverified-pricing \
  --out results/smoke/
uv run harpy plot --results results/smoke/ \
  --out results/smoke/detection_vs_overhead.png
```

The command prints the output JSON path and selected metrics. The synthetic price file requires the explicit waiver; results carry `pricing_verified: false`, and figures are watermarked. This run validates the tooling, not the economic viability of the design.

Inspect the JSON for the run specification, effective detector characteristics, fault distribution, cost components, and metrics. Use `harpy run --help`, `harpy sweep --help`, and `harpy plot --help` for supported arguments.

## A smaller sweep

The full default grid is large. This example narrows the seed, fleet-scale, and alert-cap axes while retaining all sampling arms, severities, budgets, and lineage fidelities:

```bash
uv run harpy sweep --config configs/default.yaml \
  --pricing configs/pricing.smoke.yaml \
  --seeds 0..0 --fleet-scales 1,1000 --alert-caps 0.05,inf \
  --processes 2 --allow-unverified-pricing \
  --out results/smoke-sweep/
uv run harpy plot --results results/smoke-sweep/ \
  --fleet-scale 1000 --alert-cap 0.05 \
  --out results/smoke-sweep/detection_vs_overhead.png
```

This is still a multi-configuration sweep and takes longer than one run. One seed is not a robust statistical comparison. `inf` is the uncapped attention control. Explicit plot options state the slice being displayed instead of silently mixing fleet scales or alert caps.

## Before reporting numerical results

1. Replace the placeholders in `configs/pricing.yaml` with sourced prices and access dates; set `verified: true` only after verifying them.
2. Review the analyst rate, review duration, fault distribution, and detector assumptions in `configs/default.yaml`.
3. Freeze the fault distribution before the sweep and retain the recorded inputs.
4. Run without `--allow-unverified-pricing`.
5. Report the fleet scale, attention cap, detector assumptions, and matched `TIER0_ONLY` comparison alongside any cost or detection claim.

Verification of prices does not make the mock detector empirical. Phase 1 remains a conditional simulation.

## Generated artifacts

| Artifact | Meaning |
| --- | --- |
| Per-run JSON | Inputs, assumptions, pricing state, metrics, and ledger. |
| `summary.parquet` | One summary row per sweep cell. |
| `detection_vs_overhead.png` | Detection against incremental overhead at a stated fleet scale and alert cap. |
| `overhead_composition.png` | Six cost components, including the review costs. |
| `detection_vs_alert_budget.png` | Detection across human-attention caps. |
| `detection_vs_overhead_hybrid_reserve.png` | HYBRID reserve breakdown, when applicable. |

Generated output is not bundled as a demonstrated production result. See the [methodology](METHODOLOGY.md) for scope and interpretation.
