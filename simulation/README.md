# Precision-Agriculture Synchronization Simulation

This simulator evaluates abstract synchronization policies for precision-agriculture setup data under intermittent connectivity.

It is intentionally framed as a **sensitivity analysis**, not a field-validated model. Public sources document Data Sync setup entities, near-real-time synchronization behavior, transaction-log visibility, precision-agriculture adoption, and rural connectivity barriers. They do not publish machine-level cellular traces, real conflict rates, or proprietary synchronization logs. The simulation therefore sweeps transparent parameter ranges instead of claiming one parameter set is representative of all farms.

## Run

Install the plotting dependency once:

```powershell
python -m pip install -r requirements.txt
```

Then run the simulation and figure generation:

```powershell
python simulate_sync.py --config config.json --out results
```

The core simulation uses the Python standard library. The one-command workflow
also generates SVG charts in `results/figures/`, which requires Matplotlib.

## Outputs

- `high_risk_summary.csv`: policy behavior for high-risk setup entities.
- `paired_policy_comparisons.csv`: paired policy deltas used for paper claims.
- `policy_stats_with_ci.csv`: policy statistics with confidence intervals.
- `policy_summary.csv`: averages by policy across the whole sweep.
- `raw_runs.csv`: one row per scenario, policy, and replication.
- `robustness_summary.csv`: compact robustness summary for manuscript wording.
- `robustness_by_factor.csv`: policy effects stratified by each swept parameter.
- `scenario_robustness.csv`: one row per parameter cell for scenario-level robustness checks.
- `scenario_summary.csv`: averages by connectivity, sync interval, update intensity, and policy.
- `figures/*.svg`: manuscript-ready SVG charts derived from `raw_runs.csv`.

For a curated explanation of the canonical result set, see
`results/simulation_summary.md`. For a broader narrative explanation of the assumptions, policies, outputs, and figure interpretation, see
`simulation_interpretation_guide.md`.

## Experimental Design

The simulation uses common random numbers for policy comparison. For each scenario
and replication, one synthetic day of update events and connectivity states is
generated, then all policies are evaluated against that same realization. This
keeps policy differences from being confounded with different random event draws.

Repeated offline edits from the same display to the same entity are coalesced
before synchronization. The retained pending update keeps the original cloud
base version and the newest local content, which avoids counting a display's own
successive local edits as conflicts with itself.

## Policy Definitions

- `last_write_wins`: resolves conflicts by the newer timestamp.
- `cloud_preferred`: cloud version wins all conflicts.
- `display_preferred`: display version wins all conflicts.
- `manual_review_all`: any detected conflict goes to manual review.
- `domain_aware`: high-risk setup conflicts go to manual review; medium/low-risk conflicts use last-write-wins.

## Main Claim This Can Support

The strongest paper claim is not that the simulator reproduces any vendor's production system. The defensible claim is:

> Domain-aware conflict handling can reduce silent overwrites for high-integrity setup entities while avoiding the manual-review burden of sending every conflict to review.
