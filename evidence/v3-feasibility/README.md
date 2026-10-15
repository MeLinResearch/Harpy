# V3 pre-freeze pilot

A design input, not study evidence. Nothing here is a PASS, FAIL or INCONCLUSIVE study result.

- **Seeds:** 100000–100039, disjoint from every study seed by construction (`harpy.feasibility.PILOT_SEED_FLOOR`).
- **Fixed for all cells:** SUBTLE faults, `configs/v3.yaml`, 5% audit budget, HYBRID reserve 0.5, fleet scale 100, alert cap 0.05.
- **Main grid, lineage fidelity 1.0:**
  - sentinel price: `configs/pricing.v3.*.yaml`
  - worker tier: gpt-6-luna, gpt-4.1-mini, gpt-6.1-sol, gpt-6-astra
  - arm: RANDOM, HYBRID, EXPOSURE
  - follow-up depth: 0 and 4
  - blame tracing: off and on
  - review scaling: fixed and linear
- **Stress slices** at gpt-6.1-sol workers and a Claude Haiku 4.5 sentinel:
  - `fidelity-0.5`: lineage fidelity 0.5, tracing off and on.
  - `evidence-0.5`: `sentinel.evidence_accuracy` 0.5, tracing on.
- **Decision rule replayed:** v2's, at least 10 points over matched TIER0_ONLY and below 10% overhead, at 30–500 seeds. A cell is FREEZABLE only at 80% assurance.

| File | Contents |
| --- | --- |
| `runs.jsonl.gz` | 6,200 simulations, each costed under both review policies. Each row carries its `slice` (absent means `main`). |
| `grid.json`, `grid.csv` | Per-cell arithmetic, pilot estimates, assurance curve and verdict. |
| `manifest.json` | File hashes. Unsigned. |

Regenerate with `uv run python scripts/v3_feasibility_grid.py --out <dir> --seeds 100000..100039`.

The runs were collected in two batches on this branch. The first 2,800 (tracing off) predate relay scoring and blame tracing, so their `relay_isolations` field is absent and `grid.json` reports it as `null`, not zero. Neither later change alters an untraced run's dynamics. The current tree reproduces randomly sampled runs from both batches and all slices metric-for-metric, excluding `experiment_fingerprint`, which hashes the source.

See [the v3 design notes](../../docs/STUDY_V3_DESIGN.md) for what these numbers mean.
