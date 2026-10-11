"""Make a compact, independently recomputable evidence packet from a study."""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from harpy.provenance import write_manifest  # noqa: E402
from harpy.study import analyze_rows  # noqa: E402


def publish(results: Path, output: Path, revision: str) -> None:
    report = json.loads((results / "report.json").read_text())
    with gzip.open(results / "seed-metrics.json.gz", "rt") as stream:
        rows = json.load(stream)
    recomputed = analyze_rows(rows, report["study"])
    if any(recomputed[k] != report[k] for k in recomputed):
        raise ValueError("published report differs from recomputed seed metrics")
    if output.exists() and any(output.iterdir()):
        raise ValueError("use an empty evidence directory")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "preregistration.json", "seed-metrics.json.gz"):
        shutil.copyfile(results / name, output / name)
    first = next(p for p in sorted((results / "runs").glob("*.json")) if p.name != "manifest.json")
    inputs = json.loads(first.read_text())["provenance"]
    (output / "inputs.json").write_text(
        json.dumps({"provenance": inputs}, indent=2, sort_keys=True) + "\n"
    )
    (output / "build.json").write_text(
        json.dumps(
            {
                "schema": "harpy/evidence-build/1",
                "analysis_source_revision": revision,
                "source_tree_sha256": inputs["source_tree_sha256"],
                "n_base_simulations": report["n_run_records"] // 4,
                "n_recosted_records": report["n_run_records"],
                "output_license": "MIT",
                "real_model_calls": 0,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    plot_sensitivity(report, output / "cost_sensitivity.png")
    write_manifest(output)


def plot_sensitivity(report: dict, path: Path) -> None:
    cells = report["exploratory_cells"]
    primary = report["study"]["primary_cell"]
    arms = ["RANDOM", "DIRECTED", "HYBRID"]
    severities = ["OVERT", "MODERATE", "SUBTLE"]
    fig, axes = plt.subplots(2, 3, figsize=(12, 6), squeeze=False)
    for col, severity in enumerate(severities):
        selected = [
            c
            for c in cells
            if c["severity"] == severity
            and c["budget_pct"] == primary["budget_pct"]
            and c["lineage_fidelity"] == primary["lineage_fidelity"]
            and c["fleet_scale"] == primary["fleet_scale"]
        ]
        for index, arm in enumerate(arms):
            for policy, offset, color in (("fixed", -0.18, "#6487a8"), ("linear", 0.18, "#ab4963")):
                cell = next(
                    c for c in selected if c["arm"] == arm and c["review_scaling"] == policy
                )
                if policy == "linear":
                    estimate = 100 * cell["incremental_detection"]
                    low, high = (100 * v for v in cell["incremental_detection_ci95"])
                    axes[0, col].errorbar(
                        index,
                        estimate,
                        yerr=[[max(0, estimate - low)], [max(0, high - estimate)]],
                        fmt="o",
                        color="#54486c",
                        capsize=4,
                    )
                cost = 100 * cell["overhead_pct"]
                low, high = (100 * v for v in cell["overhead_ci95"])
                axes[1, col].bar(
                    index + offset,
                    cost,
                    width=0.34,
                    color=color,
                    label=policy if index == 0 else None,
                    yerr=[[max(0, cost - low)], [max(0, high - cost)]],
                    capsize=2,
                )
        axes[0, col].set_title(severity, fontsize=11)
        axes[0, col].axhline(10, color="#82796c", linestyle="--", linewidth=1)
        axes[0, col].axhline(0, color="#ccc6be", linewidth=0.7)
        axes[1, col].axhline(10, color="#82796c", linestyle="--", linewidth=1)
        axes[1, col].set_yscale("log")
        axes[1, col].legend(title="Review scaling", fontsize=8, frameon=False)
        for ax in axes[:, col]:
            ax.set_xticks(range(3), arms, fontsize=8)
            ax.spines[["top", "right"]].set_visible(False)
            ax.grid(axis="y", alpha=0.15)
        if col == 0:
            axes[0, col].set_ylabel("Detection gain over Tier 0 (percentage points)", fontsize=9)
            axes[1, col].set_ylabel("Oversight / worker cost (%) • log scale", fontsize=9)
    fig.suptitle(
        "HARPY v2 • conditional simulation with assumed detector accuracy\n"
        "30 paired seeds • 5% audit budget • lineage fidelity 1 • fleet scale 100",
        fontsize=12,
    )
    fig.text(
        0.5,
        0.016,
        "Bars: fixed vs linear review costs. Detection is unchanged by recosting. "
        "Dashed lines: 10-point gain and 10% cost criteria. Intervals: nominal 95%.",
        ha="center",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.045, 1, 0.9))
    fig.savefig(path, dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    publish(args.results, args.out, args.source_revision)
