#!/usr/bin/env python3
"""Compare all ten policies on the full design, with paired tests.

Runs are paired: every (cell, replication) carries all ten policies evaluated
on the identical workload realisation, so policies can be compared run by run
rather than only in aggregate.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from collections import defaultdict
from statistics import mean

from policies import POLICIES

# Presentation order for the reported table. This is a reordering of
# policies.POLICIES, never a subset of it: an earlier version of this script
# carried its own hand-maintained list, silently dropped the three additional
# version-vector tiebreak variants, and printed a seven-row table for a
# ten-policy sweep. check_order() below makes that failure mode impossible.
ORDER = ["last_write_wins", "cloud_preferred", "display_preferred",
         "version_vector_causal", "version_vector_cloud_wins",
         "version_vector_display_wins", "version_vector_random",
         "crdt_field_merge", "manual_review_all", "domain_aware"]


def check_order(observed):
    """Refuse to report if ORDER, the declared policies, and the data disagree."""
    declared, order = set(POLICIES), set(ORDER)
    problems = []
    if declared - order:
        problems.append(f"declared but not in report order: {sorted(declared - order)}")
    if order - declared:
        problems.append(f"in report order but not declared: {sorted(order - declared)}")
    if observed - order:
        problems.append(f"present in the data but not reported: {sorted(observed - order)}")
    if problems:
        print("POLICY LIST DRIFT - refusing to report", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        raise SystemExit(2)


def load(path):
    files = sorted(glob.glob(os.path.join(path, "prototype_runs*.csv"))) \
        if os.path.isdir(path) else [path]
    rows = []
    for f in files:
        rows += list(csv.DictReader(open(f)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prototype", required=True)
    a = ap.parse_args()
    rows = load(a.prototype)

    P = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for m in ("high_risk_silent_overwrites", "silent_overwrites", "manual_reviews"):
            P[r["policy"]][m].append(float(r[m]))
    check_order(set(P))
    n = len(P[ORDER[0]]["manual_reviews"])
    print(f"{len(rows)} runs, {n} paired runs per policy\n")
    print(f"{'policy':28} {'hi-risk silent':>14} {'total silent':>13} {'reviews':>9}")
    print("-" * 68)
    for p in ORDER:
        if p in P:
            print(f"{p:28} {mean(P[p]['high_risk_silent_overwrites']):14.4f} "
                  f"{mean(P[p]['silent_overwrites']):13.4f} "
                  f"{mean(P[p]['manual_reviews']):9.4f}")

    byk = defaultdict(dict)
    for r in rows:
        byk[(r["cell_index"], r["replication"])][r["policy"]] = \
            float(r["high_risk_silent_overwrites"])

    def paired(a_, b_):
        d = [byk[k][a_] - byk[k][b_] for k in byk if a_ in byk[k] and b_ in byk[k]]
        return d

    print("\n" + "=" * 64)
    print("PAIRED COMPARISONS - high-risk silent overwrites")
    print("=" * 64)
    for a_, b_ in [("version_vector_causal", "last_write_wins"),
                   ("version_vector_cloud_wins", "last_write_wins"),
                   ("version_vector_display_wins", "last_write_wins"),
                   ("version_vector_random", "last_write_wins"),
                   ("crdt_field_merge", "last_write_wins"),
                   ("domain_aware", "last_write_wins")]:
        d = paired(a_, b_)
        if not d:
            continue
        base = mean([byk[k][b_] for k in byk])
        print(f"\n{a_} vs {b_}")
        print(f"  mean difference    : {mean(d):+.6f}  ({mean(d)/base:+.1%} relative)")
        print(f"  identical outcomes : {sum(1 for x in d if x == 0)/len(d):.1%} of runs")

    print("\n" + "=" * 64)
    print("PROTECTION-BURDEN FRONTIER")
    print("=" * 64)
    mra = mean(P["manual_review_all"]["manual_reviews"])
    zero = [p for p in ORDER if p in P and mean(P[p]["high_risk_silent_overwrites"]) == 0]
    print("policies reaching ZERO high-risk silent overwrites:")
    for p in zero:
        v = mean(P[p]["manual_reviews"])
        print(f"  {p:28} {v:8.4f} reviews/run   reduction {1-v/mra:6.1%}")


if __name__ == "__main__":
    main()
