#!/usr/bin/env python3
"""Measure agreement between the discrete-event simulation and the prototype.

Validation is not "did the prototype reproduce the numbers exactly" - the two
differ by construction (real HTTP, real batching, real delta convergence). It is
"does the implementation preserve the study's conclusions". This reports both the
per-policy gap and whether the qualitative ordering survives.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from statistics import mean

KEYS = ["fleet_size", "connectivity", "sync_interval_minutes",
        "updates_per_display_day", "conflict_bias", "high_risk_update_share"]
METRICS = ["conflicts", "high_risk_conflicts", "silent_overwrites",
           "high_risk_silent_overwrites", "manual_reviews", "high_risk_manual_reviews"]


def key_of(row: dict) -> tuple:
    return (int(float(row["fleet_size"])), row["connectivity"],
            int(float(row["sync_interval_minutes"])),
            int(float(row["updates_per_display_day"])),
            round(float(row["conflict_bias"]), 4),
            round(float(row["high_risk_update_share"]), 4))


def load(path: str) -> list[dict]:
    """Accept a single CSV or a directory of sharded sweep output."""
    import glob
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "prototype_runs*.csv")))
        if not files:
            raise SystemExit(f"no prototype_runs*.csv found in {path}")
    else:
        files = [path]
    rows = []
    for fp in files:
        with open(fp, newline="") as f:
            rows.extend(csv.DictReader(f))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prototype", default="sweep_out",
                    help="a CSV file, or a directory of sharded sweep output")
    ap.add_argument("--simulation", required=True)
    ap.add_argument("--force", action="store_true",
                    help="report even if the sweep is incomplete (results will be biased)")
    a = ap.parse_args()

    proto = load(a.prototype)
    cells = {key_of(r) for r in proto}
    print(f"prototype runs: {len(proto)}   cells sampled: {len(cells)} of 729\n")

    # -------- coverage guard --------------------------------------------------
    # A partial sweep is not merely a smaller sweep. Cells are generated in
    # factor order, so an interrupted run collapses the outer factors to their
    # first level and silently biases the sample toward one corner of the design.
    LEVELS = {"fleet_size": 3, "connectivity": 3, "sync_interval_minutes": 3,
              "updates_per_display_day": 3, "conflict_bias": 3,
              "high_risk_update_share": 3}
    collapsed = []
    for f, want in LEVELS.items():
        got = len({r[f] for r in proto})
        if got < want:
            collapsed.append(f"{f}: {got}/{want} levels")
    if collapsed or len(cells) < 729:
        print("!" * 78)
        print("INCOMPLETE SWEEP - RESULTS BELOW ARE NOT VALID")
        print("!" * 78)
        print(f"  cells covered      : {len(cells)} of 729 "
              f"({len(cells)/729:.1%})")
        print(f"  runs               : {len(proto)}")
        for c in collapsed:
            print(f"  COLLAPSED FACTOR   : {c}")
        print()
        print("  Cells are enumerated in factor order, so a partial run does not")
        print("  give you a random subset - it gives you one corner of the design.")
        print("  Any agreement figure computed here is measured on that corner and")
        print("  must not be reported. Re-run the sweep to completion (--resume is")
        print("  safe) and check the row count before trusting this output.")
        print("!" * 78)
        print()
        if not a.force:
            raise SystemExit("refusing to report agreement on an incomplete sweep "
                             "(pass --force to override)")

    sim = [r for r in load(a.simulation) if key_of(r) in cells]
    print(f"matched simulation runs on the same cells: {len(sim)}\n")

    def agg(rows):
        out = defaultdict(lambda: defaultdict(list))
        for r in rows:
            for m in METRICS:
                out[r["policy"]][m].append(float(r[m]))
        return {p: {m: mean(v) for m, v in d.items()} for p, d in out.items()}

    P, S = agg(proto), agg(sim)
    policies = [p for p in ["last_write_wins", "cloud_preferred", "display_preferred",
                            "manual_review_all", "domain_aware"] if p in P and p in S]

    print(f"{'policy':20} {'metric':30} {'sim':>8} {'proto':>8} {'diff':>8}")
    print("-" * 78)
    for p in policies:
        for m in ["conflicts", "high_risk_silent_overwrites", "manual_reviews"]:
            s, q = S[p][m], P[p][m]
            print(f"{p:20} {m:30} {s:8.3f} {q:8.3f} {q-s:+8.3f}")
        print()

    # the conclusions the paper actually rests on
    print("=" * 78)
    print("CONCLUSION CHECKS (do the paper's claims survive in the implementation?)")
    print("=" * 78)
    checks = []
    if "domain_aware" in P:
        checks.append(("domain_aware eliminates high-risk silent overwrites",
                       S["domain_aware"]["high_risk_silent_overwrites"] == 0,
                       P["domain_aware"]["high_risk_silent_overwrites"] == 0))
    if "domain_aware" in P and "manual_review_all" in P:
        s_red = 1 - S["domain_aware"]["manual_reviews"] / max(1e-9, S["manual_review_all"]["manual_reviews"])
        p_red = 1 - P["domain_aware"]["manual_reviews"] / max(1e-9, P["manual_review_all"]["manual_reviews"])
        print(f"\nmanual-review reduction vs manual_review_all:")
        print(f"   simulation {s_red:6.1%}      prototype {p_red:6.1%}      gap {abs(p_red-s_red):.1%}")
        checks.append(("domain_aware reduces review burden >50%", s_red > 0.5, p_red > 0.5))
    if "last_write_wins" in P and "domain_aware" in P:
        checks.append(("domain_aware loses fewer high-risk records than LWW",
                       S["domain_aware"]["high_risk_silent_overwrites"] <
                       S["last_write_wins"]["high_risk_silent_overwrites"],
                       P["domain_aware"]["high_risk_silent_overwrites"] <
                       P["last_write_wins"]["high_risk_silent_overwrites"]))
    print()
    for name, s_ok, p_ok in checks:
        verdict = "AGREE" if s_ok == p_ok else "DISAGREE"
        print(f"  [{verdict:8}] {name}")
        print(f"             simulation={s_ok}  prototype={p_ok}")

    by_factor(proto, sim)


FACTORS = ["fleet_size", "sync_interval_minutes", "connectivity",
           "updates_per_display_day", "conflict_bias", "high_risk_update_share"]
CONNECTIVITY_ORDER = ["good", "moderate", "poor"]


def _level_sort(factor):
    if factor == "connectivity":
        return lambda v: CONNECTIVITY_ORDER.index(v)
    return float


def by_factor(proto, sim, policy="last_write_wins"):
    """Prototype-to-simulation conflict-rate ratio, stratified by each factor.

    The two implementations disagree on absolute conflict rates while agreeing
    to 0.4 points on the policy comparison the paper argues. This table is how
    that disagreement is characterised, so it is derived from the archived runs
    rather than quoted from a development sweep.
    """
    def collect(rows):
        out = defaultdict(list)
        for r in rows:
            if r["policy"] != policy:
                continue
            for f in FACTORS:
                out[(f, r[f])].append(float(r["conflicts"]))
        return out

    P, S = collect(proto), collect(sim)
    if not P or not S:
        return

    print("\n" + "=" * 78)
    print(f"CONFLICT-RATE RATIO BY FACTOR ({policy})")
    print("=" * 78)
    print(f"{'factor':26} {'level':10} {'sim':>8} {'proto':>8} {'ratio':>8}")
    print("-" * 78)
    for f in FACTORS:
        levels = sorted({lv for (ff, lv) in P if ff == f}, key=_level_sort(f))
        for lv in levels:
            if (f, lv) not in S:
                continue
            s, p = mean(S[(f, lv)]), mean(P[(f, lv)])
            if s == 0:
                continue
            print(f"{f:26} {lv:10} {s:8.3f} {p:8.3f} {p / s:7.2f}x")
        print()


if __name__ == "__main__":
    main()
