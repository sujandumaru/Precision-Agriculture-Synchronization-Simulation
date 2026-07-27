"""Embedded-display client: local setup store, change log, periodic sync."""
from __future__ import annotations

import uuid

from common import FIELDS, content_hash, now_ms, vv_increment
from transport import FaultyTransport, Partitioned


class DisplayClient:
    def __init__(self, node_id: str, transport: FaultyTransport, clock_skew_ms: int = 0):
        self.node_id = node_id
        self.tx = transport
        self.clock_skew_ms = clock_skew_ms
        self.local: dict[str, dict] = {}     # entity_id -> {cls, fields, vv}
        self.pending: dict[str, dict] = {}   # entity_id -> update payload
        self.acked: set[str] = set()
        self.since_rev = 0
        self.stats = {"local_edits": 0, "superseded": 0, "sync_attempts": 0,
                      "sync_failures": 0, "updates_sent": 0}

    def clock(self) -> int:
        return now_ms() + self.clock_skew_ms

    def seed(self, entities: list[dict]):
        for e in entities:
            self.local[e["entity_id"]] = {"cls": e["cls"], "fields": dict(e["fields"]),
                                          "vv": dict(e["vv"])}

    def edit(self, entity_id: str, cls: str, field: str, value: str):
        """Make a local change and queue it for the next sync."""
        ent = self.local.setdefault(entity_id, {"cls": cls, "fields": {}, "vv": {}})
        ts = self.clock()
        ent["fields"][field] = {"v": value, "ts": ts, "node": self.node_id}
        ent["vv"] = vv_increment(ent["vv"], self.node_id)
        self.stats["local_edits"] += 1

        if entity_id in self.pending:
            self.stats["superseded"] += 1
        # coalesce repeated edits, but keep a stable update_id per pending item
        self.pending[entity_id] = {
            "update_id": self.pending.get(entity_id, {}).get("update_id") or str(uuid.uuid4()),
            "entity_id": entity_id, "entity_class": cls, "node_id": self.node_id,
            "fields": {k: dict(v) for k, v in ent["fields"].items()},
            "vv": dict(ent["vv"]), "ts": ts,
        }

    def sync(self, pull_only_enabled: bool = True) -> dict | None:
        """Synchronize with the cloud.

        A client with nothing pending must still poll for cloud-side changes;
        otherwise convergence is a property of the test harness rather than of
        the protocol. pull_only_enabled=False restores the old push-only
        behaviour and exists solely to demonstrate that I3 can fail.
        """
        if not self.pending and not pull_only_enabled:
            return None
        self.stats["sync_attempts"] += 1
        batch = list(self.pending.values())
        try:
            resp = self.tx.post("/sync", {"node_id": self.node_id, "updates": batch,
                                          "since_rev": self.since_rev})
        except Partitioned:
            self.stats["sync_failures"] += 1
            return None

        self.stats["updates_sent"] += len(batch)
        for upd, res in zip(batch, resp["results"]):
            self.acked.add(upd["update_id"])
            eid = upd["entity_id"]
            if res["status"] in ("rejected", "merged", "queued") and "fields" in res:
                self.local[eid]["fields"] = res["fields"]
            self.local[eid]["vv"] = res["vv"]
            self.pending.pop(eid, None)

        # converge on whatever the cloud changed underneath us since our last rev
        for eid, snap in resp.get("delta", {}).items():
            if eid not in self.pending:
                self.local.setdefault(eid, {"cls": snap["cls"], "fields": {}, "vv": {}})
                self.local[eid]["fields"] = snap["fields"]
                self.local[eid]["vv"] = snap["vv"]
        self.since_rev = resp.get("head_rev", self.since_rev)
        return resp

    def content_hashes(self) -> dict[str, str]:
        return {eid: content_hash(e["fields"]) for eid, e in self.local.items()}
