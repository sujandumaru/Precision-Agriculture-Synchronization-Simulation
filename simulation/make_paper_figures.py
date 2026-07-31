#!/usr/bin/env python3
"""Create manuscript-ready figures and policy statistics from simulation output."""

import matplotlib.pyplot as plt

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev


POLICY_ORDER = [
    "last_write_wins",
    "cloud_preferred",
    "display_preferred",
    "manual_review_all",
    "domain_aware",
]

POLICY_LABELS = {
    "last_write_wins": "Last-write-wins",
    "cloud_preferred": "Cloud-preferred",
    "display_preferred": "Display-preferred",
    "manual_review_all": "Manual review all",
    "domain_aware": "Domain-aware",
}

POLICY_COLORS = {
    "last_write_wins": "#4E79A7",
    "cloud_preferred": "#F28E2B",
    "display_preferred": "#E15759",
    "manual_review_all": "#59A14F",
    "domain_aware": "#8E6BBE",
}


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a simulation CSV file into a list of row dictionaries."""
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def summarize_by_policy(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    """Calculate policy-level means and 95% confidence intervals."""
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["policy"]].append(row)

    metrics = [
        "conflicts",
        "silent_overwrites",
        "high_risk_silent_overwrites",
        "manual_reviews",
        "high_risk_manual_reviews",
        "local_superseded_updates",
        "stale_ratio",
    ]

    out: list[dict[str, object]] = []
    for policy in POLICY_ORDER:
        policy_rows = grouped[policy]
        item: dict[str, object] = {
            "policy": policy,
            "label": POLICY_LABELS[policy],
            "runs": len(policy_rows),
        }
        for metric in metrics:
            values = [float(row[metric]) for row in policy_rows]
            avg = mean(values)
            sd = stdev(values) if len(values) > 1 else 0.0
            ci95 = 1.96 * sd / math.sqrt(len(values)) if values else 0.0
            item[f"{metric}_mean"] = round(avg, 6)
            item[f"{metric}_ci95"] = round(ci95, 6)
        out.append(item)
    return out


def summarize_paired_comparisons(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    """Calculate paired policy differences on shared scenario realizations."""
    grouped: dict[tuple[str, str, str],
                  dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        key = (
            row["scenario_index"],
            row["replication"],
            row.get("realization_seed") or row["run_seed"],
        )
        grouped[key][row["policy"]] = row

    comparisons = [
        ("last_write_wins", "domain_aware", "high_risk_silent_overwrites"),
        ("last_write_wins", "domain_aware", "silent_overwrites"),
        ("manual_review_all", "domain_aware", "manual_reviews"),
    ]
    output: list[dict[str, object]] = []
    for baseline_policy, comparison_policy, metric in comparisons:
        pairs = [
            (float(policy_rows[baseline_policy][metric]),
             float(policy_rows[comparison_policy][metric]))
            for policy_rows in grouped.values()
            if baseline_policy in policy_rows and comparison_policy in policy_rows
        ]
        baseline_values = [baseline for baseline, _comparison in pairs]
        comparison_values = [comparison for _baseline, comparison in pairs]
        reductions = [baseline - comparison for baseline, comparison in pairs]
        baseline_avg = mean(baseline_values)
        comparison_avg = mean(comparison_values)
        reduction_avg = mean(reductions)
        reduction_sd = stdev(reductions) if len(reductions) > 1 else 0.0
        reduction_ci95 = 1.96 * reduction_sd / \
            math.sqrt(len(reductions)) if reductions else 0.0
        percent_reduction = (
            100.0 * reduction_avg / baseline_avg
            if baseline_avg > 0
            else 0.0
        )
        output.append(
            {
                "baseline_policy": baseline_policy,
                "comparison_policy": comparison_policy,
                "metric": metric,
                "paired_runs": len(pairs),
                "baseline_mean": round(baseline_avg, 6),
                "comparison_mean": round(comparison_avg, 6),
                "baseline_minus_comparison_mean": round(reduction_avg, 6),
                "baseline_minus_comparison_ci95": round(reduction_ci95, 6),
                "percent_reduction": round(percent_reduction, 2),
            }
        )
    return output


def summarize_scenario_robustness(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    """Summarize domain-aware robustness for each parameter-cell scenario."""
    grouped: dict[str, dict[str, list[dict[str, str]]]
                  ] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["scenario_index"]][row["policy"]].append(row)

    scenario_fields = [
        "fleet_size",
        "connectivity",
        "sync_interval_minutes",
        "updates_per_display_day",
        "conflict_bias",
        "high_risk_update_share",
    ]
    output: list[dict[str, object]] = []
    for scenario_index, policy_rows in sorted(grouped.items(), key=lambda item: int(item[0])):
        if not {"last_write_wins", "manual_review_all", "domain_aware"}.issubset(policy_rows):
            continue
        representative = policy_rows["domain_aware"][0]

        def avg(policy: str, metric: str) -> float:
            """Return the mean metric value for one policy in this scenario."""
            return mean(float(row[metric]) for row in policy_rows[policy])

        manual_all = avg("manual_review_all", "manual_reviews")
        manual_domain = avg("domain_aware", "manual_reviews")
        silent_lww = avg("last_write_wins", "silent_overwrites")
        silent_domain = avg("domain_aware", "silent_overwrites")
        high_risk_lww = avg("last_write_wins", "high_risk_silent_overwrites")
        high_risk_domain = avg("domain_aware", "high_risk_silent_overwrites")
        manual_reduction = manual_all - manual_domain
        silent_reduction = silent_lww - silent_domain
        output.append(
            {
                "scenario_index": scenario_index,
                **{field: representative[field] for field in scenario_fields},
                "runs_per_policy": len(policy_rows["domain_aware"]),
                "manual_review_all_mean": round(manual_all, 6),
                "domain_aware_manual_reviews_mean": round(manual_domain, 6),
                "manual_review_reduction_mean": round(manual_reduction, 6),
                "manual_review_reduction_percent": round(100.0 * manual_reduction / manual_all, 2)
                if manual_all > 0
                else 0.0,
                "last_write_wins_silent_overwrites_mean": round(silent_lww, 6),
                "domain_aware_silent_overwrites_mean": round(silent_domain, 6),
                "silent_overwrite_reduction_mean": round(silent_reduction, 6),
                "silent_overwrite_reduction_percent": round(100.0 * silent_reduction / silent_lww, 2)
                if silent_lww > 0
                else 0.0,
                "last_write_wins_high_risk_silent_overwrites_mean": round(high_risk_lww, 6),
                "domain_aware_high_risk_silent_overwrites_mean": round(high_risk_domain, 6),
                "domain_aware_zero_high_risk_silent_overwrites": high_risk_domain == 0.0,
                "domain_aware_not_more_manual_review_than_review_all": manual_domain <= manual_all,
            }
        )
    return output


def summarize_robustness_by_factor(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    """Aggregate robustness metrics by each individual swept factor."""
    scenario_fields = [
        "fleet_size",
        "connectivity",
        "sync_interval_minutes",
        "updates_per_display_day",
        "conflict_bias",
        "high_risk_update_share",
    ]
    output: list[dict[str, object]] = []
    for field in scenario_fields:
        values = sorted({row[field] for row in rows},
                        key=lambda value: (len(value), value))
        for value in values:
            subset = [row for row in rows if row[field] == value]
            by_policy: dict[str, list[dict[str, str]]] = defaultdict(list)
            for row in subset:
                by_policy[row["policy"]].append(row)

            def avg(policy: str, metric: str) -> float:
                """Return the mean metric value for one policy in this factor group."""
                return mean(float(row[metric]) for row in by_policy[policy])

            manual_all = avg("manual_review_all", "manual_reviews")
            manual_domain = avg("domain_aware", "manual_reviews")
            silent_lww = avg("last_write_wins", "silent_overwrites")
            silent_domain = avg("domain_aware", "silent_overwrites")
            high_risk_domain = avg(
                "domain_aware", "high_risk_silent_overwrites")
            manual_reduction = manual_all - manual_domain
            silent_reduction = silent_lww - silent_domain
            output.append(
                {
                    "factor": field,
                    "value": value,
                    "runs_per_policy": len(by_policy["domain_aware"]),
                    "manual_review_all_mean": round(manual_all, 6),
                    "domain_aware_manual_reviews_mean": round(manual_domain, 6),
                    "manual_review_reduction_percent": round(100.0 * manual_reduction / manual_all, 2)
                    if manual_all > 0
                    else 0.0,
                    "last_write_wins_silent_overwrites_mean": round(silent_lww, 6),
                    "domain_aware_silent_overwrites_mean": round(silent_domain, 6),
                    "silent_overwrite_reduction_percent": round(100.0 * silent_reduction / silent_lww, 2)
                    if silent_lww > 0
                    else 0.0,
                    "domain_aware_high_risk_silent_overwrites_mean": round(high_risk_domain, 6),
                }
            )
    return output


def summarize_robustness_overall(scenario_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Create a compact overall robustness summary from scenario rows."""

    def safe_median(values: list[float]) -> float:
        """Return the median when values exist, otherwise return zero."""
        return median(values) if values else 0.0

    manual_reduction_percent = [
        float(row["manual_review_reduction_percent"])
        for row in scenario_rows
    ]
    manual_reduction_percent_nonzero = [
        float(row["manual_review_reduction_percent"])
        for row in scenario_rows
        if float(row["manual_review_all_mean"]) > 0
    ]
    silent_reduction_percent = [
        float(row["silent_overwrite_reduction_percent"])
        for row in scenario_rows
    ]
    silent_reduction_percent_nonzero = [
        float(row["silent_overwrite_reduction_percent"])
        for row in scenario_rows
        if float(row["last_write_wins_silent_overwrites_mean"]) > 0
    ]
    return [
        {
            "scenario_cells": len(scenario_rows),
            "scenario_cells_with_manual_reviews_under_review_all": len(manual_reduction_percent_nonzero),
            "scenario_cells_with_silent_overwrites_under_lww": len(silent_reduction_percent_nonzero),
            "domain_aware_zero_high_risk_silent_overwrite_cells": sum(
                1 for row in scenario_rows if row["domain_aware_zero_high_risk_silent_overwrites"]
            ),
            "domain_aware_not_more_manual_review_cells": sum(
                1 for row in scenario_rows if row["domain_aware_not_more_manual_review_than_review_all"]
            ),
            "manual_review_reduction_percent_min": round(min(manual_reduction_percent), 2),
            "manual_review_reduction_percent_median": round(median(manual_reduction_percent), 2),
            "manual_review_reduction_percent_max": round(max(manual_reduction_percent), 2),
            "manual_review_reduction_percent_nonzero_median": round(safe_median(manual_reduction_percent_nonzero), 2),
            "silent_overwrite_reduction_percent_min": round(min(silent_reduction_percent), 2),
            "silent_overwrite_reduction_percent_median": round(median(silent_reduction_percent), 2),
            "silent_overwrite_reduction_percent_max": round(max(silent_reduction_percent), 2),
            "silent_overwrite_reduction_percent_nonzero_median": round(safe_median(silent_reduction_percent_nonzero), 2),
        }
    ]


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    """Write dictionaries to a CSV file using the first row as the schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def matplotlib_bar_chart(
    path: Path,
    title: str,
    subtitle: str,
    rows: list[dict[str, object]],
    metric: str,
    y_label: str,
    note: str,
) -> None:
    """Create a manuscript-ready SVG bar chart with Matplotlib."""
    values = [float(row[f"{metric}_mean"]) for row in rows]
    errors = [float(row[f"{metric}_ci95"]) for row in rows]
    labels = [str(row["label"]) for row in rows]
    colors = [POLICY_COLORS[str(row["policy"])] for row in rows]
    max_value = max((v + e for v, e in zip(values, errors)), default=1.0)
    max_value = max(max_value * 1.18, 0.2)

    fig, ax = plt.subplots(figsize=(10.8, 6.8), dpi=100)
    x_positions = list(range(len(rows)))
    bars = ax.bar(
        x_positions,
        values,
        color=colors,
        width=0.72,
        yerr=errors,
        error_kw={"ecolor": "#111827", "elinewidth": 1.4, "capsize": 4, "capthick": 1.4},
    )

    ax.set_title(title, fontsize=15, fontweight="bold", color="#111827", pad=28)
    ax.text(
        0.5,
        1.025,
        subtitle,
        ha="center",
        va="bottom",
        transform=ax.transAxes,
        fontsize=10,
        color="#4b5563",
    )
    ax.set_ylabel(y_label, fontsize=10, color="#111827")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(labels, fontsize=9, color="#111827")
    ax.set_ylim(0, max_value)
    ax.set_xlim(-0.65, len(rows) - 0.35)
    ax.grid(axis="y", color="#e5e7eb", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#111827")
    ax.spines["bottom"].set_color("#111827")
    ax.tick_params(axis="y", colors="#374151", labelsize=9)
    ax.tick_params(axis="x", length=0)

    for bar, value, error in zip(bars, values, errors):
        label_y = min(value + error + max_value * 0.025, max_value * 0.96)
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            label_y,
            f"{value:.2f}",
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
            color="#111827",
        )

    fig.text(0.08, 0.075, note, ha="left", fontsize=9, color="#4b5563")
    fig.text(
        0.08,
        0.045,
        "Error bars show 95% confidence intervals over all simulation runs for each policy.",
        ha="left",
        fontsize=9,
        color="#4b5563",
    )
    fig.subplots_adjust(left=0.12, right=0.96, top=0.80, bottom=0.24)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def write_analysis_outputs(raw_path: Path, out_dir: Path) -> None:
    """Write paper-ready statistics and figures derived from raw run output."""
    rows = read_rows(raw_path)
    stats = summarize_by_policy(rows)
    write_csv(out_dir / "policy_stats_with_ci.csv", stats)
    paired_stats = summarize_paired_comparisons(rows)
    write_csv(out_dir / "paired_policy_comparisons.csv", paired_stats)
    scenario_robustness = summarize_scenario_robustness(rows)
    write_csv(out_dir / "scenario_robustness.csv", scenario_robustness)
    write_csv(out_dir / "robustness_by_factor.csv",
              summarize_robustness_by_factor(rows))
    write_csv(out_dir / "robustness_summary.csv",
              summarize_robustness_overall(scenario_robustness))

    figure_dir = out_dir / "figures"
    subtitle = "Means across 14,580 runs per policy; lower values indicate fewer undesirable outcomes."
    matplotlib_bar_chart(
        figure_dir / "figure_1_high_risk_silent_overwrites.svg",
        "High-risk silent overwrites by conflict policy",
        subtitle,
        stats,
        "high_risk_silent_overwrites",
        "Mean high-risk silent overwrites per run",
        r"$\bf{\ Domain-aware}$ and $\bf{\ Manual\ review\ all}$ policies prevent silent overwrites for high-integrity setup entities by construction.",
    )
    matplotlib_bar_chart(
        figure_dir / "figure_2_total_manual_reviews.svg",
        "Manual-review burden by conflict policy",
        subtitle,
        stats,
        "manual_reviews",
        "Mean manual-review cases per run",
        r"$\bf{\ Domain-aware}$ routes only high-risk conflicts to review, reducing review burden compared with reviewing all conflicts.",
    )
    matplotlib_bar_chart(
        figure_dir / "supplement_stale_ratio.svg",
        "Stale entity-display ratio by conflict policy",
        "Means across 14,580 runs per policy; similar values indicate comparable propagation delay.",
        stats,
        "stale_ratio",
        "Mean stale ratio",
        "This result is supplemental because the policies mainly differ in conflict handling, not ordinary sync delay.",
    )


def main() -> None:
    """Parse CLI arguments and write analysis outputs."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=Path("results/raw_runs.csv"))
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args()
    write_analysis_outputs(args.raw, args.out)
    print(f"Wrote analysis statistics and figures to {args.out}")


if __name__ == "__main__":
    main()
