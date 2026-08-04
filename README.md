# Domain-Aware Conflict Resolution for Precision-Agriculture Setup Data

Research artifact for a study of how embedded machine displays and cloud
farm-management platforms should resolve conflicting setup-data updates under
intermittent connectivity.

The repository contains two independent evaluations of the same reference
architecture.

| Directory | What it is |
|---|---|
| [`prototype/`](prototype/) | An executable implementation: separate operating-system processes, HTTP over real sockets, durable storage, version-vector concurrency detection, fault injection, and crash recovery. **Ten** conflict-resolution policies across a full factorial design, **145,800 runs**. This is the experiment the manuscript's policy tables report. |
| [`simulation/`](simulation/) | A discrete-event model of the same architecture, evaluating the **five** originally published policies over the same 729-cell design, **72,900 runs**. Used as an independent cross-check on the policy comparison. |

The two run counts differ because the policy counts differ, not because the
designs differ. Both sweep the same 729 cells with 20 replications.

Python 3.10+ standard library only for the prototype. The simulation needs
Matplotlib for figure generation.

## The result, and how to read it

Across 729 parameter cells x 20 replications x 10 policies = **145,800 paired
runs**, every (cell, replication) evaluated under all ten policies against an
identical workload:

- Causal detection followed by any of four tiebreak rules — node order, cloud
  authority, display authority, pseudo-random — discarded **1.8472** high-integrity
  records per run, the same as naive last-write-wins.
- A field-wise last-write-wins register merge halved that to **0.9134** without
  eliminating it.
- Only `manual_review_all` and `domain_aware` reached zero, the latter at
  **1.7509** reviews per run against 5.0464.

**Read the caveat in [`prototype/README.md`](prototype/README.md) before quoting
these numbers.** For any policy that resolves a conflict by selecting a winner,
exactly one branch is discarded per conflict, so high-integrity loss equals the
high-integrity *conflict rate* by construction — this holds in 14,580 of 14,580
runs. The shared 1.8472 is that conflict rate relabelled, not a measurement of
tiebreak quality. The 65.3% review reduction is correspondingly the fraction of
conflicts that are not high-integrity, and it ranges from 91.7% to 35.3% as the
high-integrity update share moves from 15% to 50%.

The one genuinely non-tautological policy result is the register merge, which
changes the discard-per-conflict ratio rather than inheriting it.

## Reproducing

```bash
cd prototype
python preflight.py                  # nine checks; must print PRE-FLIGHT PASSED
python sweep.py --cells all --reps 20 --out <local-dir> --new-baselines --resume
python baselines.py  --prototype <local-dir>
python agreement.py  --prototype <local-dir> --simulation ../simulation/results/raw_runs.csv
```

Full instructions in [`prototype/REPRODUCE.md`](prototype/REPRODUCE.md) and
[`prototype/RUN_SWEEP.md`](prototype/RUN_SWEEP.md). The output directory must be
on local disk — SQLite cannot open its journal on a synced folder.

Archived run-level results for every reported figure are in
[`prototype/results/`](prototype/results/), from a single clean execution.

## Scope

The evaluation uses synthetic parameter ranges, not measured field traces. The
entity integrity classes are analytical assignments derived from public
documentation, not a validated survey of operators or agronomists. Nothing here
derives from proprietary source code, internal message formats, or vendor
synchronization logs.

## License

Apache License 2.0. See [LICENSE](LICENSE).
