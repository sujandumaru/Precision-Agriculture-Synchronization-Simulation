# Running the full 729-cell sweep

## Three rules

1. **The output folder must NOT be on OneDrive.** SQLite cannot open its journal on a
   synced folder and the server fails with `cloud failed to start`. Code on OneDrive is
   fine; output must be plain local disk. Use `C:\proto_run`.
2. **Wait for it to actually finish.** A partial sweep is not a smaller sweep. Cells are
   enumerated in factor order, so stopping early collapses `fleet_size`, `connectivity`,
   and `sync_interval` to their first level and hands you the easiest corner of the
   design. Any agreement figure from that is meaningless.
3. **Verify the row count before running the analysis.** It must be 145,800.

## Check Python

```powershell
python --version
```

3.10 or newer. Nothing to install.

## IMPORTANT: v3 changes the experiment

The synchronization semantics changed, so **results from the previous sweep are not
comparable and must not be mixed with new ones.** Write to a fresh directory.

What changed:

- Clients now poll for cloud changes even with nothing pending. Previously a client with
  no local edits never pulled, and the test harness copied cloud state into clients before
  checking convergence — which made the convergence invariant true by construction. It now
  fails when the protocol is broken and passes when it is not.
- Audit records commit inside the same transaction as canonical state, so a crash cannot
  leave a decision recorded but unapplied.
- Invariants I1–I5 are evaluated for **every run in the sweep**, not only in the
  single-configuration runner. They appear as columns `I1`…`I5` in the output.
- Three additional version-vector tiebreak variants were added, so the result can be shown
  to be invariant to the tiebreak rather than an artifact of one arbitrary rule.
- `TCP_NODELAY` is set on both ends. Without it every request paid a ~41 ms Nagle /
  delayed-ACK penalty; requests now cost ~0.36 ms.

## Run it (auto-retrying, recommended)

Ten policies, 729 cells, 20 replications = **145,800 runs**. Budget about **3.5 hours**
on four cores. Pull-only synchronization means far more requests per run than v2.

```powershell
cd "$env:USERPROFILE\OneDrive\Documents\EB2-NIW\prototype"
mkdir C:\proto_v3 -Force
$target = 145800

for ($pass = 1; $pass -le 40; $pass++) {
  $n = (Get-ChildItem C:\proto_v3\prototype_runs_s*.csv -ErrorAction SilentlyContinue |
        ForEach-Object { (Get-Content $_).Count - 1 } | Measure-Object -Sum).Sum
  if (-not $n) { $n = 0 }
  if ($n -ge $target) { Write-Host "COMPLETE: $n runs"; break }
  Write-Host "pass $pass -- at $n / $target"

  $procs = 0..3 | ForEach-Object {
    Start-Process python -ArgumentList @(
      "sweep.py","--cells","all","--reps","20","--out","C:\proto_v3",
      "--port",(8801 + $_),"--shard",$_,"--nshards","4","--resume","--new-baselines"
    ) -NoNewWindow -PassThru `
      -RedirectStandardOutput "C:\proto_v3\shard$_.out.txt" `
      -RedirectStandardError  "C:\proto_v3\shard$_.err.txt"
  }
  $procs | Wait-Process
}
```

Each shard prints live progress with a rate and ETA. A pass takes hours, not seconds;
watch the row count from a second window rather than assuming it has stalled.

## Check the count

```powershell
$n = (Get-ChildItem C:\proto_run\prototype_runs_s*.csv |
      ForEach-Object { (Get-Content $_).Count - 1 } | Measure-Object -Sum).Sum
Write-Host "$n of 145800 runs  ($([math]::Round(100*$n/145800,1))%)"
```

## Analysis

```powershell
python agreement.py --prototype C:\proto_run --simulation ..\simulation\results\raw_runs.csv |
  Out-File -Encoding utf8 C:\proto_run\agreement_full.txt
```

Use `Out-File -Encoding utf8`, not `>`. PowerShell's `>` writes UTF-16 and the file
arrives full of null bytes.

`agreement.py` now refuses to report on an incomplete sweep and tells you which factors
collapsed. If you see the `INCOMPLETE SWEEP` banner, the sweep is not done — go back and
re-run. Do not use `--force` for anything you intend to publish.

## What to send back

- `C:\proto_run\agreement_full.txt`
- the shard CSVs if they are a manageable size

Not `C:\proto_run\server_s*\` — those are working SQLite files.

## Optional extras, once the main sweep is verified

Add the two causal baselines (7 policies, ~40% longer):

```powershell
python sweep.py --cells all --reps 20 --out C:\proto_run_baselines --port 8850 --new-baselines --resume
```

Measure what the simulation's abstraction omits, by enabling the fault layer:

```powershell
python sweep.py --cells all --reps 5 --out C:\proto_run_faults --port 8860 --faults --resume
```

The faults run is not an agreement run. The simulation models no packet loss,
duplication, or clock skew, so the gap is the quantity of interest, not a discrepancy.
