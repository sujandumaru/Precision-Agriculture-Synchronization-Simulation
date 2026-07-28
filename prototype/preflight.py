#!/usr/bin/env python3
"""Pre-flight check. Run this before committing hours to a sweep.

Every check here exists because something actually went wrong once:

  C1  policy list drift        - sweep ran 7 policies while 10 were defined
  C2  policy raises            - a policy read a ctx key the server never set
  C3  server errors            - the failure above was invisible in the output
  C4  conflict-rate outlier    - the broken policy read 29.6 conflicts vs 1.09
  C5  invariants present       - I1-I5 were never evaluated in the sweep
  C6  invariant has teeth      - I3 passed only because the harness faked it
  C7  determinism              - same seed must give byte-identical rows
  C8  target arithmetic        - the loop chased a target it could never reach
"""
from __future__ import annotations

import csv
import glob
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILURES: list[str] = []
def ok(c, msg):   print(f"  [ PASS ] {c}  {msg}")
def bad(c, msg):  FAILURES.append(f"{c}: {msg}"); print(f"  [ FAIL ] {c}  {msg}")


def main():
    import policies, sweep
    from display_client import DisplayClient
    from invariants import check
    from run_experiment import run_policy
    from workload import build_entities, generate_plan

    print("PRE-FLIGHT\n")

    # C1 -----------------------------------------------------------------
    declared = set(policies.POLICIES)
    swept = set(sweep.SIM_POLICIES) | set(sweep.NEW_POLICIES)
    if declared == swept:
        ok("C1", f"policy lists agree ({len(declared)} policies)")
    else:
        bad("C1", f"declared-not-swept={declared-swept}  swept-not-declared={swept-declared}")

    # C2 -- build the ctx from ONLY the keys the server actually sets, then run
    # every policy against it. If a policy reads a key the server never
    # provides, this raises KeyError here instead of silently zeroing a result.
    import inspect, re as _re
    import cloud_server
    src = inspect.getsource(cloud_server.Store.apply_update)
    server_keys = set(_re.findall(r'"(\w+)":', src.split("ctx = {")[1].split("}")[0]))
    VALUES = {
        "entity_class": "high", "entity_id": "E0001",
        "display_ts": 200, "cloud_ts": 100,
        "display_node": "d0", "cloud_node": "cloud",
        "display_fields": {"name": {"v": "X", "ts": 200, "node": "d0"}},
        "cloud_fields": {"name": {"v": "Y", "ts": 100, "node": "cloud"}},
        "display_vv": {"d0": 2}, "cloud_vv": {"cloud": 1},
    }
    unmodelled = server_keys - set(VALUES)
    if unmodelled:
        bad("C2", f"server sets ctx keys this check cannot model: {unmodelled}")
    ctx = {k: VALUES[k] for k in server_keys if k in VALUES}
    broke = []
    for p in policies.POLICIES:
        for cls in ("high", "medium", "low"):
            c = dict(ctx)
            if "entity_class" in c:
                c["entity_class"] = cls
            try:
                policies.resolve(p, c)
            except Exception as e:                      # noqa: BLE001
                broke.append(f"{p}/{cls}: {type(e).__name__}: {e}")
    if broke:
        bad("C2", "; ".join(broke[:3]))
    else:
        ok("C2", f"all {len(policies.POLICIES)} policies resolve using only the "
                 f"{len(ctx)} keys the server provides")

    # C3/C4/C5 -- live mini sweep over all policies
    out = tempfile.mkdtemp(prefix="preflight_")
    r = subprocess.run([sys.executable, os.path.join(HERE, "sweep.py"),
                        "--cells", "sample:4", "--reps", "1", "--out", out,
                        "--port", "9401", "--new-baselines"],
                       capture_output=True, text=True, timeout=600)
    rows = []
    for f in glob.glob(os.path.join(out, "prototype_runs_s*.csv")):
        rows += list(csv.DictReader(open(f)))
    if not rows:
        bad("C3", f"mini sweep produced no rows. stderr: {r.stderr[-300:]}")
        return report()

    errs = sum(int(x.get("server_errors", 0)) for x in rows)
    if errs: bad("C3", f"{errs} server errors during mini sweep")
    else: ok("C3", f"0 server errors across {len(rows)} runs")

    from collections import defaultdict
    from statistics import median
    conf = defaultdict(list)
    for x in rows: conf[x["policy"]].append(float(x["conflicts"]))
    means = {p: sum(v)/len(v) for p, v in conf.items()}
    med = median(means.values())
    out_of_band = {p: round(m, 3) for p, m in means.items() if med > 0 and (m > 3*med or m < med/3)}
    if out_of_band: bad("C4", f"conflict-rate outliers vs median {med:.3f}: {out_of_band}")
    else: ok("C4", f"all {len(means)} policies within 3x of median conflict rate ({med:.3f})")

    missing_pol = set(policies.POLICIES) - set(means)
    if missing_pol: bad("C4b", f"policies absent from sweep output: {missing_pol}")
    else: ok("C4b", "every declared policy appears in the output")

    invcols = [c for c in rows[0] if c in ("I1", "I2", "I3", "I4", "I5")]
    if len(invcols) != 5: bad("C5", f"invariant columns present: {invcols}")
    else:
        failed = {c: sum(1 for x in rows if x[c] == "0") for c in invcols}
        if any(failed.values()): bad("C5", f"invariant failures in mini sweep: {failed}")
        else: ok("C5", f"I1-I5 evaluated and passing on all {len(rows)} runs")

    # C6 -- I3 must be able to fail
    import display_client
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    cfg.update({"ticks": 120, "fleet_size": 3, "entity_count": 25,
                "updates_per_display_day": 30, "conflict_bias": 0.25, "sync_interval_ticks": 4})
    ents = build_entities(cfg["entity_count"], cfg["entity_class_mix"])
    plan = generate_plan(random.Random(3), cfg, ents)
    orig = display_client.DisplayClient.sync
    verdict = {}
    for pull, port in ((False, 9402), (True, 9403)):
        display_client.DisplayClient.sync = (lambda self, _p=pull, _o=orig: _o(self, pull_only_enabled=_p))
        res = run_policy("domain_aware", cfg, plan, ents, port, os.path.join(out, f"i3_{int(pull)}"))
        verdict[pull] = [i for i in res["invariants"] if i["invariant"].startswith("I3")][0]["passed"]
    display_client.DisplayClient.sync = orig
    if verdict[False] is False and verdict[True] is True:
        ok("C6", "I3 fails without pull-only sync and passes with it")
    else:
        bad("C6", f"I3 has no teeth: push-only={verdict[False]} pull-only={verdict[True]}")

    # C7 -- determinism
    out2 = tempfile.mkdtemp(prefix="preflight2_")
    subprocess.run([sys.executable, os.path.join(HERE, "sweep.py"),
                    "--cells", "sample:2", "--reps", "1", "--out", out2,
                    "--port", "9404", "--new-baselines"],
                   capture_output=True, text=True, timeout=600)
    out3 = tempfile.mkdtemp(prefix="preflight3_")
    subprocess.run([sys.executable, os.path.join(HERE, "sweep.py"),
                    "--cells", "sample:2", "--reps", "1", "--out", out3,
                    "--port", "9405", "--new-baselines"],
                   capture_output=True, text=True, timeout=600)
    def sig(d):
        rs = []
        for f in sorted(glob.glob(os.path.join(d, "prototype_runs_s*.csv"))):
            for x in csv.DictReader(open(f)):
                rs.append((x["cell_index"], x["policy"], x["conflicts"],
                           x["high_risk_silent_overwrites"], x["manual_reviews"]))
        return sorted(rs)
    if sig(out2) and sig(out2) == sig(out3): ok("C7", f"identical results across two runs ({len(sig(out2))} rows)")
    else: bad("C7", "non-deterministic output")

    # C8 -- target arithmetic
    n = 729 * 20 * len(swept)
    ok("C8", f"full sweep target = 729 x 20 x {len(swept)} = {n:,} rows")

    for d in (out, out2, out3): shutil.rmtree(d, ignore_errors=True)
    return report()


def report():
    print()
    if FAILURES:
        print(f"PRE-FLIGHT FAILED ({len(FAILURES)})")
        for f in FAILURES: print("   -", f)
        return 1
    print("PRE-FLIGHT PASSED - safe to start the full sweep")
    return 0


if __name__ == "__main__":
    sys.exit(main())
