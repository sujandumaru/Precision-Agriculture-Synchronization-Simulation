# Simulation Result Summary

Generated from:

```powershell
python simulate_sync.py --config config.json --out results
```

Total runs: **72,900**
Replications per scenario-policy combination: **20**
Model type: **parameter-sweep sensitivity analysis**, not field-validated simulation.

Policy comparisons use a **paired common-random-number design**: each scenario replication generates one synthetic day of update events and connectivity states, then evaluates every policy on that same realization. Repeated unsynchronized edits from the same display to the same entity are coalesced before synchronization to avoid treating a display's own successive local edits as independent conflicts.

## Overall Result

The simulation provides a reproducible sensitivity analysis for comparing
conflict-resolution policies under intermittent connectivity.

The strongest result is the **trade-off** between protection and review burden. In this model, the **domain-aware policy** prevents silent overwrites for high-risk setup entities by routing those conflicts to manual review, but it does so with substantially fewer total manual-review cases than a blanket policy that reviews every conflict.

This distinction matters: the zero high-risk overwrite result is guaranteed by the definition of the domain-aware policy. The simulation's contribution is not the zero-overwrite outcome by itself; it is the quantification of the manual-review burden required to obtain that protection across a broad parameter sweep.

## Policy-Level Averages

| Policy | Mean conflicts/run | Mean silent overwrites/run | Mean high-risk silent overwrites/run | Mean manual reviews/run | Mean high-risk manual reviews/run | Mean stale ratio |
|---|---:|---:|---:|---:|---:|---:|
| cloud_preferred | 2.186 | 2.186 | 0.768 | 0.000 | 0.000 | 0.021093 |
| display_preferred | 2.240 | 2.240 | 0.789 | 0.000 | 0.000 | 0.021455 |
| domain_aware | 2.194 | 1.425 | 0.000 | 0.768 | 0.768 | 0.021147 |
| last_write_wins | 2.197 | 2.197 | 0.772 | 0.000 | 0.000 | 0.021174 |
| manual_review_all | 2.186 | 0.000 | 0.000 | 2.186 | 0.768 | 0.021093 |

## Main Quantitative Takeaway

Compared with `manual_review_all`, `domain_aware`:

- reduced total manual-review cases by about **64.9%**,
- preserved the same protection against high-risk silent overwrites.

Compared with `last_write_wins`, `domain_aware`:

- reduced **high-risk silent overwrites** by **100%** in this model,
- reduced **total silent overwrites** by about **35.1%**,
- introduced manual review only for high-risk conflicts.

## Robustness Check

The paired runs were also aggregated into **729 scenario cells**. Domain-aware conflict handling:

- produced zero high-risk silent overwrites in **729/729** cells,
- never required more manual review than `manual_review_all` in **729/729** cells,
- had a median manual-review reduction of **71.4%** among cells where review-all produced at least one review,
- had a median total silent-overwrite reduction of **28.8%** among cells where last-write-wins produced at least one silent overwrite.

Detailed robustness outputs are available in:

- `robustness_summary.csv`
- `robustness_by_factor.csv`
- `scenario_robustness.csv`

## Manuscript Interpretation

The result can be framed as:

> Across a broad sensitivity sweep, domain-aware conflict handling achieved zero silent overwrites for high-integrity setup entities, as intended by policy design, while requiring approximately 64.9% fewer manual-review interventions than routing all conflicts to manual review.

This interpretation treats the simulation as policy evaluation under transparent sensitivity scenarios, not as a field measurement of any production vendor implementation.

## Source and Parameter Basis

The entity categories are anchored in public Data Sync documentation. The connectivity scenarios are stress ranges motivated by public rural-connectivity and precision-agriculture adoption reports. Exact outage durations, update rates, and conflict rates are intentionally varied as sensitivity parameters because public display-level traces are not available.

## Figures

Manuscript-ready figures are available in:

- `figures/figure_1_high_risk_silent_overwrites.svg`
- `figures/figure_2_total_manual_reviews.svg`
- `figures/supplement_stale_ratio.svg`

The first two figures report the primary protection-burden trade-off. The stale-ratio figure provides supplemental context because stale ratios are similar across policies in this simplified model.

## Caveats

- Synthetic update rates are not measured farm behavior.
- Connectivity scenarios are stress conditions, not real cellular traces.
- Manual-review resolution time is not modeled. When a conflict is routed to review, the simulation records the review case and temporarily aligns the display to the cloud version. This represents a conservative "hold for review while preserving cloud state" assumption, not a claim about a production interface.
- Entity semantics are simplified into risk classes.
- The model evaluates policy behavior, not vendor implementation.

These caveats are reflected in the manuscript methodology and limitations text.
