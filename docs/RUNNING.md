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

1. Use the sourced `configs/pricing.study.yaml`, or create a new pricing file with URLs and access dates; set `verified: true` only after verifying them.
2. Review the analyst rate, review duration, fault distribution, and detector assumptions in `configs/default.yaml`.
3. Freeze the fault distribution before the sweep and retain the recorded inputs.
4. Run without `--allow-unverified-pricing`.
5. Report the fleet scale, review-scaling policy, attention cap, detector assumptions, uncertainty, and matched `TIER0_ONLY` comparison alongside any cost or detection claim.

Verification of prices does not make the mock detector empirical. Phase 1 remains a conditional simulation.

## Reproduce the frozen v2 study

```bash
uv run harpy study --spec configs/study.v2.yaml \
  --out results/study-v2-reproduction/ --processes 4
uv run harpy verify --manifest results/study-v2-reproduction/manifest.json
```

Use an empty output directory. The command writes the specification and raw YAML hash before collecting observations, simulates 1,800 worlds, then re-costs them into 7,200 records. It writes the statistical report and deterministic compressed seed metrics. Expect several minutes, depending on hardware.

To make the compact published packet and regenerate its comparison figure:

```bash
uv run python scripts/publish_study.py \
  --results results/study-v2-reproduction/ \
  --out results/study-v2-packet/ \
  --source-revision YOUR_CHECKOUT_COMMIT
uv run harpy verify --manifest results/study-v2-packet/manifest.json
```

The publishing script recomputes the report from compressed seed metrics before copying it. The packet records both Git revision and executing-source content identity. Documentation changes do not change the hashed simulation source; code or lockfile changes do.

## Review-cost policy and plot selection

Use `harpy run --review-scaling linear`, or `harpy sweep --review-scalings fixed,linear`, to select the accounting assumption. Both policies preserve simulation decisions. A plot must select one policy and one worker tier:

```bash
uv run harpy plot --results results/study-v2-reproduction/runs/ \
  --review-scaling linear --worker-model gpt-4.1-mini \
  --fleet-scale 100 --alert-cap 0.05 \
  --out results/study-v2-reproduction/runs/detection_vs_overhead.png
```

Plots refuse to combine different experiment fingerprints or detector assumptions. When plotting changes a nested artifact directory, regenerate the parent study manifest too if you intend to verify the entire modified study. The frozen published packet has its own manifest and is not altered by this plotting command.

## Replay and artifact verification

```bash
uv run harpy replay --corpus examples/replay/synthetic \
  --out results/replay-reference/
uv run harpy verify --manifest results/replay-reference/manifest.json
uv run harpy verify --manifest evidence/study-v2/manifest.json
```

See [Replay](REPLAY.md) for real detector credentials, cost reservations, corpus rights, and the observation/label contract. Mechanical replay and all simulations need no API key. The published packet contains no live-model evaluation.

## Generated artifacts

| Artifact | Meaning |
| --- | --- |
| Per-run JSON | Inputs, assumptions, pricing state, metrics, and ledger. |
| `summary.parquet` | One summary row per sweep cell. |
| `manifest.json` | Artifact hashes and relationships; verify against a trusted copy. |
| `seed-metrics.json.gz` | Compressed per-seed metrics for recomputing the study report. |
| `detection_vs_overhead.png` | Detection against incremental overhead at a stated fleet scale and alert cap. |
| `overhead_composition.png` | Six cost components, including the review costs. |
| `detection_vs_alert_budget.png` | Detection across human-attention caps. |
| `detection_vs_overhead_hybrid_reserve.png` | HYBRID reserve breakdown, when applicable. |

Generated output is not bundled as a demonstrated production result. See the [methodology](METHODOLOGY.md) for scope and interpretation.

