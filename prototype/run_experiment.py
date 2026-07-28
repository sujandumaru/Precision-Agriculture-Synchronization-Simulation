#!/usr/bin/env python3
"""Replay one workload under every policy against the real cloud process."""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

from common import AuditLog, content_hash, now_ms
from display_client import DisplayClient
from invariants import check, metrics_from_audit
from policies import POLICIES
from transport import FaultyTransport, Partitioned
from workload import build_entities, generate_plan

HERE = os.path.dirname(os.path.abspath(__file__))


def wait_up(url: str, timeout=15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.05)
    return False


def start_cloud(port: int, rundir: str, policy: str, fast: bool = False) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, os.path.join(HERE, "cloud_server.py"),
         "--port", str(port), "--policy", policy,
         "--db", f"{rundir}/cloud.db", "--audit", f"{rundir}/audit.jsonl"]
        + (["--fast"] if fast else []),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HERE)


def run_policy(policy: str, cfg: dict, plan: dict, entities: list[dict],
               port: int, outdir: str, crash_at: float | None = None) -> dict:
    rundir = os.path.join(outdir, policy)
    shutil.rmtree(rundir, ignore_errors=True)
    os.makedirs(rundir, exist_ok=True)

    proc = start_cloud(port, rundir, policy)
    url = f"http://127.0.0.1:{port}"
    if not wait_up(url):
        proc.kill()
        raise RuntimeError(f"cloud did not start for {policy}")

    urllib.request.urlopen(urllib.request.Request(
        url + "/seed", data=json.dumps({"entities": entities}).encode(),
        headers={"Content-Type": "application/json"}), timeout=10).read()

    rng = random.Random(cfg["seed"] + 777)
    clients = []
    for i in range(cfg["fleet_size"]):
        tx = FaultyTransport(url, random.Random(cfg["seed"] + i), drop=cfg["fault_drop"],
                             dup=cfg["fault_dup"], reorder=cfg["fault_reorder"])
        # one display carries a deliberately skewed clock
        skew = cfg["clock_skew_ms"] if i == 0 else 0
        c = DisplayClient(f"d{i}", tx, clock_skew_ms=skew)
        c.seed(entities)
        clients.append(c)

    cloud_tx = FaultyTransport(url, random.Random(cfg["seed"] + 999))
    cloud_writer = DisplayClient("cloud_admin", cloud_tx)
    cloud_writer.seed(entities)

    d_idx = c_idx = 0
    de, ce = plan["display_events"], plan["cloud_events"]
    conn = plan["connectivity"]
    crashed = False
    crash_tick = int(crash_at * cfg["ticks"]) if crash_at else None

    for tick in range(cfg["ticks"]):
        while d_idx < len(de) and de[d_idx]["tick"] == tick:
            e = de[d_idx]
            clients[e["node"]].edit(e["entity_id"], e["cls"], e["field"], e["value"])
            d_idx += 1
        while c_idx < len(ce) and ce[c_idx]["tick"] == tick:
            e = ce[c_idx]
            cloud_writer.edit(e["entity_id"], e["cls"], e["field"], e["value"])
            try:
                cloud_writer.sync()
            except Partitioned:
                pass
            c_idx += 1

        if crash_tick is not None and tick == crash_tick and not crashed:
            proc.send_signal(signal.SIGKILL)   # hard crash, no clean shutdown
            proc.wait()
            time.sleep(0.2)
            proc = start_cloud(port, rundir, policy)
            wait_up(url)
            crashed = True

        if tick % cfg["sync_interval_ticks"] == 0:
            for i, c in enumerate(clients):
                c.tx.partitioned = not conn[i][tick]
                c.sync()

    # heal the network and drain everything
    for c in clients:
        c.tx.partitioned = False
    for _ in range(6):          # drain: pull-only sync must achieve convergence itself
        for c in clients:
            c.sync()

    metrics = json.loads(urllib.request.urlopen(url + "/metrics", timeout=10).read())
    state = json.loads(urllib.request.urlopen(url + "/state", timeout=10).read())
    queue = json.loads(urllib.request.urlopen(url + "/review_queue", timeout=10).read())
    audit = json.loads(urllib.request.urlopen(url + "/audit", timeout=30).read())

    # NOTE: no state is copied into clients here. Convergence must be achieved
    # by the synchronization protocol during the drain above, or I3 fails.

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    inv = check(audit, state, [c.content_hashes() for c in clients], policy, queue)

    tx_stats = {"dropped": sum(c.tx.stats["dropped"] for c in clients),
                "duplicated": sum(c.tx.stats["duplicated"] for c in clients),
                "reordered": sum(c.tx.stats["reordered"] for c in clients),
                "failed": sum(c.tx.stats["failed"] for c in clients)}
    client_stats = {k: sum(c.stats[k] for c in clients) for k in clients[0].stats}

    audit_metrics = metrics_from_audit(audit)
    return {"policy": policy, "metrics": audit_metrics, "live_counters": metrics,
            "counters_agree": all(audit_metrics[k] == metrics.get(k) for k in audit_metrics),
            "invariants": inv,
            "review_queue_len": len(queue), "audit_records": len(audit),
            "transport": tx_stats, "clients": client_stats, "crashed": crashed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "run"))
    ap.add_argument("--crash-at", type=float, default=None,
                    help="fraction of the run at which to SIGKILL the cloud process")
    a = ap.parse_args()

    cfg = json.load(open(a.config))
    os.makedirs(a.out, exist_ok=True)
    entities = build_entities(cfg["entity_count"], cfg["entity_class_mix"])
    plan = generate_plan(random.Random(cfg["seed"]), cfg, entities)

    print(f"workload: {len(plan['display_events'])} display events, "
          f"{len(plan['cloud_events'])} cloud events, fleet={cfg['fleet_size']}, "
          f"ticks={cfg['ticks']}")
    if a.crash_at:
        print(f"crash injection: SIGKILL cloud at {a.crash_at:.0%} of run")
    print()

    results = []
    for i, policy in enumerate(POLICIES):
        r = run_policy(policy, cfg, plan, entities, 8800 + i, a.out, a.crash_at)
        results.append(r)
        m, inv = r["metrics"], r["invariants"]
        failed = [x["invariant"] for x in inv if not x["passed"]]
        print(f"{policy:24} conflicts={m['conflicts']:4} "
              f"silent={m['silent_overwrites']:4} hi-silent={m['high_risk_silent_overwrites']:3} "
              f"reviews={m['manual_reviews']:4} merges={m['merges']:4} "
              f"dupes={m['duplicates_suppressed']:3} "
              f"inv={'PASS' if not failed else 'FAIL:' + ','.join(failed)}"
              f"{'' if r['counters_agree'] else '  [counters diverged: crash recovery]'}")

    json.dump(results, open(os.path.join(a.out, "results.json"), "w"), indent=2)
    print(f"\nwrote {os.path.join(a.out, 'results.json')}")


if __name__ == "__main__":
    main()
