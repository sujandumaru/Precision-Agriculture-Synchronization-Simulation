#!/usr/bin/env python3
"""Replay the study's factorial design through the real prototype.

The full design is 3^6 = 729 cells. Running every cell x 20 replications x 7
policies through real processes is not tractable, and is not what validation
requires. What matters is whether the discrete-event model and the running
implementation agree. So this samples cells, replays them through the
prototype, and `agreement.py` measures the gap against simulation/results.

Faults default to OFF: the simulation models no packet loss, duplication, or
clock skew, so an agreement run must not either. Use --faults to measure what
the simulation's abstraction leaves out.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.request

from display_client import DisplayClient
from invariants import check
from run_experiment import start_cloud, wait_up
from transport import FaultyTransport, Partitioned
from workload import build_entities, generate_plan

HERE = os.path.dirname(os.path.abspath(__file__))

# factor levels copied verbatim from simulation/config.json
FACTORS = {
    "fleet_size": [2, 5, 10],
    "connectivity": [("good", 0.90, 5), ("moderate", 0.70, 20), ("poor", 0.50, 60)],
    "sync_interval_minutes": [1, 5, 15],
    "updates_per_display_day": [2, 8, 20],
    "conflict_bias": [0.01, 0.05, 0.10],
    "high_risk_update_share": [0.15, 0.30, 0.50],
}
BASE = {"ticks": 480, "entity_count": 100,
        "entity_class_mix": {"high": 0.30, "medium": 0.40, "low": 0.30},
        "cloud_update_ratio": 0.40}
SIM_POLICIES = ["last_write_wins", "cloud_preferred", "display_preferred",
                "manual_review_all", "domain_aware"]
NEW_POLICIES = ["version_vector_causal", "version_vector_cloud_wins",
                "version_vector_display_wins", "version_vector_random",
                "crdt_field_merge"]


def full_grid() -> list[dict]:
    keys = list(FACTORS)
    out = []
    for combo in itertools.product(*(FACTORS[k] for k in keys)):
        cell = dict(zip(keys, combo))
        conn = cell.pop("connectivity")
        cell["connectivity"] = conn[0]
        cell["online_probability"] = conn[1]
        cell["mean_outage_ticks"] = conn[2]
        out.append(cell)
    return out


def select_cells(mode: str, rng: random.Random) -> list[dict]:
    grid = full_grid()
    if mode == "all":
        return grid
    if mode == "corners":
        # every factor at an extreme level, plus the centre cell
        keys = [k for k in FACTORS if k != "connectivity"]
        sel = [c for c in grid
               if c["connectivity"] in ("good", "poor")
               and all(c[k] in (FACTORS[k][0], FACTORS[k][-1]) for k in keys)]
        centre = [c for c in grid
                  if c["connectivity"] == "moderate"
                  and all(c[k] == FACTORS[k][1] for k in keys)]
        return sel + centre
    if mode.startswith("sample:"):
        n = int(mode.split(":")[1])
        return rng.sample(grid, min(n, len(grid)))
    raise ValueError(f"unknown cell mode: {mode}")


def run_cell(url: str, cell: dict, policy: str, rep: int, seed: int,
             faults: bool, ctl: FaultyTransport, check_invariants: bool = True) -> dict:
    cfg = {**BASE, **cell}
    entities = build_entities(cfg["entity_count"], cfg["entity_class_mix"])
    plan = generate_plan(random.Random(seed), cfg, entities)

    ctl.post("/reset", {"policy": policy})
    ctl.post("/seed", {"entities": entities})

    fp = dict(drop=0.05, dup=0.05, reorder=0.10) if faults else dict(drop=0.0, dup=0.0, reorder=0.0)
    clients = []
    for i in range(cfg["fleet_size"]):
        tx = FaultyTransport(url, random.Random(seed + i), **fp)
        c = DisplayClient(f"d{i}", tx, clock_skew_ms=(-120000 if (faults and i == 0) else 0))
        c.seed(entities)
        clients.append(c)
    cloud_writer = DisplayClient("cloud_admin", FaultyTransport(url, random.Random(seed + 999)))
    cloud_writer.seed(entities)

    de, ce, conn = plan["display_events"], plan["cloud_events"], plan["connectivity"]
    d_i = c_i = 0
    for tick in range(cfg["ticks"]):
        while d_i < len(de) and de[d_i]["tick"] == tick:
            e = de[d_i]
            clients[e["node"]].edit(e["entity_id"], e["cls"], e["field"], e["value"])
            d_i += 1
        while c_i < len(ce) and ce[c_i]["tick"] == tick:
            e = ce[c_i]
            cloud_writer.edit(e["entity_id"], e["cls"], e["field"], e["value"])
            try:
                cloud_writer.sync()
            except Partitioned:
                pass
            c_i += 1
        if tick % cfg["sync_interval_minutes"] == 0:
            for i, c in enumerate(clients):
                c.tx.partitioned = not conn[i][tick]
                c.sync()

    for c in clients:
        c.tx.partitioned = False
    for _ in range(3):
        for c in clients:
            c.sync()

    m = ctl.get("/metrics")
    inv_summary = {}
    if check_invariants:
        state = ctl.get("/state")
        queue = ctl.get("/review_queue")
        audit = ctl.get("/audit")
        results = check(audit, state, [c.content_hashes() for c in clients], policy, queue)
        for item in results:
            inv_summary[item["invariant"].split("_")[0]] = int(item["passed"])
    for c in clients:
        c.tx.close()
    cloud_writer.tx.close()
    row = {"policy": policy, "replication": rep, "faults": int(faults), **cell}
    for k in ("server_errors", "conflicts", "high_risk_conflicts", "silent_overwrites",
              "high_risk_silent_overwrites", "manual_reviews",
              "high_risk_manual_reviews", "merges", "accepted",
              "duplicates_suppressed", "stale_rejected", "sync_requests",
              "bytes_in", "bytes_out"):
        row[k] = m.get(k, 0)
    row["events_total"] = len(de) + len(ce)
    row.update(inv_summary)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", default="corners", help="all | corners | sample:N")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--faults", action="store_true")
    ap.add_argument("--new-baselines", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "sweep_out"))
    ap.add_argument("--port", type=int, default=8901)
    ap.add_argument("--budget", type=float, default=0, help="stop after N seconds")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--no-invariants", action="store_true",
                    help="skip per-run invariant evaluation (faster)")
    ap.add_argument("--resume", action="store_true",
                    help="skip (cell,rep,policy) triples already present in the output")
    a = ap.parse_args()

    policies = SIM_POLICIES + (NEW_POLICIES if a.new_baselines else [])
    all_cells = select_cells(a.cells, random.Random(a.seed))
    cells = [(i, c) for i, c in enumerate(all_cells) if i % a.nshards == a.shard]
    os.makedirs(a.out, exist_ok=True)

    path = os.path.join(a.out, f"prototype_runs_s{a.shard}.csv")
    done: set[tuple] = set()
    if a.resume and os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                done.add((int(r["cell_index"]), int(r["replication"]), r["policy"]))
        print(f"resume: {len(done)} runs already complete in shard {a.shard}", flush=True)
    rundir = os.path.join(a.out, f"server_s{a.shard}")  # per-shard: shards must not share a DB
    shutil.rmtree(rundir, ignore_errors=True)
    os.makedirs(rundir, exist_ok=True)

    proc = start_cloud(a.port, rundir, policies[0], fast=True)
    url = f"http://127.0.0.1:{a.port}"
    if not wait_up(url):
        proc.kill()
        sys.exit("cloud failed to start")

    ctl = FaultyTransport(url, random.Random(0))
    t0, stopped, n_new, n_err = time.time(), False, 0, 0
    total = len(cells) * a.reps * len(policies)
    print(f"shard {a.shard}/{a.nshards}: cells={len(cells)} reps={a.reps} "
          f"policies={len(policies)} runs={total} faults={'on' if a.faults else 'off'}",
          flush=True)

    fresh = not os.path.exists(path)
    fh = open(path, "a", newline="")
    writer = None
    try:
        for ci, cell in cells:
            for rep in range(a.reps):
                seed = a.seed * 100003 + ci * 997 + rep
                for policy in policies:
                    if (ci, rep, policy) in done:
                        continue
                    try:
                        row = {"cell_index": ci,
                               **run_cell(url, cell, policy, rep, seed, a.faults, ctl,
                                          not a.no_invariants)}
                    except Exception as exc:
                        n_err += 1
                        print(f"  ERROR cell={ci} rep={rep} policy={policy}: "
                              f"{type(exc).__name__}: {exc}", flush=True)
                        ctl.close()
                        if n_err > 200:
                            print("  too many errors, stopping shard", flush=True)
                            stopped = True
                            break
                        continue
                    if writer is None:
                        writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
                        if fresh:
                            writer.writeheader()
                    writer.writerow(row)
                    n_new += 1
                    fh.flush()          # every row, so progress is externally visible
                    if n_new % 25 == 0:
                        el = time.time() - t0
                        rate = n_new / max(el, 1e-9)
                        remaining = max(0, total - len(done) - n_new)
                        eta = remaining / max(rate, 1e-9) / 60
                        print(f"  shard {a.shard}: cell {ci}  {n_new} runs  "
                              f"{el:.0f}s  {rate:.1f} runs/s  ETA {eta:.0f}m",
                              flush=True)
                    if a.budget and time.time() - t0 > a.budget:
                        stopped = True
                        break
                if stopped:
                    break
            if stopped:
                break
    finally:
        fh.flush()
        fh.close()
        ctl.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    el = time.time() - t0
    print(f"shard {a.shard}: +{n_new} runs in {el:.0f}s "
          f"({el/max(1,n_new):.2f}s/run)  errors={n_err}"
          f"{'  [stopped early]' if stopped else '  [SHARD COMPLETE]'}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
