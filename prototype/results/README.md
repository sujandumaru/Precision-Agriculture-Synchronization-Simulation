# Archived results

Everything here comes from a single clean execution of the code in this repository.
No data from earlier development runs is included.

## Main factorial sweep

`prototype_runs_s0.csv` … `prototype_runs_s3.csv` — 145,800 run-level rows
(729 parameter cells x 20 replications x 10 policies), sharded four ways. Every
(cell, replication) is evaluated under all ten policies against an identical workload.
Faults are **off** in the main sweep, matching the fault-free discrete-event model it is
compared against.

Columns `I1`…`I5` record the per-run safety-invariant outcome; `server_errors` records
handler exceptions and is 0 in every row.

- `baselines_final.txt` — policy-level means and paired comparisons (Table 3)
- `agreement_final.txt` — cross-implementation comparison against `../../simulation/` (Table 4)

Regenerate with:

```bash
python baselines.py  --prototype results/
python agreement.py  --prototype results/ --simulation ../simulation/results/raw_runs.csv
python make_figures.py --prototype results/ --out figures/
```

## Fault and crash experiments

Reported separately from the main sweep, in `fault_experiments/`:

- `fault_injection_results.json` — all ten policies with requests dropped, duplicated and
  reordered at the transport boundary (0.05 / 0.05 / 0.10), connectivity partitions, and one
  display carrying a −120 s clock offset. 27 duplicate deliveries were suppressed, which is
  where invariant I4 is exercised; the fault-free main sweep generates none. All invariants pass.
- `crash_recovery_results.json` — the same experiment with the cloud process terminated by an
  uncatchable signal at the run midpoint and restarted. Audit-derived outcome measures are
  identical to the uninterrupted run for every policy. In-process counters are lost, which is
  why measures are reconstructed from the durable audit table.
- `failpoint_test.txt` — the cloud process terminated *inside* the window between writing an
  audit record and committing the state transaction. Zero orphaned decisions: no audit record
  survived without its corresponding state change.

Reproduce with:

```bash
python run_experiment.py                    # fault injection
python run_experiment.py --crash-at 0.5     # crash and restart
python cloud_server.py --failpoint before_commit --failpoint-at 3 ...
```

## Reproducibility

All seeds derive from a fixed master seed. Reproduction is exact at the precision reported
in the paper rather than bit-for-bit: the timestamp-dependent policies compare wall-clock
values, so two writes landing in the same millisecond may be ordered differently between
executions. This affects a small number of runs and no reported mean at the precision shown.
