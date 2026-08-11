"""The primary artifact: detection bought per dollar of overhead.

x is total incremental overhead as a percentage of worker-fleet cost, y is
detection probability, one curve per arm. Severity is a column facet and
lineage fidelity a row facet because both change the answer and neither may be
averaged away.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from .metrics import aggregate  # noqa: E402
from .types import Arm, Severity  # noqa: E402

ARM_STYLE: dict[str, dict] = {
    Arm.NONE.value: {"color": "#9e9e9e", "marker": "o", "linestyle": ":"},
    Arm.TIER0_ONLY.value: {"color": "#1f77b4", "marker": "s", "linestyle": "--"},
    Arm.RANDOM.value: {"color": "#2ca02c", "marker": "^", "linestyle": "-"},
    Arm.DIRECTED.value: {"color": "#d62728", "marker": "D", "linestyle": "-"},
    Arm.HYBRID.value: {"color": "#9467bd", "marker": "v", "linestyle": "-"},
}
SEVERITY_ORDER: tuple[str, ...] = tuple(s.value for s in Severity)


def _cells_by_arm(cells: list[dict], severity: str, fidelity: float) -> dict[str, list[dict]]:
    """Group a facet's cells by arm, collapsing HYBRID's reserve_fraction axis.

    HYBRID is swept over five reserve fractions; the main figure shows one curve
    per arm, so those are averaged into a single HYBRID curve. The per-reserve
    breakdown is the companion figure, not a hidden best-of.
    """
    per_arm: dict[str, dict[float, list[dict]]] = {}
    for cell in cells:
        if cell["severity"] != severity or cell["lineage_fidelity"] != fidelity:
            continue
        per_arm.setdefault(cell["arm"], {}).setdefault(cell["budget_pct"], []).append(cell)

    out: dict[str, list[dict]] = {}
    for arm, by_budget in per_arm.items():
        points = []
        for budget in sorted(by_budget):
            group = by_budget[budget]
            points.append(
                {
                    "budget_pct": budget,
                    "overhead_pct": sum(c["overhead_pct"] for c in group) / len(group),
                    "detection_rate": sum(c["detection_rate"] for c in group) / len(group),
                }
            )
        out[arm] = sorted(points, key=lambda p: p["overhead_pct"])
    return out


def detection_vs_overhead(rows: list[dict], out_path: str | Path) -> Path:
    """Write results/detection_vs_overhead.png from per-run metric rows."""
    if not rows:
        raise ValueError("no results to plot")
    cells = aggregate(rows)

    severities = [s for s in SEVERITY_ORDER if any(c["severity"] == s for c in cells)]
    fidelities = sorted({c["lineage_fidelity"] for c in cells})
    n_rows, n_cols = len(fidelities), len(severities)

    fig, axes = plt.subplots(
        n_rows,
        n_cols,
        figsize=(4.2 * n_cols, 3.4 * n_rows),
        squeeze=False,
        sharex=True,
        sharey=True,
    )

    for row_index, fidelity in enumerate(fidelities):
        for col_index, severity in enumerate(severities):
            ax = axes[row_index][col_index]
            for arm, points in sorted(_cells_by_arm(cells, severity, fidelity).items()):
                style = ARM_STYLE.get(arm, {})
                ax.plot(
                    [p["overhead_pct"] * 100.0 for p in points],
                    [p["detection_rate"] for p in points],
                    label=arm,
                    linewidth=1.6,
                    markersize=4.5,
                    **style,
                )
            ax.axvline(10.0, color="#444444", linewidth=0.9, alpha=0.7)
            ax.set_ylim(-0.03, 1.03)
            # Symlog, linear below 1%: false isolations can push a cell past
            # 200% overhead, and on a linear axis those outliers would squash
            # the 0-20% band the kill condition is actually decided in. The
            # linear threshold keeps the NONE arm's exact zero on the axis.
            ax.set_xscale("symlog", linthresh=1.0, linscale=0.5)
            ax.grid(alpha=0.25, linewidth=0.5, which="both")
            if row_index == 0:
                ax.set_title(severity, fontsize=11)
            if row_index == 0 and col_index == 0:
                ax.annotate(
                    "10% kill condition",
                    xy=(10.0, 0.5),
                    xytext=(2, 4),
                    textcoords="offset points",
                    rotation=90,
                    fontsize=7,
                    color="#444444",
                )
            if col_index == 0:
                ax.set_ylabel(f"fidelity {fidelity:g}\ndetection probability", fontsize=9)
            if row_index == n_rows - 1:
                ax.set_xlabel("incremental overhead (% of worker cost)", fontsize=9)

    # One shared x-range across every facet, set once: the axes are shared, so
    # per-axes limits would leave the last facet's range clipping all the others.
    widest = max(cell["overhead_pct"] for cell in cells) * 100.0
    axes[0][0].set_xlim(0.0, max(widest * 1.2, 25.0))

    # Built from the arms present in the data rather than from one facet's
    # handles, which would silently drop an arm that only appears elsewhere.
    present = [arm.value for arm in Arm if any(c["arm"] == arm.value for c in cells)]
    handles = [
        Line2D([], [], label=arm, linewidth=1.6, markersize=4.5, **ARM_STYLE.get(arm, {}))
        for arm in present
    ]
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False, fontsize=9)
    title = (
        "HARPY-SIM phase 1 — detection vs incremental overhead "
        "(MockSentinel, assumed detection)"
    )
    fig.suptitle(title, fontsize=12)

    if not all(row.get("pricing_verified", False) for row in rows):
        fig.text(
            0.5,
            0.5,
            "UNVERIFIED PRICING",
            fontsize=52,
            color="#d62728",
            alpha=0.13,
            ha="center",
            va="center",
            rotation=28,
            zorder=10,
        )

    fig.tight_layout(rect=(0, 0.045, 1, 0.96))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def hybrid_reserve_breakdown(rows: list[dict], out_path: str | Path) -> Path | None:
    """Companion figure: HYBRID split out by reserve_fraction, per severity.

    The main figure averages HYBRID's reserve fractions into one curve. This one
    shows what that average is hiding, at the highest lineage fidelity present.
    """
    cells = [c for c in aggregate(rows) if c["arm"] == Arm.HYBRID.value]
    if not cells:
        return None
    fidelity = max(c["lineage_fidelity"] for c in cells)
    severities = [s for s in SEVERITY_ORDER if any(c["severity"] == s for c in cells)]

    fig, axes = plt.subplots(
        1, len(severities), figsize=(4.2 * len(severities), 3.6), squeeze=False
    )
    for col_index, severity in enumerate(severities):
        ax = axes[0][col_index]
        facet = [
            c for c in cells if c["severity"] == severity and c["lineage_fidelity"] == fidelity
        ]
        for reserve in sorted({c["reserve_fraction"] for c in facet}):
            points = sorted(
                (c for c in facet if c["reserve_fraction"] == reserve),
                key=lambda c: c["overhead_pct"],
            )
            ax.plot(
                [c["overhead_pct"] * 100.0 for c in points],
                [c["detection_rate"] for c in points],
                marker="o",
                markersize=4,
                linewidth=1.4,
                label=f"reserve {reserve:g}",
            )
        ax.set_title(severity, fontsize=11)
        ax.set_ylim(-0.03, 1.03)
        ax.grid(alpha=0.25, linewidth=0.5)
        ax.set_xlabel("incremental overhead (% of worker cost)", fontsize=9)
        if col_index == 0:
            ax.set_ylabel("detection probability", fontsize=9)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False, fontsize=9)
    fig.suptitle(f"HYBRID reserve_fraction, lineage fidelity {fidelity:g}", fontsize=12)
    fig.tight_layout(rect=(0, 0.08, 1, 0.93))

    out_path = Path(out_path)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
