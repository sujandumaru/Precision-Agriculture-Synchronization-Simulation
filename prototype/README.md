# Domain-Aware Synchronization Prototype (v0.1)

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
python3 run_experiment.py                    # clean run, all 7 policies
python3 run_experiment.py --crash-at 0.5     # SIGKILL the cloud at 50% of the run
```

## Policies

| # | Policy | Origin |
|---|--------|--------|
| 1 | `last_write_wins` | in the paper |
| 2 | `cloud_preferred` | in the paper |
| 3 | `display_preferred` | in the paper |
| 4 | `manual_review_all` | in the paper |
| 5 | `domain_aware` | in the paper (proposed) |
| 6 | `version_vector_causal` | **new baseline** |
| 7 | `crdt_field_merge` | **new baseline** |

Policies 6 and 7 exist because the paper cites Shapiro et al. (CRDTs) and Terry et al.
(Bayou) in related work but benchmarks only against timestamp and authority rules. A
distributed-systems reviewer will ask why. These are the answer.

## Result that matters

Full design: 729 cells x 20 replications x 7 policies = **102,060 runs**, every
(cell, replication) evaluated under all seven policies on the identical workload.

| Policy | high-risk silent overwrites | total silent overwrites | manual reviews |
|---|---|---|---|
| `last_write_wins` | 3.1009 | 8.8562 | 0.0000 |
| `cloud_preferred` | 3.1014 | 8.8566 | 0.0000 |
| `display_preferred` | 3.1001 | 8.8547 | 0.0000 |
| `version_vector_causal` | **3.1002** | 8.8546 | 0.0000 |
| `crdt_field_merge` | **1.4715** | **3.9416** | 0.0000 |
| `manual_review_all` | 0.0000 | 0.0000 | 8.3219 |
| `domain_aware` | **0.0000** | 5.7549 | **2.8924** |

### Causal consistency does not protect high-integrity records

`version_vector_causal` against `last_write_wins`, paired across 14,580 runs:

- mean difference: **-0.000686**
- **99.7% of runs produce identical outcomes**

Version vectors detect concurrency correctly and remove the wall-clock dependence, and
make essentially no difference to how many high-integrity records are lost. Detecting a
conflict is not the same as knowing it matters.

### The strongest baseline halves the loss but does not close it

`crdt_field_merge` cuts high-risk loss by **52.5%** relative to last-write-wins and total
loss by 55%, because disjoint field edits survive. It still discards 1.47 high-integrity
records per run whenever two replicas write the same field. Only `manual_review_all` and
`domain_aware` reach zero, and `domain_aware` gets there at **2.89 reviews per run against
8.32 — a 65.2% reduction**.

### An honest complication worth reporting

`crdt_field_merge` has *lower total* silent overwrites than `domain_aware` (3.94 against
5.75). The domain-aware policy is not uniformly better; it is better at protecting the
records that matter, and worse on aggregate volume, because it resolves lower-integrity
conflicts automatically by design.

The two are therefore complementary rather than competing, which suggests a policy this
study has not evaluated: field-level merge for low- and medium-integrity entities, review
routing for high-integrity ones. That combination would plausibly dominate both. It is
the obvious next experiment and it should be named in the paper before a reviewer names
it first.

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
| `policies.py` | the seven conflict-resolution policies |
| `cloud_server.py` | canonical store, conflict detector, review queue, delta sync |
| `display_client.py` | local store, change log, coalescing, periodic sync |
| `transport.py` | fault injection: drop, duplicate, reorder, partition |
| `workload.py` | deterministic workload + two-state connectivity, matching the study's factors |
| `invariants.py` | safety invariants and audit-derived metrics |
| `run_experiment.py` | orchestrates one workload across all policies |

## Status and honest limits

This is **v0.1**. It demonstrates the architecture end to end and produces the
comparison above. It is not yet:

- calibrated against measured rural connectivity traces (parameters are still synthetic)
- exercised over ISOXML/ADAPT-shaped records (entities are 5 generic fields)
- swept across the full 729-cell design (it runs one configuration at a time)
- model-checked (the I1–I5 invariants are runtime assertions, not TLA+ proofs)

Nothing here uses proprietary code, internal logs, or non-public technical material.

## Sweep and simulation agreement

`sweep.py` replays the study's factorial design (3^6 = 729 cells, identical factor
levels to `simulation/config.json`) through the running prototype. `agreement.py`
then compares the result against `simulation/results/raw_runs.csv` on the same cells.

```bash
python3 sweep.py --cells corners --reps 1 --out /tmp/sweep_out
python3 agreement.py --simulation ../simulation/results/raw_runs.csv
```

**Do not point `--out` at a OneDrive, Dropbox, or other synced folder.** SQLite cannot
open its journal there and the server will fail to start. Use a local disk path.

Throughput is roughly 0.1 s per run with a persistent server, so the full design
(729 cells x 20 replications x 5 policies = 72,900 runs) is about two hours. The full
sweep is tractable; it just should not be run in a foreground session.

### Result on the 65 corner cells

| Policy | metric | simulation | prototype | diff |
|---|---|---|---|---|
| `last_write_wins` | conflicts | 3.785 | 13.523 | +9.738 |
| `last_write_wins` | high-risk silent overwrites | 1.399 | 5.046 | +3.647 |
| `domain_aware` | high-risk silent overwrites | 0.000 | 0.000 | 0.000 |
| `domain_aware` | manual reviews | 1.388 | 4.600 | +3.212 |
| `manual_review_all` | manual reviews | 3.757 | 12.523 | +8.766 |

Manual-review reduction versus `manual_review_all`: **63.1% simulated, 63.3% measured
— a 0.2 point gap.** All three of the paper's load-bearing claims agree between model
and implementation.

(63% rather than the paper's 64.9% because these are the 65 corner cells, not the full
equally-weighted 729-cell design.)

### Full-design result (729 cells, 72,900 runs)

| Policy | metric | simulation | prototype | diff |
|---|---|---|---|---|
| `last_write_wins` | conflicts | 2.197 | 8.856 | +6.659 |
| `last_write_wins` | high-risk silent overwrites | 0.772 | 3.101 | +2.329 |
| `manual_review_all` | manual reviews | 2.186 | 8.322 | +6.136 |
| `domain_aware` | high-risk silent overwrites | 0.000 | 0.000 | 0.000 |
| `domain_aware` | manual reviews | 0.768 | 2.892 | +2.124 |

Manual-review reduction versus `manual_review_all`: **64.9% simulated, 65.2% measured
— a 0.4 point gap.** The simulated figure is the number reported in the manuscript, so
the published headline claim reproduces in a running implementation to within half a
point. All three load-bearing claims agree.

### The discrepancy, and a hypothesis that did not survive

Absolute conflict rates are about 4x higher in the prototype. The first explanation
proposed was conflict cascades: cloud-side rejections increment the cloud's version
vector, producing a version other displays have not seen, which then conflicts with the
next display to sync. That predicts the prototype/simulation ratio should *rise* with
fleet size, since more replicas mean more to cascade to.

It was tested. It is wrong.

| factor | ratio at low level | ratio at high level |
|---|---|---|
| `fleet_size` 2 → 10 | 5.34x | 3.93x |
| `sync_interval` 1 → 15 | 5.18x | 3.25x |
| `connectivity` good → poor | 8.04x | 3.01x |

The ratio *falls* with fleet size, and the excess is largest where connectivity is best,
syncing most frequent, and the fleet smallest — precisely where contention is lowest.
Cascades cannot produce that.

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
