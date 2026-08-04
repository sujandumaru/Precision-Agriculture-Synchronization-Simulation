# Domain-Aware Synchronization Prototype

A clean-room, executable implementation of the reference architecture described in
*Domain-Aware Synchronization of Precision-Agriculture Setup Data Between Embedded
Displays and Cloud Farm-Management Platforms*.

Written to answer one editorial objection: that the study is "conceptual only."
The architecture in the paper is no longer a diagram. It runs.

## What makes this a prototype and not a second simulation

The existing `simulation/` study is a single-process discrete-event model. This is not.

- The cloud is a **separate OS process** serving HTTP over a real socket.
- State is in **SQLite with WAL and `synchronous=FULL`**; every decision is committed
  and the audit record `fsync`ed *before* the client is acknowledged.
- Faults are **real**: requests are genuinely dropped, duplicated, reordered and
  delayed at the transport boundary, and the cloud is killed with `SIGKILL`.
- Crash recovery is genuine process death and restart, not a flag.

## No dependencies

Python 3.10+ standard library only. This is deliberate: a reviewer can reproduce it
with no install step.

```bash
python3 run_experiment.py                    # clean run, all ten policies
python3 run_experiment.py --crash-at 0.5     # SIGKILL the cloud at 50% of the run
```

## Policies

Ten, defined in `policies.py` as `POLICIES`. Every script that reports on a sweep
validates its own list against that tuple and refuses to run if the two disagree.

| # | Policy | Origin |
|---|--------|--------|
| 1 | `last_write_wins` | in the paper |
| 2 | `cloud_preferred` | in the paper |
| 3 | `display_preferred` | in the paper |
| 4 | `manual_review_all` | in the paper |
| 5 | `domain_aware` | in the paper (proposed) |
| 6 | `version_vector_causal` | **new baseline** |
| 7 | `version_vector_cloud_wins` | **new baseline**, tiebreak variant |
| 8 | `version_vector_display_wins` | **new baseline**, tiebreak variant |
| 9 | `version_vector_random` | **new baseline**, tiebreak variant |
| 10 | `crdt_field_merge` | **new baseline** |

The causal variants exist because the paper cites Parker et al. (version vectors) and
Shapiro et al. (CRDTs) in related work but would otherwise benchmark only against
timestamp and authority rules. Causal detection must be followed by *some* tiebreak,
so policies 6 to 9 apply four different ones — node order, cloud authority, display
authority, pseudo-random — to show the result is invariant to that choice rather than
an artifact of one arbitrary rule. `crdt_field_merge` is a field-wise last-write-wins
register merge inspired by CRDT designs; its convergence algebra was not formally
established here, so it is not claimed to be a general conflict-free replicated data type.

## Result that matters

Full design: 729 cells x 20 replications x 10 policies = **145,800 paired runs**, every
(cell, replication) evaluated under all ten policies on an identical workload.

| Policy | high-integrity discarded | total silent overwrites | manual reviews |
|---|---|---|---|
| `last_write_wins` | 1.8472 | 5.2919 | 0.0000 |
| `cloud_preferred` | 1.8472 | 5.2919 | 0.0000 |
| `display_preferred` | 1.8472 | 5.2920 | 0.0000 |
| `version_vector_causal` | 1.8472 | 5.2920 | 0.0000 |
| `version_vector_cloud_wins` | 1.8472 | 5.2919 | 0.0000 |
| `version_vector_display_wins` | 1.8472 | 5.2919 | 0.0000 |
| `version_vector_random` | 1.8472 | 5.2919 | 0.0000 |
| `crdt_field_merge` | **0.9134** | **2.4477** | 0.0000 |
| `manual_review_all` | 0.0000 | 0.0000 | 5.0464 |
| `domain_aware` | **0.0000** | 3.4447 | **1.7509** |

### Read this before quoting the numbers

**The high-integrity loss figure is an identity, not a measurement.** For every policy
that resolves each conflict by picking a winner, exactly one branch is discarded per
conflict, so `high_risk_silent_overwrites == high_risk_conflicts` in 14,580 of 14,580
runs. The 1.8472 shared by seven policies is therefore the high-integrity *conflict rate*
under this workload, relabelled. It is not evidence that one tiebreak beats another; no
tiebreak can. The four tiebreak variants exist to confirm the implementation contains no
accidental content sensitivity, which is a sanity check rather than a finding.

Likewise `manual_reviews == conflicts` for `manual_review_all` and
`manual_reviews == high_risk_conflicts` for `domain_aware`, in all 14,580 runs each. The
65.3% review reduction is consequently the fraction of conflicts that are *not*
high-integrity, which is set by the entity mix.

**The 65.3% is an average over a parameter we chose the levels of.** It ranges from 91.7%
at a 15% high-integrity update share to 35.3% at 50%. Across every other factor it is
essentially flat (connectivity 64.2-65.8%, fleet size 64.6-65.5%, conflict bias
65.1-65.5%, sync interval 65.1-65.6%). Quote the range, not the average alone.

The one genuinely non-tautological policy result is `crdt_field_merge`: because field-wise
merge changes the discard-per-conflict ratio rather than inheriting it, its 0.9134 is not
fixed by the conflict rate. It halves high-integrity loss (-50.6%) without eliminating it.

## Invariants

Checked against the durable audit log after every run (`invariants.py`):

- **I1** under `domain_aware`, no high-integrity concurrent update is ever auto-resolved
- **I2** every conflict retains *both* branches in the audit record before acknowledgement
- **I3** replicas converge once connectivity returns
- **I4** duplicate delivery is idempotent
- **I5** every queued conflict is persisted in the review queue

## Crash recovery

`--crash-at 0.5` sends `SIGKILL` to the cloud mid-run and restarts it. Audit-derived
metrics come back **identical to the clean run**, and all invariants still pass. In-process
counters are lost — which is why metrics are reconstructed from the log, not from memory.
That the two agree exactly on a clean run is itself a check that the log is complete.

## Files

| File | Role |
|---|---|
| `common.py` | version vectors, content hashing, `fsync`ed audit log, SQLite helpers |
| `policies.py` | the ten conflict-resolution policies, declared once as `POLICIES` |
| `cloud_server.py` | canonical store, conflict detector, review queue, delta sync |
| `display_client.py` | local store, change log, coalescing, periodic sync |
| `transport.py` | fault injection: drop, duplicate, reorder, partition |
| `workload.py` | deterministic workload + two-state connectivity, matching the study's factors |
| `invariants.py` | safety invariants and audit-derived metrics |
| `run_experiment.py` | orchestrates one workload across all policies |

## Status and honest limits

It demonstrates the architecture end to end and produces the comparison above
across the full 729-cell design. It is not yet:

- calibrated against measured rural connectivity traces (parameters are still synthetic)
- exercised over ISOXML/ADAPT-shaped records (entities are 5 generic fields)
- model-checked (the I1–I5 invariants are runtime assertions, not TLA+ proofs)

Nothing here uses proprietary code, internal logs, or non-public technical material.

## Sweep and simulation agreement

`sweep.py` replays the study's factorial design (3^6 = 729 cells, identical factor
levels to `simulation/config.json`) through the running prototype. `agreement.py`
then compares the result against `simulation/results/raw_runs.csv` on the same cells.

```bash
python3 sweep.py --cells corners --reps 1 --out /tmp/sweep_out
python3 agreement.py --prototype /tmp/sweep_out --simulation ../simulation/results/raw_runs.csv
```

**Do not point `--out` at a OneDrive, Dropbox, or other synced folder.** SQLite cannot
open its journal there and the server will fail to start. Use a local disk path.

Throughput is roughly 0.1 s per run with a persistent server, so the full design
(729 cells x 20 replications x 10 policies = 145,800 runs) is roughly four hours. The full
sweep is tractable; it just should not be run in a foreground session.

### Cross-implementation comparison (prototype vs discrete-event model)

| Policy | metric | simulation | prototype |
|---|---|---|---|
| `last_write_wins` | conflicts | 2.197 | 5.292 |
| `last_write_wins` | high-integrity discarded | 0.772 | 1.847 |
| `manual_review_all` | manual reviews | 2.186 | 5.046 |
| `domain_aware` | high-integrity discarded | 0.000 | 0.000 |
| `domain_aware` | manual reviews | 0.768 | 1.751 |

Manual-review reduction: **64.9% simulated, 65.3% measured — a 0.4 point gap.** All three
of the paper's load-bearing claims agree between model and implementation.

### The discrepancy, and a hypothesis that did not survive

Absolute conflict rates are **2.41x** higher in the prototype. The first explanation
proposed was conflict cascades: cloud-side rejections increment the cloud's version
vector, producing a version other displays have not seen, which then conflicts with the
next display to sync. That predicts the prototype/simulation ratio should *rise* with
fleet size, since more replicas mean more to cascade to.

It was tested. It is wrong.

| factor | ratio at low level | ratio at high level |
|---|---|---|
| `fleet_size` 2 → 10 | 3.47x | 2.33x |
| `sync_interval_minutes` 1 → 15 | 2.51x | 2.33x |
| `connectivity` good → poor | 2.81x | 2.33x |
| `updates_per_display_day` 2 → 20 | 3.37x | 2.34x |

The ratio *falls* with fleet size, and the excess is largest where connectivity is best,
syncing most frequent, update volume lowest and the fleet smallest — precisely where
contention is lowest. Cascades cannot produce that.

These ratios come from the archived runs in `results/`. Regenerate the full table,
including the two factors the conflict rate is flat against, with:

```bash
python agreement.py --prototype results/ --simulation ../simulation/results/raw_runs.csv
```

The pattern instead points at a detection-semantics difference. In the simulation, a
cloud-side write lands directly in `cloud_content` and `refresh_stale_for_entity`
immediately re-aligns every display holding no pending update for that entity. Displays
therefore absorb cloud changes between synchronisation points without ever registering a
conflict. In the prototype a display learns of a cloud change only at its next sync, so
an edit made in the interval is a genuine concurrent write. That would make the excess
largest when syncs are frequent and connectivity good, which is what the data shows.

**This second explanation is not yet verified.** It should be tested before it appears in
the manuscript. The check: instrument the prototype to classify each detected conflict by
whether the simulation's scalar `base != current` test would also have fired, and confirm
the residual concentrates in the low-contention cells.

What is already established is narrower and safer to state: the two systems disagree on
absolute conflict *rates* while agreeing to 0.4 points on the *policy comparison* the
paper argues.
