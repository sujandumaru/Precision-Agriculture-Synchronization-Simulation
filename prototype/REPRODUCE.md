# Reproducing the reported results

Python 3.10+ standard library only. No packages to install. Matplotlib is required
only to regenerate figures.

## 1. Verify the code before spending hours on it

```bash
python preflight.py
```

Nine checks covering policy-list drift, policies raising on the server's context shape,
silent server errors, conflict-rate outliers, invariant coverage, whether the convergence
invariant can actually fail, determinism, and target arithmetic. Must print
`PRE-FLIGHT PASSED`.

## 2. Full factorial sweep

729 parameter cells x 20 replications x 10 policies = 145,800 paired runs. Every
(cell, replication) is evaluated under all ten policies against an identical workload.

```bash
python sweep.py --cells all --reps 20 --out <output-dir> --new-baselines --resume
```

Shard it across cores with `--shard i --nshards n` on distinct `--port` values.
`--resume` is safe to re-run; it skips completed (cell, replication, policy) triples.

**The output directory must be on local disk.** SQLite cannot open its journal on a
OneDrive- or Dropbox-synced folder and the server will fail to start.

## 3. Analysis

```bash
python baselines.py  --prototype <output-dir>
python agreement.py  --prototype <output-dir> --simulation ../simulation/results/raw_runs.csv
python make_figures.py --prototype <output-dir> --out figures
```

`agreement.py` refuses to report on an incomplete sweep. Cells are enumerated in factor
order, so a partial run collapses the outer factors to a single level and biases the
sample toward one corner of the design; the guard exists because that happened once.

## 4. Fault and crash experiments

These are **separate** from the main sweep and are reported separately.

```bash
python run_experiment.py                     # fault injection, all policies
python run_experiment.py --crash-at 0.5      # SIGKILL the cloud mid-run and restart
```

Failpoints target the commit window directly:

```bash
python cloud_server.py --failpoint before_commit --failpoint-at 3 ...
```

## Determinism

All seeds derive from a fixed master seed. Two independent full executions agreed on
145,787 of 145,800 runs. The thirteen differing runs arise from wall-clock tiebreaks in
the timestamp-dependent policies and from transient transport retries under concurrent
load; no policy mean shifted by more than 0.0002 records per run. The artifact is
reproducible at the precision reported in the paper, not bit-for-bit.
