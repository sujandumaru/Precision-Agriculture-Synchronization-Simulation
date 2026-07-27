#!/usr/bin/env python3
"""Cloud-side canonical store, conflict detector, and manual-review queue.

Runs as its own OS process and speaks HTTP over a real socket so that
partitions, duplicate delivery, and process crashes are genuine rather
than simulated in-process.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import AuditLog, content_hash, now_ms, open_db, vv_compare, vv_increment, vv_merge
from policies import resolve

CLOUD_NODE = "cloud"


class Store:
    FAILPOINT = None      # 'before_commit' | 'after_commit'
    FAILPOINT_AT = 0      # trip on the Nth conflict decision

    def __init__(self, db_path: str, audit_path: str, policy: str, fast: bool = False):
        self.db = open_db(db_path, fast=fast)
        self.audit_path = audit_path
        self.audit = AuditLog(audit_path, fsync=not fast)
        self.policy = policy
        self.fast = fast
        self.lock = threading.Lock()
        self._conflict_seq = 0
        self._init_schema()
        self.metrics = {
            "conflicts": 0, "high_risk_conflicts": 0,
            "silent_overwrites": 0, "high_risk_silent_overwrites": 0,
            "manual_reviews": 0, "high_risk_manual_reviews": 0,
            "merges": 0, "contested_field_merges": 0,
            "accepted": 0, "duplicates_suppressed": 0,
            "stale_rejected": 0, "sync_requests": 0, "bytes_in": 0, "bytes_out": 0,
            "server_errors": 0,
        }

    def reset(self, policy: str | None = None):
        """Wipe all state so one server process can serve many sweep cells."""
        with self.lock:
            self.db.executescript(
                "DELETE FROM entities; DELETE FROM review_queue; "
                "DELETE FROM applied_updates; DELETE FROM meta; DELETE FROM audit;")
            self._conflict_seq = 0
            self.db.commit()
            if policy:
                self.policy = policy
            open(self.audit_path, "w").close()
            self.audit = AuditLog(self.audit_path, fsync=not self.fast)
            for k in self.metrics:
                self.metrics[k] = 0

    def _init_schema(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY, cls TEXT NOT NULL,
                fields TEXT NOT NULL, vv TEXT NOT NULL, updated_ms INTEGER NOT NULL,
                rev INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v INTEGER);
            CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL,
                cls TEXT NOT NULL, display_branch TEXT NOT NULL, cloud_branch TEXT NOT NULL,
                created_ms INTEGER NOT NULL, resolved INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS applied_updates (
                update_id TEXT PRIMARY KEY, result TEXT NOT NULL, applied_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL);
            """
        )
        self.db.commit()

    # ------------------------------------------------------------------ helpers
    def get_entity(self, entity_id: str):
        row = self.db.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
        if row is None:
            return None
        return {"entity_id": row["entity_id"], "cls": row["cls"],
                "fields": json.loads(row["fields"]), "vv": json.loads(row["vv"]),
                "updated_ms": row["updated_ms"]}

    def _audit(self, kind: str, payload: dict) -> None:
        """Record a decision inside the current transaction.

        The record and the state change it describes commit atomically. Writing
        the audit entry to a separate log and fsyncing it before the state
        transaction commits leaves a window in which a crash yields a decision
        that was never applied.
        """
        self.db.execute("INSERT INTO audit(ts,kind,payload) VALUES(?,?,?)",
                        (now_ms(), kind, json.dumps(payload, sort_keys=True)))

    def _maybe_fail(self, when: str) -> None:
        if self.FAILPOINT == when and self._conflict_seq == self.FAILPOINT_AT:
            os._exit(70)          # hard exit inside the crash window

    def read_audit(self) -> list[dict]:
        rows = self.db.execute("SELECT seq,ts,kind,payload FROM audit ORDER BY seq").fetchall()
        return [{"seq": r["seq"], "ts": r["ts"], "kind": r["kind"],
                 **json.loads(r["payload"])} for r in rows]

    def next_rev(self) -> int:
        row = self.db.execute("SELECT v FROM meta WHERE k='rev'").fetchone()
        r = (row["v"] if row else 0) + 1
        self.db.execute("INSERT INTO meta(k,v) VALUES('rev',?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (r,))
        return r

    def put_entity(self, entity_id, cls, fields, vv, ts):
        self.db.execute(
            "INSERT INTO entities(entity_id,cls,fields,vv,updated_ms,rev) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(entity_id) DO UPDATE SET cls=excluded.cls, fields=excluded.fields, "
            "vv=excluded.vv, updated_ms=excluded.updated_ms, rev=excluded.rev",
            (entity_id, cls, json.dumps(fields, sort_keys=True), json.dumps(vv, sort_keys=True),
             ts, self.next_rev()))

    def delta(self, since_rev: int) -> dict:
        """Only entities changed after the client's last known revision."""
        rows = self.db.execute("SELECT * FROM entities WHERE rev > ?", (since_rev,)).fetchall()
        return {r["entity_id"]: {"cls": r["cls"], "fields": json.loads(r["fields"]),
                                 "vv": json.loads(r["vv"]), "rev": r["rev"],
                                 "hash": content_hash(json.loads(r["fields"]))} for r in rows}

    def head_rev(self) -> int:
        row = self.db.execute("SELECT v FROM meta WHERE k='rev'").fetchone()
        return row["v"] if row else 0

    # ------------------------------------------------------------------ core
    def apply_update(self, upd: dict) -> dict:
        """Apply one pending display update. Idempotent on update_id."""
        with self.lock:
            uid = upd["update_id"]
            cached = self.db.execute(
                "SELECT result FROM applied_updates WHERE update_id=?", (uid,)).fetchone()
            if cached is not None:
                self.metrics["duplicates_suppressed"] += 1
                self._audit("duplicate_suppressed", {"update_id": uid})
                self.db.commit()
                return json.loads(cached["result"])

            entity_id, cls = upd["entity_id"], upd["entity_class"]
            cur = self.get_entity(entity_id)
            if cur is None:
                cur = {"entity_id": entity_id, "cls": cls, "fields": {},
                       "vv": {}, "updated_ms": 0}

            rel = vv_compare(upd["vv"], cur["vv"])
            result: dict

            if rel in ("dominates", "equal"):
                # causally newer: fast-forward, no conflict
                new_vv = vv_merge(cur["vv"], upd["vv"])
                self.put_entity(entity_id, cls, upd["fields"], new_vv, upd["ts"])
                self.metrics["accepted"] += 1
                result = {"status": "accepted", "conflict": False, "vv": new_vv}
                self._audit("accept_fastforward", {
                    "update_id": uid, "entity_id": entity_id, "cls": cls, "vv": new_vv})

            elif rel == "dominated":
                # cloud already ahead: client is stale, nothing to do
                self.metrics["stale_rejected"] += 1
                result = {"status": "stale", "conflict": False, "vv": cur["vv"]}
                self._audit("stale_update", {
                    "update_id": uid, "entity_id": entity_id, "cls": cls})

            else:  # concurrent -> real conflict
                self.metrics["conflicts"] += 1
                self._conflict_seq += 1
                if cls == "high":
                    self.metrics["high_risk_conflicts"] += 1
                ctx = {
                    "entity_class": cls, "entity_id": entity_id,
                    "display_ts": upd["ts"], "cloud_ts": cur["updated_ms"],
                    "display_node": upd["node_id"], "cloud_node": CLOUD_NODE,
                    "display_fields": upd["fields"], "cloud_fields": cur["fields"],
                    "display_vv": upd["vv"], "cloud_vv": cur["vv"],
                }
                dec = resolve(self.policy, ctx)
                new_vv = vv_merge(cur["vv"], upd["vv"])
                # both branches are always written to the audit log before ack
                branches = {"display": {"fields": upd["fields"], "vv": upd["vv"], "ts": upd["ts"]},
                            "cloud": {"fields": cur["fields"], "vv": cur["vv"], "ts": cur["updated_ms"]}}

                if dec.action == "accept":
                    self.put_entity(entity_id, cls, upd["fields"], new_vv, upd["ts"])
                    self._count_silent(cls)
                    result = {"status": "accepted", "conflict": True, "vv": new_vv}
                elif dec.action == "reject":
                    new_vv = vv_increment(new_vv, CLOUD_NODE)
                    self.put_entity(entity_id, cls, cur["fields"], new_vv, now_ms())
                    self._count_silent(cls)
                    result = {"status": "rejected", "conflict": True, "vv": new_vv,
                              "fields": cur["fields"]}
                elif dec.action == "merge":
                    self.metrics["merges"] += 1
                    contested = dec.diagnostics.get("contested_fields", [])
                    if contested:
                        self.metrics["contested_field_merges"] += 1
                        self._count_silent(cls)
                    new_vv = vv_increment(new_vv, CLOUD_NODE)
                    self.put_entity(entity_id, cls, dec.merged_fields, new_vv, now_ms())
                    result = {"status": "merged", "conflict": True, "vv": new_vv,
                              "fields": dec.merged_fields, "contested": contested}
                else:  # queue
                    self.metrics["manual_reviews"] += 1
                    if cls == "high":
                        self.metrics["high_risk_manual_reviews"] += 1
                    self.db.execute(
                        "INSERT INTO review_queue(entity_id,cls,display_branch,cloud_branch,created_ms) "
                        "VALUES(?,?,?,?,?)",
                        (entity_id, cls, json.dumps(branches["display"], sort_keys=True),
                         json.dumps(branches["cloud"], sort_keys=True), now_ms()))
                    # cloud state is left untouched pending human decision
                    result = {"status": "queued", "conflict": True, "vv": cur["vv"],
                              "fields": cur["fields"]}

                self._audit("conflict_resolved", {
                    "update_id": uid, "entity_id": entity_id, "cls": cls,
                    "policy": self.policy, "action": dec.action,
                    "discarded_branch": dec.discarded_branch,
                    "branches": branches, "note": dec.note})

            self.db.execute(
                "INSERT OR REPLACE INTO applied_updates(update_id,result,applied_ms) VALUES(?,?,?)",
                (uid, json.dumps(result, sort_keys=True), now_ms()))
            self._maybe_fail("before_commit")
            self.db.commit()   # state, audit record and dedup key commit together
            self._maybe_fail("after_commit")
            return result

    def _count_silent(self, cls: str):
        self.metrics["silent_overwrites"] += 1
        if cls == "high":
            self.metrics["high_risk_silent_overwrites"] += 1

    def snapshot(self) -> dict:
        rows = self.db.execute("SELECT * FROM entities").fetchall()
        return {r["entity_id"]: {"cls": r["cls"], "fields": json.loads(r["fields"]),
                                 "vv": json.loads(r["vv"]), "rev": r["rev"],
                                 "hash": content_hash(json.loads(r["fields"]))} for r in rows}


class Handler(BaseHTTPRequestHandler):
    store: Store = None  # type: ignore
    protocol_version = "HTTP/1.1"
    # Without this, small keep-alive request/response pairs hit the classic
    # Nagle / delayed-ACK interaction and every round trip costs ~40 ms.
    disable_nagle_algorithm = True

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.store.metrics["bytes_out"] += len(body)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:                      # noqa: BLE001
            self.store.metrics["server_errors"] += 1
            self._send({"error": f"{type(e).__name__}: {e}"}, 500)

    def _do_GET(self):
        if self.path.startswith("/state"):
            self._send(self.store.snapshot())
        elif self.path.startswith("/metrics"):
            self._send(self.store.metrics)
        elif self.path.startswith("/audit"):
            self._send(self.store.read_audit())
        elif self.path.startswith("/review_queue"):
            rows = self.store.db.execute("SELECT * FROM review_queue").fetchall()
            self._send([dict(r) for r in rows])
        elif self.path.startswith("/health"):
            self._send({"ok": True, "policy": self.store.policy})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        try:
            self._do_POST()
        except Exception as e:                      # noqa: BLE001
            self.store.metrics["server_errors"] += 1
            self._send({"error": f"{type(e).__name__}: {e}"}, 500)

    def _do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        self.store.metrics["bytes_in"] += len(raw)
        req = json.loads(raw or b"{}")
        if self.path.startswith("/sync"):
            self.store.metrics["sync_requests"] += 1
            results = [self.store.apply_update(u) for u in req.get("updates", [])]
            since = int(req.get("since_rev", 0))
            self._send({"results": results, "delta": self.store.delta(since),
                        "head_rev": self.store.head_rev()})
        elif self.path.startswith("/reset"):
            self.store.reset(req.get("policy"))
            self._send({"reset": True, "policy": self.store.policy})
        elif self.path.startswith("/seed"):
            with self.store.lock:
                for e in req["entities"]:
                    self.store.put_entity(e["entity_id"], e["cls"], e["fields"], e["vv"], now_ms())
                self.store.db.commit()
            self._send({"seeded": len(req["entities"])})
        else:
            self._send({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--db", default="run/cloud.db")
    ap.add_argument("--audit", default="run/audit.jsonl")
    ap.add_argument("--policy", default="domain_aware")
    ap.add_argument("--failpoint", choices=["before_commit", "after_commit"], default=None)
    ap.add_argument("--failpoint-at", type=int, default=0)
    ap.add_argument("--fast", action="store_true",
                    help="sweep mode: relax fsync/synchronous for throughput")
    a = ap.parse_args()
    Store.FAILPOINT = a.failpoint
    Store.FAILPOINT_AT = a.failpoint_at
    Handler.store = Store(a.db, a.audit, a.policy, fast=a.fast)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"cloud up port={a.port} policy={a.policy}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
