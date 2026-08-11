"""The primary artifact: detection bought per dollar of overhead.

x is total incremental overhead as a percentage of worker-fleet cost, y is
detection probability, one curve per arm. Severity is a column facet and
lineage fidelity a row facet because both change the answer and neither may be
averaged away.

Two companions sit alongside it. ``overhead_composition`` breaks the overhead
back out into its six components by fleet scale, which is the only way to see
that the number on the primary figure's x axis is mostly human review at scale 1
and almost none of it at scale 1000. ``detection_vs_alert_budget`` puts the
second budget on the x axis, because a run that clears the dollar ceiling by
paging a person every other minute has not cleared anything.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

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
ARM_ORDER: tuple[str, ...] = tuple(a.value for a in Arm)

#: The six oversight components, in stacking order, with the two that do not
#: scale with the fleet last so they sit on top of the bar and stay legible as
#: they shrink away.
COST_COMPONENTS: tuple[tuple[str, str, str], ...] = (
    ("mean_sentinel_dollars", "sentinel", "#1f77b4"),
    ("mean_tier0_dollars", "Tier 0", "#17becf"),
    ("mean_discarded_work_dollars", "discarded work", "#ff7f0e"),
    ("mean_rerun_work_dollars", "rerun work", "#8c564b"),
    ("mean_triage_dollars", "triage", "#d62728"),
    ("mean_investigation_dollars", "investigation", "#e377c2"),
)


def _pick_slice(cells: list[dict], key: str, requested: float | None) -> float:
    """Resolve which value of a fixed axis a figure is drawn at.

    Facets cannot absorb every axis, so the ones a figure holds fixed have to be
    held at a *stated* value rather than averaged: a curve blended across fleet
    scales is a curve about no fleet in particular. The default is the largest
    value present, which for fleet_scale is the one least distorted by the
    slice's size and for the alert cap is the uncapped control.
    """
    present = sorted({c.get(key, 1.0) for c in cells})
    if requested is None:
        return present[-1]
    if requested not in present:
        raise ValueError(f"no cells at {key}={requested}; present: {present}")
    return requested


def _fmt_cap(cap: float) -> str:
    return "uncapped" if math.isinf(cap) else f"{cap:g}/agent-h"


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


def detection_vs_overhead(
    rows: list[dict],
    out_path: str | Path,
    fleet_scale: float | None = None,
    alert_cap: float | None = None,
) -> Path:
    """Write results/detection_vs_overhead.png from per-run metric rows.

    Drawn at one fleet scale and one alert cap, both named on the figure. The
    overhead on the x axis is not scale-free — at fleet_scale 1 most of it is
    human review — so a figure that did not say which scale it was drawn at
    would not be reporting a number at all.
    """
    if not rows:
        raise ValueError("no results to plot")
    all_cells = aggregate(rows)
    fleet_scale = _pick_slice(all_cells, "fleet_scale", fleet_scale)
    alert_cap = _pick_slice(all_cells, "max_alerts_per_agent_hour", alert_cap)
    cells = [
        c
        for c in all_cells
        if c["fleet_scale"] == fleet_scale and c["max_alerts_per_agent_hour"] == alert_cap
    ]

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
        "(MockSentinel, assumed detection)\n"
        f"fleet_scale {fleet_scale:g}  ·  alert budget {_fmt_cap(alert_cap)}"
    )
    fig.suptitle(title, fontsize=12)

    _watermark_if_unverified(fig, rows)

    fig.tight_layout(rect=(0, 0.045, 1, 0.94))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def _watermark_if_unverified(fig, rows: list[dict]) -> None:
    if all(row.get("pricing_verified", False) for row in rows):
        return
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


def overhead_composition(
    rows: list[dict], out_path: str | Path, alert_cap: float | None = None
) -> Path:
    """Stacked bars of the six oversight components, by arm, faceted by scale.

    This is the figure that makes the review-cost problem legible. Four of the
    six components are per-agent and scale with the fleet; triage and
    investigation do not, because a person reviews the alerts HARPY raised and
    the alert count belongs to the simulated slice. So at fleet_scale 1 the two
    review bands are most of the bar and at fleet_scale 1000 they are a line you
    have to look for. Facets do not share a y axis: the bars grow a thousandfold
    down the rows, and a shared axis would flatten every row but the last.
    """
    if not rows:
        raise ValueError("no results to plot")
    all_cells = aggregate(rows)
    alert_cap = _pick_slice(all_cells, "max_alerts_per_agent_hour", alert_cap)
    cells = [c for c in all_cells if c["max_alerts_per_agent_hour"] == alert_cap]

    scales = sorted({c["fleet_scale"] for c in cells})
    severities = [s for s in SEVERITY_ORDER if any(c["severity"] == s for c in cells)]
    arms = [a for a in ARM_ORDER if any(c["arm"] == a for c in cells)]

    fig, axes = plt.subplots(
        len(scales),
        len(severities),
        figsize=(3.9 * len(severities), 3.0 * len(scales)),
        squeeze=False,
        sharex=True,
    )

    for row_index, scale in enumerate(scales):
        for col_index, severity in enumerate(severities):
            ax = axes[row_index][col_index]
            facet = [
                c
                for c in cells
                if c["fleet_scale"] == scale and c["severity"] == severity
            ]
            positions = range(len(arms))
            bottoms = [0.0] * len(arms)
            for key, _label, color in COST_COMPONENTS:
                # Averaged over budget, fidelity, reserve and seed: this figure
                # is about the shape of the bill, and those axes move its size.
                values = []
                for arm in arms:
                    group = [c for c in facet if c["arm"] == arm]
                    values.append(
                        sum(c[key] for c in group) / len(group) if group else 0.0
                    )
                ax.bar(
                    positions,
                    values,
                    bottom=bottoms,
                    color=color,
                    width=0.68,
                    linewidth=0.0,
                )
                bottoms = [b + v for b, v in zip(bottoms, values, strict=True)]

            ax.set_xticks(list(positions))
            ax.set_xticklabels(arms, rotation=35, ha="right", fontsize=7.5)
            ax.grid(axis="y", alpha=0.25, linewidth=0.5)
            ax.tick_params(axis="y", labelsize=7.5)
            if row_index == 0:
                ax.set_title(severity, fontsize=11)
            if col_index == 0:
                ax.set_ylabel(f"fleet_scale {scale:g}\nincremental $", fontsize=9)

    handles = [Patch(facecolor=color, label=label) for _key, label, color in COST_COMPONENTS]
    fig.legend(handles=handles, loc="lower center", ncol=6, frameon=False, fontsize=8.5)
    fig.suptitle(
        "HARPY-SIM phase 1 — overhead composition by fleet scale\n"
        f"alert budget {_fmt_cap(alert_cap)}  ·  triage and investigation do not scale; "
        "the other four do",
        fontsize=11.5,
    )
    _watermark_if_unverified(fig, rows)
    fig.tight_layout(rect=(0, 0.035, 1, 0.955))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def detection_vs_alert_budget(
    rows: list[dict], out_path: str | Path, fleet_scale: float | None = None
) -> Path:
    """Detection against the alert cap, one curve per arm, faceted by severity.

    The x axis is categorical rather than logarithmic because the uncapped
    control belongs on it and infinity has no position on a log axis. Read it as
    an ordered sequence of operating points, not as a continuum.
    """
    if not rows:
        raise ValueError("no results to plot")
    all_cells = aggregate(rows)
    fleet_scale = _pick_slice(all_cells, "fleet_scale", fleet_scale)
    cells = [c for c in all_cells if c["fleet_scale"] == fleet_scale]

    caps = sorted({c["max_alerts_per_agent_hour"] for c in cells})
    severities = [s for s in SEVERITY_ORDER if any(c["severity"] == s for c in cells)]

    fig, axes = plt.subplots(
        1,
        len(severities),
        figsize=(4.2 * len(severities), 3.8),
        squeeze=False,
        sharey=True,
    )

    for col_index, severity in enumerate(severities):
        ax = axes[0][col_index]
        facet = [c for c in cells if c["severity"] == severity]
        for arm in [a for a in ARM_ORDER if any(c["arm"] == a for c in facet)]:
            xs, ys = [], []
            for index, cap in enumerate(caps):
                group = [
                    c
                    for c in facet
                    if c["arm"] == arm and c["max_alerts_per_agent_hour"] == cap
                ]
                if not group:
                    continue
                xs.append(index)
                ys.append(sum(c["detection_rate"] for c in group) / len(group))
            ax.plot(xs, ys, label=arm, linewidth=1.6, markersize=4.5, **ARM_STYLE.get(arm, {}))
        ax.set_xticks(range(len(caps)))
        ax.set_xticklabels(
            ["inf" if math.isinf(cap) else f"{cap:g}" for cap in caps], fontsize=8
        )
        ax.set_ylim(-0.03, 1.03)
        ax.grid(alpha=0.25, linewidth=0.5)
        ax.set_title(severity, fontsize=11)
        ax.set_xlabel("max_alerts_per_agent_hour", fontsize=9)
        if col_index == 0:
            ax.set_ylabel("detection probability", fontsize=9)

    present = [arm for arm in ARM_ORDER if any(c["arm"] == arm for c in cells)]
    handles = [
        Line2D([], [], label=arm, linewidth=1.6, markersize=4.5, **ARM_STYLE.get(arm, {}))
        for arm in present
    ]
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False, fontsize=9)
    fig.suptitle(
        "HARPY-SIM phase 1 — detection vs the alert budget\n"
        f"fleet_scale {fleet_scale:g}  ·  human attention as a second binding constraint",
        fontsize=12,
    )
    _watermark_if_unverified(fig, rows)
    fig.tight_layout(rect=(0, 0.09, 1, 0.9))

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
    all_cells = aggregate(rows)
    fleet_scale = _pick_slice(all_cells, "fleet_scale", None)
    alert_cap = _pick_slice(all_cells, "max_alerts_per_agent_hour", None)
    cells = [
        c
        for c in all_cells
        if c["arm"] == Arm.HYBRID.value
        and c["fleet_scale"] == fleet_scale
        and c["max_alerts_per_agent_hour"] == alert_cap
    ]
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
    fig.suptitle(
        f"HYBRID reserve_fraction, lineage fidelity {fidelity:g}\n"
        f"fleet_scale {fleet_scale:g}  ·  alert budget {_fmt_cap(alert_cap)}",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0.08, 1, 0.88))

    out_path = Path(out_path)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
