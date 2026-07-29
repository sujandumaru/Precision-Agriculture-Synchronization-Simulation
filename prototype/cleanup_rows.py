#!/usr/bin/env python3
"""Drop rows for named policies from sweep output so --resume regenerates them."""
from __future__ import annotations

import argparse
import csv
import glob
import os
import shutil


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--drop", nargs="+", required=True)
    a = ap.parse_args()
    drop = set(a.drop)
    total_kept = total_dropped = 0
    for f in sorted(glob.glob(os.path.join(a.out, "prototype_runs_s*.csv"))):
        with open(f, newline="") as fh:
            rd = csv.DictReader(fh)
            fields = rd.fieldnames
            rows = list(rd)
        keep = [r for r in rows if r["policy"] not in drop]
        if len(keep) == len(rows):
            print(f"  {os.path.basename(f)}: nothing to drop ({len(rows)} rows)")
            total_kept += len(rows)
            continue
        shutil.copy(f, f + ".bak")
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(keep)
        print(f"  {os.path.basename(f)}: dropped {len(rows)-len(keep)}, kept {len(keep)}  (backup .bak)")
        total_kept += len(keep); total_dropped += len(rows) - len(keep)
    print(f"\ndropped {total_dropped}, kept {total_kept}")


if __name__ == "__main__":
    main()
