# V3 pre-freeze pilot

A design input, not study evidence. Nothing here is a PASS, FAIL or INCONCLUSIVE study result.

- **Seeds:** 100000–100039, disjoint from every study seed by construction (`harpy.feasibility.PILOT_SEED_FLOOR`).
- **Fixed for all cells:** SUBTLE faults, `configs/v3.yaml`, 5% audit budget, lineage fidelity 1.0, HYBRID reserve 0.5, fleet scale 100, alert cap 0.05.
- **Swept:**
  - sentinel price: `configs/pricing.v3.*.yaml`
  - worker tier: gpt-6-luna, gpt-4.1-mini, gpt-6.1-sol, gpt-6-astra
  - arm: RANDOM, HYBRID, EXPOSURE
  - follow-up depth: 0 and 4
  - review scaling: fixed and linear
- **Decision rule replayed:** v2's, at least 10 points over matched TIER0_ONLY and below 10% overhead, at 30–500 seeds. A cell is FREEZABLE only at 80% assurance.

| File | Contents |
| --- | --- |
| `runs.jsonl.gz` | 2,800 simulations, each costed under both review policies. |
| `grid.json`, `grid.csv` | Per-cell arithmetic, pilot estimates, assurance curve and verdict. |
| `manifest.json` | File hashes. Unsigned. |

Regenerate with `uv run python scripts/v3_feasibility_grid.py --out <dir> --seeds 100000..100039`.

The runs were produced on this branch before false isolations were split into relays and pure false positives. That change adds a scoring field and alters no dynamics. The current tree reproduces 16 randomly sampled runs metric-for-metric, excluding `experiment_fingerprint`, which hashes the source.

See [the v3 design notes](../../docs/STUDY_V3_DESIGN.md) for what these numbers mean.
