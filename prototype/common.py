"""Shared primitives: version vectors, entity model, audit log, storage helpers."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any

FIELDS = ("name", "boundary", "guidance_track", "notes", "operator")
ENTITY_CLASSES = ("high", "medium", "low")


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- version vectors
def vv_compare(a: dict[str, int], b: dict[str, int]) -> str:
    """Compare two version vectors.

    Returns one of: 'equal', 'dominates' (a > b), 'dominated' (a < b), 'concurrent'.
    """
    keys = set(a) | set(b)
    a_greater = b_greater = False
    for k in keys:
        av, bv = a.get(k, 0), b.get(k, 0)
        if av > bv:
            a_greater = True
        elif av < bv:
            b_greater = True
    if a_greater and b_greater:
        return "concurrent"
    if a_greater:
        return "dominates"
    if b_greater:
        return "dominated"
    return "equal"


def vv_merge(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {k: max(a.get(k, 0), b.get(k, 0)) for k in set(a) | set(b)}


def vv_increment(vv: dict[str, int], node_id: str) -> dict[str, int]:
    out = dict(vv)
    out[node_id] = out.get(node_id, 0) + 1
    return out


def content_hash(fields: dict[str, Any]) -> str:
    blob = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- audit log
class AuditLog:
    """Append-only JSONL log. Every decision is durable before it is acknowledged."""

    def __init__(self, path: str, fsync: bool = True):
        self.path = path
        self.fsync = fsync
        self._lock = threading.Lock()
        self._seq = 0
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if os.path.exists(path):
            with open(path) as f:
                self._seq = sum(1 for _ in f)

    def append(self, kind: str, payload: dict) -> int:
        with self._lock:
            self._seq += 1
            rec = {"seq": self._seq, "ts": now_ms(), "kind": kind, **payload}
            with open(self.path, "a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
                f.flush()
                if self.fsync:
                    os.fsync(f.fileno())
            return self._seq

    def read(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # torn final write after a crash; stop here
                    break
        return out


# ---------------------------------------------------------------- sqlite
def open_db(path: str, fast: bool = False) -> sqlite3.Connection:
    """Open the store. fast=True relaxes durability for parameter sweeps only.

    Durability claims in the paper rest on the default path plus the crash test,
    never on a sweep run.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=" + ("NORMAL" if fast else "FULL"))
    conn.row_factory = sqlite3.Row
    return conn
